import os
import sqlite3
import stat
from pathlib import Path

import pytest

from passwatcher_server.database import DatabaseError, NotFoundError, ValidationError, Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path / "passwatcher.db", tmp_path / "backups")


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
