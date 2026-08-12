from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from passwatcher.local_vault import (
    LocalPasswordService,
    LocalVaultError,
    delete_local_vault,
)
from passwatcher.service import CredentialDraft
from tests.client.local_protector import TestProtector


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "Passwatcher" / "vault.db"


@pytest.fixture
def local_service(vault_path: Path) -> LocalPasswordService:
    return LocalPasswordService(vault_path, TestProtector())


def test_local_crud_round_trip_and_plaintext_absence(
    local_service: LocalPasswordService, vault_path: Path
) -> None:
    """Catches any credential field being stored unprotected in SQLite."""
    created = local_service.create("github.com", "work", "nika", "secret", "private")
    assert local_service.search("WORK") == [created]

    raw = vault_path.read_bytes()
    for value in (b"github.com", b"work", b"nika", b"secret", b"private"):
        assert value not in raw

    updated = local_service.update(created.id, "gitlab.com", "", "nika", "new", "")
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.service == "gitlab.com"
    local_service.delete(created.id)
    assert local_service.list_all() == []


def test_local_search_and_list_match_remote_order(
    local_service: LocalPasswordService,
) -> None:
    """Catches local mode changing lookup semantics or display order."""
    local_service.create("Zulu", "", "b", "one", "")
    local_service.create("alpha", "Work", "a", "two", "")

    assert [item.service for item in local_service.list_all()] == ["alpha", "Zulu"]
    assert [item.service for item in local_service.search("work")] == ["alpha"]


def test_local_import_updates_atomically_and_backs_up_before_mutation(
    local_service: LocalPasswordService, vault_path: Path
) -> None:
    """Catches migration/import writes occurring without an encrypted rollback copy."""
    existing = local_service.create("existing", "", "nika", "old", "")

    summary = local_service.import_many(
        [
            CredentialDraft("existing", "", "nika", "new", "updated"),
            CredentialDraft("github", "", "nika", "other", ""),
        ],
        "update",
    )

    assert (summary.inserted, summary.updated, summary.skipped) == (1, 1, 0)
    assert local_service.search("existing")[0].id == existing.id
    assert local_service.search("existing")[0].password == "new"
    backups = list((vault_path.parent / "backups").glob("passwatcher-local-*-v1.db"))
    assert len(backups) == 1
    assert b"old" not in backups[0].read_bytes()


def test_local_import_duplicate_policies_change_no_unselected_data(
    local_service: LocalPasswordService,
) -> None:
    """Catches local imports silently overwriting under skip or partially writing on error."""
    local_service.create("github", "work", "nika", "old", "")

    skipped = local_service.import_many(
        [CredentialDraft(" GITHUB ", " work ", " NIKA ", "new", "")], "skip"
    )
    assert skipped.skipped == 1
    assert local_service.search("github")[0].password == "old"

    with pytest.raises(LocalVaultError) as raised:
        local_service.import_many(
            [CredentialDraft("github", "work", "nika", "new", "")], "error"
        )
    assert raised.value.code == "duplicate_conflict"
    assert local_service.search("github")[0].password == "old"


def test_local_import_rejects_conflicting_input_before_writing(
    local_service: LocalPasswordService,
) -> None:
    """Catches input order deciding which conflicting password survives."""
    with pytest.raises(LocalVaultError) as raised:
        local_service.import_many(
            [
                CredentialDraft("github", "", "nika", "one", ""),
                CredentialDraft("github", "", "nika", "two", ""),
            ],
            "update",
        )

    assert raised.value.code == "duplicate_conflict"
    assert local_service.list_all() == []


def test_corrupt_record_fails_health_without_leaking_blob(
    local_service: LocalPasswordService, vault_path: Path
) -> None:
    """Catches health accepting a DPAPI blob that cannot be authenticated or decrypted."""
    record = local_service.create("github", "", "nika", "secret", "")
    with sqlite3.connect(vault_path) as connection:
        connection.execute(
            "UPDATE credentials SET protected = ? WHERE id = ?", (b"broken", record.id)
        )
        connection.commit()

    with pytest.raises(LocalVaultError) as raised:
        local_service.health()

    assert raised.value.code == "decrypt_failed"
    assert "broken" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_health_reports_only_non_secret_status(local_service: LocalPasswordService) -> None:
    """Catches diagnostics returning decrypted credential fields."""
    local_service.create("github", "", "nika", "secret", "")

    health = local_service.health()

    assert health == {
        "schema_version": 1,
        "record_count": 1,
        "integrity_check": "ok",
        "protection": "current-user",
    }
    assert "secret" not in repr(health)


def test_delete_local_vault_removes_only_owned_artifacts(vault_path: Path) -> None:
    """Catches local cleanup recursively deleting unrelated user data."""
    service = LocalPasswordService(vault_path, TestProtector())
    service.create("github", "", "nika", "secret", "")
    backup = service.backup()
    unrelated = vault_path.parent / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    unrelated_backup = backup.parent / "keep.db"
    unrelated_backup.write_text("keep", encoding="utf-8")

    summary = delete_local_vault(vault_path)

    assert summary.removed >= 2
    assert not vault_path.exists()
    assert not backup.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert unrelated_backup.read_text(encoding="utf-8") == "keep"
    assert vault_path.parent.exists()
