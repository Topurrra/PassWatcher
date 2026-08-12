import os
import sqlite3
import stat
from pathlib import Path

import pytest

import passwatcher_server.database as database_module
import passwatcher_server.models as models
from passwatcher_server.database import DatabaseError, NotFoundError, ValidationError, Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path / "passwatcher.db", tmp_path / "backups")


def draft(**overrides: str):
    values = {
        "service": "github.com",
        "label": "work",
        "username": "nika",
        "password": "secret",
        "notes": "",
    }
    values.update(overrides)
    return models.CredentialDraft(**values)


def test_import_many_inserts_all_records_in_one_result(vault: Vault) -> None:
    """Catches a batch import dropping a valid row or reporting wrong counts."""
    summary = vault.import_many(
        [draft(), draft(service="gitlab.com", password="other")],
        duplicates="skip",
    )

    assert summary.total == 2
    assert summary.inserted == 2
    assert summary.updated == 0
    assert summary.skipped == 0
    assert len(vault.list_all()) == 2


def test_import_many_updates_matching_identity(vault: Vault) -> None:
    """Catches update imports replacing IDs, creation times, or identity fields."""
    original = vault.create(
        service="GitHub.com", label="Work", username="Nika", password="old", notes="old"
    )

    summary = vault.import_many(
        [draft(password="new", notes="new")],
        duplicates="update",
    )

    updated = vault.list_all()[0]
    assert summary.updated == 1
    assert updated.id == original.id
    assert updated.created_at == original.created_at
    assert (updated.service, updated.label, updated.username) == (
        "GitHub.com",
        "Work",
        "Nika",
    )
    assert (updated.password, updated.notes) == ("new", "new")


def test_import_many_error_policy_changes_nothing(vault: Vault) -> None:
    """Catches duplicate-error imports mutating a record before rejecting it."""
    vault.create(
        service="github.com", label="work", username="nika", password="old", notes=""
    )

    with pytest.raises(ValidationError) as raised:
        vault.import_many([draft(password="new")], duplicates="error")

    assert raised.value.code == "duplicate_conflict"
    assert vault.list_all()[0].password == "old"


