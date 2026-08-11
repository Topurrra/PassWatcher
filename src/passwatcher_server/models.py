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
