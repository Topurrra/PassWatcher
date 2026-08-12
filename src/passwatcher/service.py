"""Typed client operations built on the versioned Passwatcher protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from .protocol import ProtocolError, make_request, parse_response


_CREDENTIAL_FIELDS = frozenset(
    {"id", "service", "label", "username", "password", "notes", "created_at", "updated_at"}
)
_INVALID_CREDENTIAL_MESSAGE = "The server returned an invalid credential record"


class RequestTransport(Protocol):
    """The narrow transport interface required by the password service."""

    def request(self, raw: bytes) -> bytes: ...


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    """An immutable credential returned by the Passwatcher server."""

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
    """One credential candidate supplied by a local import parser."""

    service: str
    label: str
    username: str
    password: str
    notes: str


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Non-secret result counts returned by a bulk import."""

    total: int
    inserted: int
    updated: int
    skipped: int


class CredentialService(Protocol):
    """Storage-independent credential operations consumed by the CLI."""

    def search(self, query: str) -> list[CredentialRecord]: ...

    def list_all(self) -> list[CredentialRecord]: ...

    def create(
        self, service: str, label: str, username: str, password: str, notes: str
    ) -> CredentialRecord: ...

    def update(
        self,
        credential_id: int,
        service: str,
        label: str,
        username: str,
        password: str,
        notes: str,
    ) -> CredentialRecord: ...

    def delete(self, credential_id: int) -> None: ...

    def health(self) -> dict[str, object]: ...

    def import_many(
        self, records: list[CredentialDraft], duplicates: str
    ) -> ImportSummary: ...


class PasswordService:
    """Expose typed credential operations over an injected request transport."""

    def __init__(self, transport: RequestTransport) -> None:
        self._transport = transport

    def search(self, query: str) -> list[CredentialRecord]:
        return self._records(self._invoke("search", {"query": query}))

    def list_all(self) -> list[CredentialRecord]:
        return self._records(self._invoke("list", {}))

    def create(
        self, service: str, label: str, username: str, password: str, notes: str
    ) -> CredentialRecord:
        return self._credential(
            self._invoke(
                "create",
                {
                    "service": service,
                    "label": label,
                    "username": username,
                    "password": password,
                    "notes": notes,
                },
            )
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
        return self._credential(
            self._invoke(
                "update",
                {
                    "id": credential_id,
                    "service": service,
                    "label": label,
                    "username": username,
                    "password": password,
                    "notes": notes,
                },
            )
        )

    def delete(self, credential_id: int) -> None:
        result = self._invoke("delete", {"id": credential_id})
        if result is not None:
            raise _malformed_response()

    def health(self) -> dict[str, object]:
        result = self._invoke("health", {})
        if not isinstance(result, dict) or not all(isinstance(key, str) for key in result):
            raise _malformed_response()
        return result

    def import_many(
        self, records: list[CredentialDraft], duplicates: str
    ) -> ImportSummary:
        result = self._invoke(
            "import",
            {
                "records": [asdict(record) for record in records],
                "duplicates": duplicates,
            },
        )
        fields = {"total", "inserted", "updated", "skipped"}
        if not isinstance(result, dict) or set(result) != fields:
            raise _malformed_response()
        if any(type(result[field]) is not int or result[field] < 0 for field in fields):
            raise _malformed_response()
        if result["total"] != result["inserted"] + result["updated"] + result["skipped"]:
            raise _malformed_response()
        return ImportSummary(
            total=result["total"],
            inserted=result["inserted"],
            updated=result["updated"],
            skipped=result["skipped"],
        )

    def _invoke(self, operation: str, payload: dict[str, object]) -> object:
        response = parse_response(self._transport.request(make_request(operation, payload)))
        return response["result"]

    def _records(self, result: object) -> list[CredentialRecord]:
        if not isinstance(result, list):
            raise _malformed_response()
        return [self._credential(value) for value in result]

    def _credential(self, value: object) -> CredentialRecord:
        if not isinstance(value, dict) or set(value) != _CREDENTIAL_FIELDS:
            raise _invalid_credential()
        if type(value["id"]) is not int or any(
            type(value[field]) is not str for field in _CREDENTIAL_FIELDS - {"id"}
        ):
            raise _invalid_credential()
        return CredentialRecord(
            id=value["id"],
            service=value["service"],
            label=value["label"],
            username=value["username"],
            password=value["password"],
            notes=value["notes"],
            created_at=value["created_at"],
            updated_at=value["updated_at"],
        )


def _malformed_response() -> ProtocolError:
    return ProtocolError("malformed_response", "The server returned an invalid response")


def _invalid_credential() -> ProtocolError:
    return ProtocolError("malformed_response", _INVALID_CREDENTIAL_MESSAGE)
