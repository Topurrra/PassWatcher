"""DPAPI-protected SQLite credential service for serverless Windows mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from platformdirs import user_data_dir

from .local_crypto import DataProtector, ProtectionError
from .service import CredentialDraft, CredentialRecord, ImportSummary


SCHEMA_VERSION = 1
DOCUMENT_VERSION = 1
MAX_FIELD_BYTES = 4096
MAX_IMPORT_ROWS = 3000
_BACKUP_NAME = re.compile(r"passwatcher-local-\d{8}T\d{6}\.\d{6}Z-v1\.db")


class LocalVaultError(RuntimeError):
    """A safe validation, protection, filesystem, or SQLite failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class LocalDeleteSummary:
    """Non-secret local cleanup result."""

    removed: int
    retained: int


def default_local_vault_path() -> Path:
    """Return the fixed non-roaming local vault path."""
    return Path(user_data_dir("Passwatcher", appauthor=False, roaming=False)) / "vault.db"


class LocalPasswordService:
    """Implement credential operations over protected local SQLite rows."""

    def __init__(self, path: Path, protector: DataProtector) -> None:
        self.path = Path(path)
        self.backup_dir = self.path.parent / "backups"
        self._protector = protector
        self.initialize()

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS metadata ("
                        "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                    )
                    row = connection.execute(
                        "SELECT value FROM metadata WHERE key = 'schema_version'"
                    ).fetchone()
                    if row is None:
                        connection.execute(
                            "CREATE TABLE credentials ("
                            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                            "protected BLOB NOT NULL, "
                            "created_at TEXT NOT NULL, "
                            "updated_at TEXT NOT NULL)"
                        )
                        connection.execute(
                            "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                            (str(SCHEMA_VERSION),),
                        )
                    elif row["value"] != str(SCHEMA_VERSION):
                        raise LocalVaultError(
                            "unsupported_schema", "The local vault schema is not supported"
                        )
            finally:
                connection.close()
        except LocalVaultError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise LocalVaultError(
                "initialize_failed", "The local vault could not be initialized"
            ) from error

    def create(
        self, service: str, label: str, username: str, password: str, notes: str
    ) -> CredentialRecord:
        fields = self._validate_fields(service, label, username, password, notes)
        protected = self._protect(fields)
        now = self._timestamp()
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "INSERT INTO credentials(protected, created_at, updated_at) VALUES (?, ?, ?)",
                    (protected, now, now),
                )
                credential_id = cursor.lastrowid
        except sqlite3.Error as error:
            raise LocalVaultError(
                "write_failed", "The local vault could not store the credential"
            ) from error
        finally:
            connection.close()
        assert credential_id is not None
        return CredentialRecord(int(credential_id), *fields, now, now)

    def search(self, query: str) -> list[CredentialRecord]:
        query = self._validate_text("query", query, required=False, strip=True).casefold()
        return [
            record
            for record in self.list_all()
            if query in record.service.casefold()
            or query in record.label.casefold()
            or query in record.username.casefold()
        ]

    def list_all(self) -> list[CredentialRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, protected, created_at, updated_at FROM credentials"
            ).fetchall()
        except sqlite3.Error as error:
            raise LocalVaultError("read_failed", "The local vault could not be read") from error
        finally:
            connection.close()
        records = [self._record(row) for row in rows]
        return sorted(
            records,
            key=lambda value: (
                value.service.casefold(),
                value.label.casefold(),
                value.username.casefold(),
                value.id,
            ),
        )

    def update(
        self,
        credential_id: int,
        service: str,
        label: str,
        username: str,
        password: str,
        notes: str,
    ) -> CredentialRecord:
        credential_id = self._validate_id(credential_id)
        fields = self._validate_fields(service, label, username, password, notes)
        protected = self._protect(fields)
        now = self._timestamp()
        connection = self._connect()
        try:
            with connection:
                row = connection.execute(
                    "SELECT created_at FROM credentials WHERE id = ?", (credential_id,)
                ).fetchone()
                if row is None:
                    raise LocalVaultError("not_found", "The credential was not found")
                connection.execute(
                    "UPDATE credentials SET protected = ?, updated_at = ? WHERE id = ?",
                    (protected, now, credential_id),
                )
                created_at = row["created_at"]
        except LocalVaultError:
            raise
        except sqlite3.Error as error:
            raise LocalVaultError(
                "write_failed", "The local vault could not update the credential"
            ) from error
        finally:
            connection.close()
        return CredentialRecord(credential_id, *fields, created_at, now)

    def delete(self, credential_id: int) -> None:
        credential_id = self._validate_id(credential_id)
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM credentials WHERE id = ?", (credential_id,)
                )
                if cursor.rowcount != 1:
                    raise LocalVaultError("not_found", "The credential was not found")
        except LocalVaultError:
            raise
        except sqlite3.Error as error:
            raise LocalVaultError(
                "write_failed", "The local vault could not delete the credential"
            ) from error
        finally:
            connection.close()

    def import_many(
        self, records: list[CredentialDraft], duplicates: str
    ) -> ImportSummary:
        if not 1 <= len(records) <= MAX_IMPORT_ROWS:
            raise LocalVaultError(
                "invalid_import_count",
                f"Import must contain between 1 and {MAX_IMPORT_ROWS} records",
            )
        if duplicates not in {"skip", "update", "error"}:
            raise LocalVaultError(
                "invalid_duplicate_policy", "Duplicate policy must be skip, update, or error"
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
        unique: list[tuple[str, str, str, str, str]] = []
        input_by_identity: dict[tuple[str, str, str], tuple[str, str, str, str, str]] = {}
        skipped = 0
        for fields in normalized:
            identity = self._identity(*fields[:3])
            previous = input_by_identity.get(identity)
            if previous is None:
                input_by_identity[identity] = fields
                unique.append(fields)
            elif previous == fields:
                skipped += 1
            else:
                raise LocalVaultError(
                    "duplicate_conflict", "The import contains a duplicate credential identity"
                )

        existing: dict[tuple[str, str, str], list[CredentialRecord]] = {}
        for record in self.list_all():
            existing.setdefault(
                self._identity(record.service, record.label, record.username), []
            ).append(record)

        inserts: list[tuple[str, str, str, str, str]] = []
        updates: list[tuple[int, tuple[str, str, str, str, str]]] = []
        for fields in unique:
            matches = existing.get(self._identity(*fields[:3]), [])
            if not matches:
                inserts.append(fields)
            elif duplicates == "skip":
                skipped += 1
            elif duplicates == "error" or len(matches) != 1:
                raise LocalVaultError(
                    "duplicate_conflict", "The import contains a duplicate credential identity"
                )
            else:
                updates.append((matches[0].id, fields))

        if not inserts and not updates:
            return ImportSummary(len(records), 0, 0, skipped)

        protected_inserts = [(self._protect(fields), fields) for fields in inserts]
        protected_updates = [
            (credential_id, self._protect(fields), fields)
            for credential_id, fields in updates
        ]
        self.backup()
        now = self._timestamp()
        connection = self._connect()
        try:
            with connection:
                for protected, _fields in protected_inserts:
                    connection.execute(
                        "INSERT INTO credentials(protected, created_at, updated_at) VALUES (?, ?, ?)",
                        (protected, now, now),
                    )
                for credential_id, protected, _fields in protected_updates:
                    connection.execute(
                        "UPDATE credentials SET protected = ?, updated_at = ? WHERE id = ?",
                        (protected, now, credential_id),
                    )
        except sqlite3.Error as error:
            raise LocalVaultError(
                "import_failed", "The local vault could not import credentials"
            ) from error
        finally:
            connection.close()
        return ImportSummary(len(records), len(inserts), len(updates), skipped)

    def backup(self) -> Path:
        self.initialize()
        target = self.backup_dir / (
            f"passwatcher-local-{self._timestamp().replace(':', '').replace('-', '')}-v1.db"
        )
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            source = self._connect()
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            return target
        except (OSError, sqlite3.Error) as error:
            target.unlink(missing_ok=True)
            raise LocalVaultError(
                "backup_failed", "The local vault backup could not be created"
            ) from error

    def health(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            version = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            count = connection.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.Error as error:
            raise LocalVaultError(
                "health_failed", "The local vault health check failed"
            ) from error
        finally:
            connection.close()
        if version is None or version["value"] != str(SCHEMA_VERSION):
            raise LocalVaultError("unsupported_schema", "The local vault schema is not supported")
        self.list_all()
        return {
            "schema_version": SCHEMA_VERSION,
            "record_count": count,
            "integrity_check": integrity,
            "protection": "current-user",
        }

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            return connection
        except sqlite3.Error as error:
            raise LocalVaultError("open_failed", "The local vault could not be opened") from error

    def _record(self, row: sqlite3.Row) -> CredentialRecord:
        fields = self._unprotect(bytes(row["protected"]))
        return CredentialRecord(
            row["id"], *fields, row["created_at"], row["updated_at"]
        )

    def _protect(self, fields: tuple[str, str, str, str, str]) -> bytes:
        document = json.dumps(
            {
                "label": fields[1],
                "notes": fields[4],
                "password": fields[3],
                "service": fields[0],
                "username": fields[2],
                "version": DOCUMENT_VERSION,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        try:
            return self._protector.protect(document)
        except (ProtectionError, OSError, ValueError, TypeError):
            raise LocalVaultError(
                "protect_failed", "The credential could not be protected for local storage"
            ) from None

    def _unprotect(self, protected: bytes) -> tuple[str, str, str, str, str]:
        try:
            raw = self._protector.unprotect(protected)
            document = json.loads(raw.decode("utf-8"))
            expected = {"version", "service", "label", "username", "password", "notes"}
            if not isinstance(document, dict) or set(document) != expected:
                raise ValueError
            if type(document["version"]) is not int or document["version"] != DOCUMENT_VERSION:
                raise ValueError
            return self._validate_fields(
                document["service"],
                document["label"],
                document["username"],
                document["password"],
                document["notes"],
            )
        except (ProtectionError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            raise LocalVaultError(
                "decrypt_failed", "The local vault contains unreadable protected data"
            ) from None

    @staticmethod
    def _identity(service: str, label: str, username: str) -> tuple[str, str, str]:
        return service.strip().casefold(), label.strip().casefold(), username.strip().casefold()

    @classmethod
    def _validate_fields(
        cls, service: str, label: str, username: str, password: str, notes: str
    ) -> tuple[str, str, str, str, str]:
        return (
            cls._validate_text("service", service, required=True, strip=True),
            cls._validate_text("label", label, required=False, strip=True),
            cls._validate_text("username", username, required=True, strip=True),
            cls._validate_text("password", password, required=True, strip=False),
            cls._validate_text("notes", notes, required=False, strip=True),
        )

    @staticmethod
    def _validate_text(field: str, value: str, *, required: bool, strip: bool) -> str:
        if not isinstance(value, str):
            raise LocalVaultError("invalid_field", f"{field} must be text")
        normalized = value.strip() if strip else value
        if required and not normalized:
            raise LocalVaultError("required_field", f"{field} is required")
        if len(normalized.encode("utf-8")) > MAX_FIELD_BYTES:
            raise LocalVaultError(
                "field_too_long", f"{field} must be at most {MAX_FIELD_BYTES} bytes"
            )
        return normalized

    @staticmethod
    def _validate_id(credential_id: int) -> int:
        if type(credential_id) is not int or credential_id < 1:
            raise LocalVaultError("invalid_id", "Credential id must be a positive integer")
        return credential_id

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def delete_local_vault(path: Path) -> LocalDeleteSummary:
    """Delete only exact Passwatcher local-vault artifacts beside *path*."""
    path = Path(path)
    removed = 0
    retained = 0
    owned = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]
    backup_dir = path.parent / "backups"
    if backup_dir.exists():
        for candidate in backup_dir.iterdir():
            if candidate.is_file() and _BACKUP_NAME.fullmatch(candidate.name):
                owned.append(candidate)
            else:
                retained += 1
    try:
        for candidate in owned:
            if candidate.exists():
                candidate.unlink()
                removed += 1
        if backup_dir.exists():
            try:
                backup_dir.rmdir()
            except OSError:
                pass
        try:
            path.parent.rmdir()
        except OSError:
            pass
    except OSError as error:
        raise LocalVaultError(
            "delete_failed", "The local vault could not be completely removed"
        ) from error
    return LocalDeleteSummary(removed, retained)
