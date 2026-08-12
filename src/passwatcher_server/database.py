"""Transactional, permissions-restricted SQLite storage for Passwatcher."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Credential, CredentialDraft, ImportSummary


SCHEMA_VERSION = 1
MAX_FIELD_BYTES = 4096
MAX_IMPORT_ROWS = 3000
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def _chmod(path: Path, mode: int) -> None:
    """Apply POSIX permission modes without impairing Windows storage."""
    if os.name != "nt":
        os.chmod(path, mode)


class ValidationError(ValueError):
    """Raised when a credential field cannot be stored safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(LookupError):
    """Raised when a requested credential does not exist."""


class DatabaseError(RuntimeError):
    """Raised when SQLite or the vault filesystem cannot be used."""


class Vault:
    """A small, single-user SQLite credential vault."""

    def __init__(self, path: Path, backup_dir: Path) -> None:
        self.path = Path(path)
        self.backup_dir = Path(backup_dir)

    def initialize(self) -> None:
        """Create version-one storage, or verify the existing schema."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._restrict_permissions()
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS metadata ("
                        "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                    )
                    version_row = connection.execute(
                        "SELECT value FROM metadata WHERE key = 'schema_version'"
                    ).fetchone()
                    if version_row is None:
                        self._create_version_one_schema(connection)
                        connection.execute(
                            "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                            (str(SCHEMA_VERSION),),
                        )
                    else:
                        self._check_schema_version(connection, version_row["value"])
            finally:
                self._secure_and_close(connection)
        except DatabaseError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise DatabaseError("The vault database could not be initialized") from error

    def create(
        self,
        *,
        service: str,
        label: str,
        username: str,
        password: str,
        notes: str,
    ) -> Credential:
        """Store and return one validated credential."""
        fields = self._validate_fields(service, label, username, password, notes)
        self.initialize()
        now = self._timestamp()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "INSERT INTO credentials("
                    "service, label, username, password, notes, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*fields, now, now),
                )
                row = connection.execute(
                    "SELECT id, service, label, username, password, notes, created_at, updated_at "
                    "FROM credentials WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
        except sqlite3.Error as error:
            raise DatabaseError("The vault database could not store the credential") from error
        finally:
            self._secure_and_close(connection)
        return self._credential_from_row(row)

    def search(self, query: str) -> list[Credential]:
        """Return deterministically ordered partial matches for *query*."""
        query = self._validate_query(query)
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, service, label, username, password, notes, created_at, updated_at "
                "FROM credentials "
                "WHERE instr(lower(service), lower(?)) > 0 "
                "OR instr(lower(coalesce(label, '')), lower(?)) > 0 "
                "OR instr(lower(username), lower(?)) > 0 "
                "ORDER BY lower(service), lower(coalesce(label, '')), lower(username), id",
                (query, query, query),
            ).fetchall()
        except sqlite3.Error as error:
            raise DatabaseError("The vault database could not be searched") from error
        finally:
            self._secure_and_close(connection)
        return [self._credential_from_row(row) for row in rows]

    def list_all(self) -> list[Credential]:
        """Return every credential in deterministic display order."""
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, service, label, username, password, notes, created_at, updated_at "
                "FROM credentials "
                "ORDER BY lower(service), lower(coalesce(label, '')), lower(username), id"
            ).fetchall()
        except sqlite3.Error as error:
            raise DatabaseError("The vault database could not be read") from error
        finally:
            self._secure_and_close(connection)
        return [self._credential_from_row(row) for row in rows]

    def update(
        self,
        credential_id: int,
        *,
        service: str,
        label: str,
        username: str,
        password: str,
        notes: str,
    ) -> Credential:
        """Replace all mutable credential fields and return the updated record."""
        credential_id = self._validate_id(credential_id)
        fields = self._validate_fields(service, label, username, password, notes)
        self.initialize()
        now = self._timestamp()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "UPDATE credentials SET service = ?, label = ?, username = ?, password = ?, "
                    "notes = ?, updated_at = ? WHERE id = ?",
                    (*fields, now, credential_id),
                )
                if cursor.rowcount != 1:
                    raise NotFoundError(f"Credential {credential_id} was not found")
                row = connection.execute(
                    "SELECT id, service, label, username, password, notes, created_at, updated_at "
                    "FROM credentials WHERE id = ?",
                    (credential_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise DatabaseError("The vault database could not update the credential") from error
        finally:
            self._secure_and_close(connection)
        return self._credential_from_row(row)

    def delete(self, credential_id: int) -> None:
        """Delete one credential, requiring that its identifier exists."""
        credential_id = self._validate_id(credential_id)
        self.initialize()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
                if cursor.rowcount != 1:
                    raise NotFoundError(f"Credential {credential_id} was not found")
        except sqlite3.Error as error:
            raise DatabaseError("The vault database could not delete the credential") from error
        finally:
            self._secure_and_close(connection)

    def import_many(
        self, records: list[CredentialDraft], *, duplicates: str
    ) -> ImportSummary:
        """Validate and apply one credential batch in a single transaction."""
        if not 1 <= len(records) <= MAX_IMPORT_ROWS:
            raise ValidationError(
                "invalid_import_count",
                f"Import must contain between 1 and {MAX_IMPORT_ROWS} records",
            )
        if duplicates not in {"skip", "update", "error"}:
            raise ValidationError(
                "invalid_duplicate_policy",
                "Duplicate policy must be skip, update, or error",
            )

        normalized = [
            self._validate_fields(
                record.service,
                record.label,
                record.username,
                record.password,
                record.notes,
            )
            for record in records
        ]
        unique_fields: list[tuple[str, str, str, str, str]] = []
        input_by_identity: dict[tuple[str, str, str], tuple[str, str, str, str, str]] = {}
        skipped = 0
        for fields in normalized:
            identity = self._identity(fields[0], fields[1], fields[2])
            previous = input_by_identity.get(identity)
            if previous is None:
                input_by_identity[identity] = fields
                unique_fields.append(fields)
                continue
            if previous != fields:
                raise ValidationError(
                    "duplicate_conflict",
                    "The import contains a duplicate credential identity",
                )
            skipped += 1

        existing_by_identity: dict[tuple[str, str, str], list[Credential]] = {}
        for record in self.list_all():
            identity = self._identity(record.service, record.label, record.username)
            existing_by_identity.setdefault(identity, []).append(record)

        inserts: list[tuple[str, str, str, str, str]] = []
        updates: list[tuple[int, tuple[str, str, str, str, str]]] = []
        for fields in unique_fields:
            identity = self._identity(fields[0], fields[1], fields[2])
            matches = existing_by_identity.get(identity, [])
            if not matches:
                inserts.append(fields)
                continue
            if duplicates == "skip":
                skipped += 1
                continue
            if duplicates == "error" or len(matches) != 1:
                raise ValidationError(
                    "duplicate_conflict",
                    "The import contains a duplicate credential identity",
                )
            updates.append((matches[0].id, fields))

        if not inserts and not updates:
            return ImportSummary(
                total=len(records),
                inserted=0,
                updated=0,
                skipped=skipped,
            )

        self.backup()
        now = self._timestamp()
        connection = self._connect()
        try:
            with connection:
                for fields in inserts:
                    connection.execute(
                        "INSERT INTO credentials("
                        "service, label, username, password, notes, created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (*fields, now, now),
                    )
                for credential_id, fields in updates:
                    connection.execute(
                        "UPDATE credentials SET password = ?, notes = ?, updated_at = ? "
                        "WHERE id = ?",
                        (fields[3], fields[4], now, credential_id),
                    )
        except sqlite3.Error as error:
            raise DatabaseError("The vault database could not import credentials") from error
        finally:
            self._secure_and_close(connection)

        return ImportSummary(
            total=len(records),
            inserted=len(inserts),
            updated=len(updates),
            skipped=skipped,
        )

    def health(self) -> dict[str, Any]:
        """Return non-secret vault diagnostics for setup and doctor commands."""
        self.initialize()
        connection = self._connect()
        try:
            version = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()["value"]
            record_count = connection.execute("SELECT COUNT(*) AS count FROM credentials").fetchone()[
                "count"
            ]
            integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.Error as error:
            raise DatabaseError("The vault database health check failed") from error
        finally:
            self._secure_and_close(connection)
        return {
            "schema_version": int(version),
            "record_count": record_count,
            "integrity_check": integrity_check,
            "permissions_ok": self._permissions_ok(),
        }

    def backup(self) -> Path:
        """Create and return one owner-only online backup of the current vault."""
        self.initialize()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                raise DatabaseError("The vault schema version is invalid")
            try:
                version = int(row["value"])
            except (TypeError, ValueError) as error:
                raise DatabaseError("The vault schema version is invalid") from error
            return self._backup_before_migration(connection, version)
        except sqlite3.Error as error:
            raise DatabaseError("The vault backup could not be created") from error
        finally:
            self._secure_and_close(connection)

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            self._restrict_permissions()
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            self._restrict_permissions()
            return connection
        except (OSError, sqlite3.Error) as error:
            raise DatabaseError("The vault database could not be opened") from error

    def _create_version_one_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS credentials ("
            "id INTEGER PRIMARY KEY, "
            "service TEXT NOT NULL, "
            "label TEXT NOT NULL, "
            "username TEXT NOT NULL, "
            "password TEXT NOT NULL, "
            "notes TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL"
            ")"
        )

    def _check_schema_version(self, connection: sqlite3.Connection, raw_version: str) -> None:
        try:
            version = int(raw_version)
        except (TypeError, ValueError) as error:
            raise DatabaseError("The vault schema version is invalid") from error
        if version > SCHEMA_VERSION:
            raise DatabaseError("The vault schema is newer than this server")
        if version < SCHEMA_VERSION:
            self._backup_before_migration(connection, version)
            raise DatabaseError("This server cannot migrate the vault schema")

    def _backup_before_migration(self, source: sqlite3.Connection, old_version: int) -> Path:
        """Make a locked-down backup before a future migration is attempted."""
        timestamp = self._timestamp().replace(":", "").replace("-", "")
        target = self.backup_dir / f"passwatcher-{timestamp}-v{old_version}.db"
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            _chmod(self.backup_dir, PRIVATE_DIRECTORY_MODE)
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
            _chmod(target, PRIVATE_FILE_MODE)
        except (OSError, sqlite3.Error) as error:
            raise DatabaseError("The vault backup could not be created") from error
        return target

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _credential_from_row(row: sqlite3.Row | None) -> Credential:
        if row is None:
            raise DatabaseError("The vault returned an incomplete credential record")
        return Credential(
            id=row["id"],
            service=row["service"],
            label=row["label"],
            username=row["username"],
            password=row["password"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _validate_id(credential_id: int) -> int:
        if isinstance(credential_id, bool) or not isinstance(credential_id, int) or credential_id < 1:
            raise ValidationError("invalid_id", "Credential id must be a positive integer")
        return credential_id

    @staticmethod
    def _identity(service: str, label: str, username: str) -> tuple[str, str, str]:
        return service.strip().casefold(), label.strip().casefold(), username.strip().casefold()

    @classmethod
    def _validate_fields(
        cls, service: str, label: str, username: str, password: str, notes: str
    ) -> tuple[str, str, str, str, str]:
        normalized_service = cls._validate_text("service", service, required=True, strip=True)
        normalized_label = cls._validate_text("label", label, required=False, strip=True)
        normalized_username = cls._validate_text("username", username, required=True, strip=True)
        normalized_password = cls._validate_text("password", password, required=True, strip=False)
        normalized_notes = cls._validate_text("notes", notes, required=False, strip=True)
        return (
            normalized_service,
            normalized_label,
            normalized_username,
            normalized_password,
            normalized_notes,
        )

    @classmethod
    def _validate_query(cls, query: str) -> str:
        return cls._validate_text("query", query, required=False, strip=True)

    @staticmethod
    def _validate_text(field: str, value: str, *, required: bool, strip: bool) -> str:
        if not isinstance(value, str):
            raise ValidationError("invalid_field", f"{field} must be text")
        normalized = value.strip() if strip else value
        if required and not normalized:
            raise ValidationError("required_field", f"{field} is required")
        if len(normalized.encode("utf-8")) > MAX_FIELD_BYTES:
            raise ValidationError("field_too_long", f"{field} must be at most {MAX_FIELD_BYTES} bytes")
        return normalized

    def _restrict_permissions(self) -> None:
        try:
            _chmod(self.path.parent, PRIVATE_DIRECTORY_MODE)
            if self.backup_dir.exists():
                _chmod(self.backup_dir, PRIVATE_DIRECTORY_MODE)
            sensitive_files = [
                self.path,
                self.path.with_name(f"{self.path.name}-wal"),
                self.path.with_name(f"{self.path.name}-shm"),
            ]
            if self.backup_dir.exists():
                sensitive_files.extend(self.backup_dir.glob("passwatcher-*.db"))
            for path in sensitive_files:
                if path.exists():
                    _chmod(path, PRIVATE_FILE_MODE)
        except OSError as error:
            raise DatabaseError("The vault storage permissions could not be secured") from error

    def _secure_and_close(self, connection: sqlite3.Connection) -> None:
        try:
            self._restrict_permissions()
        finally:
            connection.close()

    def _permissions_ok(self) -> bool:
        if os.name == "nt":
            return True
        try:
            directories = [self.path.parent]
            if self.backup_dir.exists():
                directories.append(self.backup_dir)
            files = [
                self.path,
                self.path.with_name(f"{self.path.name}-wal"),
                self.path.with_name(f"{self.path.name}-shm"),
            ]
            if self.backup_dir.exists():
                files.extend(self.backup_dir.glob("passwatcher-*.db"))
            return all(
                (path.stat().st_mode & 0o777) == PRIVATE_DIRECTORY_MODE
                for path in directories
            ) and all(
                (path.stat().st_mode & 0o777) == PRIVATE_FILE_MODE
                for path in files
                if path.exists()
            )
        except OSError:
            return False
