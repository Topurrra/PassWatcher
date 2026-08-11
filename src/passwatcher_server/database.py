"""Transactional, permissions-restricted SQLite storage for Passwatcher."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Credential


SCHEMA_VERSION = 1
MAX_FIELD_BYTES = 4096


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
                connection.close()
            self._restrict_permissions()
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
            connection.close()
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
            connection.close()
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
            connection.close()
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
            connection.close()
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
            connection.close()

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
            connection.close()
        return {
            "schema_version": int(version),
            "record_count": record_count,
            "integrity_check": integrity_check,
            "permissions_ok": self._permissions_ok(),
        }

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            self._restrict_permissions()
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
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
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
            os.chmod(target, 0o600)
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
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise DatabaseError("The vault database permissions could not be secured") from error

    def _permissions_ok(self) -> bool:
        if os.name == "nt":
            return True
        try:
            return (self.path.stat().st_mode & 0o777) == 0o600
        except OSError:
            return False
