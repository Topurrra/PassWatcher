from __future__ import annotations

import pytest

from passwatcher.migration import (
    ConflictPolicy,
    MigrationError,
    execute_migration,
    plan_migration,
)
from passwatcher.service import CredentialRecord, ImportSummary


def record(
    credential_id: int,
    service: str,
    *,
    label: str = "",
    username: str = "nika",
    password: str = "secret",
    notes: str = "",
) -> CredentialRecord:
    return CredentialRecord(
        credential_id,
        service,
        label,
        username,
        password,
        notes,
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00Z",
    )


def test_plan_classifies_source_identical_conflict_and_destination_only() -> None:
    """Catches migration dropping source-only data or overwriting without conflict accounting."""
    plan = plan_migration(
        [
            record(1, "only-source", password="a"),
            record(2, "same", password="b"),
            record(3, "conflict", password="source-secret"),
        ],
        [
            record(8, "same", password="b"),
            record(9, "conflict", password="destination-secret"),
            record(10, "only-destination", password="d"),
        ],
    )

    assert (
        plan.source_only,
        plan.identical,
        plan.conflicts,
        plan.destination_only,
    ) == (1, 1, 1, 1)
    assert "source-secret" not in repr(plan)
    assert "destination-secret" not in repr(plan)


def test_plan_rejects_ambiguous_identity_without_values() -> None:
    """Catches duplicate source identities being resolved by input order."""
    with pytest.raises(MigrationError) as raised:
        plan_migration([record(1, "same"), record(2, " SAME ")], [])

    assert raised.value.code == "ambiguous_source"
    assert "same" not in str(raised.value).casefold()


class Destination:
    def __init__(self) -> None:
        self.calls: list[tuple[list[object], str]] = []

    def import_many(self, records: list[object], duplicates: str) -> ImportSummary:
        self.calls.append((records, duplicates))
        updated = 1 if duplicates == "update" and len(records) > 1 else 0
        return ImportSummary(len(records), len(records) - updated, updated, 0)


def _mixed_plan():
    return plan_migration(
        [
            record(1, "only-source", password="a"),
            record(2, "same", password="b"),
            record(3, "conflict", password="source-secret"),
        ],
        [
            record(8, "same", password="b"),
            record(9, "conflict", password="destination-secret"),
        ],
    )


def test_source_wins_uses_one_atomic_update_import() -> None:
    """Catches per-record migration writes or destination-wins behavior under source policy."""
    destination = Destination()

    summary = execute_migration(destination, _mixed_plan(), ConflictPolicy.SOURCE)

    assert len(destination.calls) == 1
    records, policy = destination.calls[0]
    assert len(records) == 2
    assert policy == "update"
    assert summary == ImportSummary(total=3, inserted=1, updated=1, skipped=1)


def test_destination_wins_imports_source_only_and_counts_kept_conflicts() -> None:
    """Catches destination conflict records being overwritten under destination policy."""
    destination = Destination()

    summary = execute_migration(
        destination, _mixed_plan(), ConflictPolicy.DESTINATION
    )

    assert len(destination.calls) == 1
    records, policy = destination.calls[0]
    assert len(records) == 1
    assert policy == "skip"
    assert summary == ImportSummary(total=3, inserted=1, updated=0, skipped=2)


def test_identical_only_migration_performs_no_write() -> None:
    """Catches setup creating backups or transactions when both vaults are already equal."""
    destination = Destination()
    plan = plan_migration([record(1, "same")], [record(2, "same")])

    summary = execute_migration(destination, plan, ConflictPolicy.DESTINATION)

    assert destination.calls == []
    assert summary == ImportSummary(total=1, inserted=0, updated=0, skipped=1)
