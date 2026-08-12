"""Non-secret planning and one-batch execution for vault switches."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Sequence
from typing import Protocol

from .service import CredentialDraft, CredentialRecord, ImportSummary


class MigrationError(RuntimeError):
    """A migration failure whose message never identifies a credential."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ConflictPolicy(str, Enum):
    """One decision applied to every conflicting migration identity."""

    SOURCE = "source"
    DESTINATION = "destination"


class ImportDestination(Protocol):
    def import_many(
        self, records: list[CredentialDraft], duplicates: str
    ) -> ImportSummary: ...


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """Public counts plus private source drafts needed for execution."""

    total_source: int
    source_only: int
    identical: int
    conflicts: int
    destination_only: int
    _source_only_records: tuple[CredentialDraft, ...] = field(repr=False)
    _conflict_records: tuple[CredentialDraft, ...] = field(repr=False)


def plan_migration(
    source: Sequence[CredentialRecord], destination: Sequence[CredentialRecord]
) -> MigrationPlan:
    """Classify both vaults without selecting or exposing credential values."""
    source_by_identity = _unique_records(source, "ambiguous_source")
    destination_by_identity = _unique_records(destination, "ambiguous_destination")
    source_only: list[CredentialDraft] = []
    conflicts: list[CredentialDraft] = []
    identical = 0

    for identity, source_record in source_by_identity.items():
        destination_record = destination_by_identity.get(identity)
        draft = _draft(source_record)
        if destination_record is None:
            source_only.append(draft)
        elif _fields(source_record) == _fields(destination_record):
            identical += 1
        else:
            conflicts.append(draft)

    destination_only = len(set(destination_by_identity) - set(source_by_identity))
    return MigrationPlan(
        total_source=len(source),
        source_only=len(source_only),
        identical=identical,
        conflicts=len(conflicts),
        destination_only=destination_only,
        _source_only_records=tuple(source_only),
        _conflict_records=tuple(conflicts),
    )


def execute_migration(
    destination: ImportDestination,
    plan: MigrationPlan,
    conflicts: ConflictPolicy,
) -> ImportSummary:
    """Apply the plan through at most one destination bulk-import call."""
    if not isinstance(conflicts, ConflictPolicy):
        raise MigrationError("invalid_policy", "Migration conflict policy is invalid")

    if conflicts is ConflictPolicy.SOURCE:
        records = [*plan._source_only_records, *plan._conflict_records]
        duplicate_policy = "update"
        policy_skipped = 0
    else:
        records = list(plan._source_only_records)
        duplicate_policy = "skip"
        policy_skipped = plan.conflicts

    if records:
        result = destination.import_many(records, duplicate_policy)
    else:
        result = ImportSummary(0, 0, 0, 0)
    return ImportSummary(
        total=plan.total_source,
        inserted=result.inserted,
        updated=result.updated,
        skipped=result.skipped + plan.identical + policy_skipped,
    )


def _unique_records(
    records: Sequence[CredentialRecord], error_code: str
) -> dict[tuple[str, str, str], CredentialRecord]:
    values: dict[tuple[str, str, str], CredentialRecord] = {}
    for record in records:
        identity = (
            record.service.strip().casefold(),
            record.label.strip().casefold(),
            record.username.strip().casefold(),
        )
        if identity in values:
            raise MigrationError(
                error_code, "A vault contains an ambiguous credential identity"
            )
        values[identity] = record
    return values


def _fields(record: CredentialRecord) -> tuple[str, str, str, str, str]:
    return record.service, record.label, record.username, record.password, record.notes


def _draft(record: CredentialRecord) -> CredentialDraft:
    return CredentialDraft(*_fields(record))