def test_import_many_backs_up_before_mutation(vault: Vault) -> None:
    """Catches bulk writes running without a recoverable pre-import snapshot."""
    vault.create(
        service="existing.test", label="", username="old", password="old", notes=""
    )

    vault.import_many([draft()], duplicates="skip")

    backups = list(vault.backup_dir.glob("passwatcher-*-v1.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT service FROM credentials").fetchall() == [
            ("existing.test",)
        ]


def test_import_many_backup_failure_prevents_mutation(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches import continuing after its recovery backup fails."""
    monkeypatch.setattr(
        vault,
        "backup",
        lambda: (_ for _ in ()).throw(DatabaseError("failed")),
    )

    with pytest.raises(DatabaseError):
        vault.import_many([draft()], duplicates="skip")

    assert vault.list_all() == []


def test_import_many_rejects_conflicting_input_identity(vault: Vault) -> None:
    """Catches conflicting CSV rows being inserted in input order."""
    with pytest.raises(ValidationError) as raised:
        vault.import_many(
            [draft(password="one"), draft(password="two")],
            duplicates="skip",
        )

    assert raised.value.code == "duplicate_conflict"
    assert vault.list_all() == []


def test_import_many_collapses_identical_input_duplicates(vault: Vault) -> None:
    """Catches identical repeated rows creating repeated vault records."""
    summary = vault.import_many([draft(), draft()], duplicates="skip")

    assert summary == models.ImportSummary(total=2, inserted=1, updated=0, skipped=1)
    assert len(vault.list_all()) == 1


def test_import_many_all_skipped_creates_no_backup(vault: Vault) -> None:
    """Catches a no-op import producing unnecessary plaintext backups."""
    vault.create(
        service="github.com", label="work", username="nika", password="old", notes=""
    )

    summary = vault.import_many([draft()], duplicates="skip")

    assert summary == models.ImportSummary(total=1, inserted=0, updated=0, skipped=1)
    assert list(vault.backup_dir.glob("passwatcher-*.db")) == []


def test_import_many_handles_ambiguous_existing_identity_safely(vault: Vault) -> None:
    """Catches update mode guessing between duplicate existing records."""
    for password in ("one", "two"):
        vault.create(
            service="github.com",
            label="work",
            username="nika",
            password=password,
            notes="",
        )

    skipped = vault.import_many([draft()], duplicates="skip")
    assert skipped.skipped == 1

    for policy in ("update", "error"):
        with pytest.raises(ValidationError) as raised:
            vault.import_many([draft()], duplicates=policy)
        assert raised.value.code == "duplicate_conflict"
    assert [item.password for item in vault.list_all()] == ["one", "two"]


def test_import_many_rejects_invalid_policy(vault: Vault) -> None:
    """Catches misspelled duplicate policies silently acting like update."""
    with pytest.raises(ValidationError) as raised:
        vault.import_many([draft()], duplicates="replace")

    assert raised.value.code == "invalid_duplicate_policy"


@pytest.mark.parametrize("count", [0, 3001])
def test_import_many_enforces_batch_bounds(vault: Vault, count: int) -> None:
    """Catches empty or unbounded batches reaching SQLite."""
    with pytest.raises(ValidationError) as raised:
        vault.import_many([draft()] * count, duplicates="skip")

    assert raised.value.code == "invalid_import_count"


def test_import_many_validates_every_field_before_writing(vault: Vault) -> None:
    """Catches an invalid later row leaving an earlier valid row behind."""
    with pytest.raises(ValidationError) as raised:
        vault.import_many([draft(), draft(service="")], duplicates="skip")

    assert raised.value.code == "required_field"
    assert vault.list_all() == []


def test_import_many_rolls_back_prior_insert_on_sql_failure(vault: Vault) -> None:
    """Catches a database error committing the successful prefix of a batch."""
    vault.initialize()
    with sqlite3.connect(vault.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_gitlab BEFORE INSERT ON credentials "
            "WHEN NEW.service = 'gitlab.com' BEGIN "
            "SELECT RAISE(ABORT, 'rejected'); END"
        )

    with pytest.raises(DatabaseError):
        vault.import_many(
            [draft(), draft(service="gitlab.com", username="other")],
            duplicates="skip",
        )

    assert vault.list_all() == []


def test_initialize_is_idempotent_and_create_round_trips(vault: Vault) -> None:
    vault.initialize()
    vault.initialize()

    created = vault.create(
        service="github.com",
        label="personal",
        username="nika@example.com",
        password="s3cret",
        notes="main",
    )

    assert created.id == 1
    assert vault.search("github") == [created]


def test_search_is_case_insensitive_across_required_fields(vault: Vault) -> None:
    vault.initialize()
    vault.create(
        service="github.com",
        label="Work",
        username="nika@company.test",
        password="one",
        notes="",
    )
    vault.create(
        service="gitlab.com",
        label="Personal",
        username="nika@example.test",
        password="two",
        notes="",
    )

    assert [item.service for item in vault.search("WORK")] == ["github.com"]
    assert len(vault.search("NIKA")) == 2


def test_list_all_orders_records_and_normalizes_nonsecret_fields(vault: Vault) -> None:
    vault.create(
        service=" zebra.example ",
        label=" A ",
        username=" person ",
        password=" secret ",
        notes=" note ",
    )
    vault.create(
        service="alpha.example",
        label="",
        username="admin",
        password="password",
        notes="",
    )

    records = vault.list_all()

    assert [record.service for record in records] == ["alpha.example", "zebra.example"]
    assert records[1].label == "A"
    assert records[1].username == "person"
    assert records[1].password == " secret "
    assert records[1].notes == "note"
    assert records[1].created_at.endswith("Z")


@pytest.mark.parametrize(
    ("field", "value"),
    [("service", " \t"), ("username", "\n"), ("password", "")],
)
def test_create_rejects_empty_required_fields(vault: Vault, field: str, value: str) -> None:
    fields = {
        "service": "github.com",
        "label": "personal",
        "username": "nika",
        "password": "secret",
        "notes": "",
    }
    fields[field] = value

    with pytest.raises(ValidationError) as raised:
        vault.create(**fields)

    assert raised.value.code == "required_field"


def test_create_enforces_utf8_byte_limit(vault: Vault) -> None:
    accepted = vault.create(
        service="github.com",
        label="é" * 2048,
        username="nika",
        password="secret",
        notes="",
    )

    with pytest.raises(ValidationError) as raised:
        vault.create(
            service="gitlab.com",
            label="é" * 2049,
            username="nika",
            password="secret",
            notes="",
        )

    assert len(accepted.label.encode("utf-8")) == 4096
    assert raised.value.code == "field_too_long"


def test_vault_connections_enable_required_sqlite_pragmas(vault: Vault) -> None:
    connection = vault._connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        connection.close()


def test_failed_update_rolls_back(vault: Vault) -> None:
    original = vault.create(
        service="github.com", label="", username="nika", password="old", notes=""
    )

    with pytest.raises(ValidationError):
        vault.update(
            original.id,
            service="",
            label="",
            username="nika",
            password="new",
            notes="",
        )

    assert vault.search("github")[0].password == "old"


def test_update_replaces_all_mutable_fields(vault: Vault) -> None:
    original = vault.create(
        service="github.com", label="personal", username="nika", password="old", notes="old note"
    )

    updated = vault.update(
        original.id,
        service="gitlab.com",
        label="work",
        username="nika@company.test",
        password="new",
        notes="new note",
    )

    assert updated.service == "gitlab.com"
    assert updated.label == "work"
    assert updated.username == "nika@company.test"
    assert updated.password == "new"
    assert updated.notes == "new note"
    assert updated.created_at == original.created_at
    assert updated.updated_at.endswith("Z")


def test_delete_requires_existing_id(vault: Vault) -> None:
    with pytest.raises(NotFoundError):
        vault.delete(999)


def test_delete_removes_existing_credential(vault: Vault) -> None:
    credential = vault.create(
        service="github.com", label="", username="nika", password="secret", notes=""
    )

    vault.delete(credential.id)

    assert vault.list_all() == []


def test_health_reports_safe_vault_status(vault: Vault) -> None:
    vault.create(
        service="github.com", label="", username="nika", password="secret", notes="private"
    )

    health = vault.health()

    assert health == {
        "schema_version": 1,
        "record_count": 1,
        "integrity_check": "ok",
        "permissions_ok": True,
    }
    assert "secret" not in repr(health)
    assert "private" not in repr(health)


def test_older_schema_creates_locked_down_backup_before_failing_migration(vault: Vault) -> None:
    vault.create(
        service="github.com", label="", username="nika", password="secret", notes="private"
    )
    with sqlite3.connect(vault.path) as connection:
        connection.execute("UPDATE metadata SET value = '0' WHERE key = 'schema_version'")

    with pytest.raises(DatabaseError):
        vault.initialize()

    backups = list(vault.backup_dir.glob("passwatcher-*-v0.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone() == ("0",)
        assert connection.execute("SELECT service FROM credentials").fetchone() == ("github.com",)
    if os.name != "nt":
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertion")
def test_database_is_owner_read_write_only(vault: Vault) -> None:
    vault.initialize()

    assert stat.S_IMODE(vault.path.stat().st_mode) == 0o600


def test_initialize_requests_private_vault_directory_permissions(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    chmod_calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        database_module,
        "_chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
        raising=False,
    )

    vault.initialize()

    assert (vault.path.parent, 0o700) in chmod_calls


def test_permission_helper_targets_existing_database_sidecars(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_files = [
        vault.path,
        vault.path.with_name(f"{vault.path.name}-wal"),
        vault.path.with_name(f"{vault.path.name}-shm"),
    ]
    for path in database_files:
        path.touch()
    chmod_calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        database_module,
        "_chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
        raising=False,
    )

    vault._restrict_permissions()

    assert {(path, mode) for path, mode in chmod_calls if mode == 0o600} == {
        (path, 0o600) for path in database_files
    }


def test_migration_backup_requests_private_directory_and_file_permissions(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault.initialize()
    with sqlite3.connect(vault.path) as connection:
        connection.execute("UPDATE metadata SET value = '0' WHERE key = 'schema_version'")
    chmod_calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        database_module,
        "_chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
        raising=False,
    )

    with pytest.raises(DatabaseError):
        vault.initialize()

    backups = list(vault.backup_dir.glob("passwatcher-*-v0.db"))
    assert len(backups) == 1
    assert (vault.backup_dir, 0o700) in chmod_calls
    assert (backups[0], 0o600) in chmod_calls


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertion")
def test_vault_directory_is_owner_only(vault: Vault) -> None:
    os.chmod(vault.path.parent, 0o755)

    vault.initialize()

    assert stat.S_IMODE(vault.path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertion")
def test_migration_backup_directory_is_owner_only(vault: Vault) -> None:
    vault.initialize()
    with sqlite3.connect(vault.path) as connection:
        connection.execute("UPDATE metadata SET value = '0' WHERE key = 'schema_version'")

    with pytest.raises(DatabaseError):
        vault.initialize()

    assert stat.S_IMODE(vault.backup_dir.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertion")
def test_live_database_files_are_owner_read_write_only(vault: Vault) -> None:
    vault.initialize()
    held_connection = vault._connect()
    try:
        vault.create(
            service="github.com", label="", username="nika", password="secret", notes=""
        )
        database_files = [
            vault.path,
            vault.path.with_name(f"{vault.path.name}-wal"),
            vault.path.with_name(f"{vault.path.name}-shm"),
        ]
        existing_files = [path for path in database_files if path.exists()]

        assert {path.name for path in existing_files} == {path.name for path in database_files}
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in existing_files)
    finally:
        held_connection.close()
