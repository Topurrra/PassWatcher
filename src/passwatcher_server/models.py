"""Value objects used by the Passwatcher server."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Credential:
    """One credential record stored in the vault."""

    id: int
    service: str
    label: str
    username: str
    password: str
    notes: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CredentialDraft:
    """One validated credential candidate supplied by a bulk import."""

    service: str
    label: str
    username: str
    password: str
    notes: str


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Non-secret outcome counts for one bulk import."""

    total: int
    inserted: int
    updated: int
    skipped: int
