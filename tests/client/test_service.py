import json

import pytest

from passwatcher.protocol import ProtocolError
from passwatcher.service import CredentialRecord, PasswordService


def credential(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": 7,
        "service": "github.com",
        "label": "work",
        "username": "nika",
        "password": "secret",
        "notes": "",
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
    }
    record.update(overrides)
    return record


class FakeTransport:
    def __init__(self) -> None:
        self.result: object = None
        self.request_json: dict[str, object] | None = None

    def request(self, raw: bytes) -> bytes:
        self.request_json = json.loads(raw)
        return json.dumps({"protocol_version": 1, "ok": True, "result": self.result}).encode("utf-8")


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


def test_service_search_maps_records(fake_transport: FakeTransport) -> None:
    """Search returns immutable client records from server credential dictionaries."""
    fake_transport.result = [credential()]

    records = PasswordService(fake_transport).search("github")

    assert records == [CredentialRecord(**credential())]
    assert fake_transport.request_json == {
        "protocol_version": 1,
        "operation": "search",
        "payload": {"query": "github"},
    }
    with pytest.raises(AttributeError):
        records[0].service = "gitlab.com"  # type: ignore[misc]


def test_service_never_places_secret_in_operation_name(fake_transport: FakeTransport) -> None:
    """The password is serialized only in the request payload."""
    fake_transport.result = credential()

    PasswordService(fake_transport).create("github.com", "work", "nika", "a b;secret", "")

    assert fake_transport.request_json == {
        "protocol_version": 1,
        "operation": "create",
        "payload": {
            "service": "github.com",
            "label": "work",
            "username": "nika",
            "password": "a b;secret",
            "notes": "",
        },
    }


def test_service_maps_each_operation_to_its_typed_result(fake_transport: FakeTransport) -> None:
    """Each public operation sends its documented RPC operation and result shape."""
    service = PasswordService(fake_transport)

    fake_transport.result = [credential()]
    assert service.list_all() == [CredentialRecord(**credential())]
    assert fake_transport.request_json == {"protocol_version": 1, "operation": "list", "payload": {}}

    fake_transport.result = credential(id=8)
    assert service.update(8, "gitlab.com", "personal", "nika", "new", "rotated").id == 8
    assert fake_transport.request_json == {
        "protocol_version": 1,
        "operation": "update",
        "payload": {
            "id": 8,
            "service": "gitlab.com",
            "label": "personal",
            "username": "nika",
            "password": "new",
            "notes": "rotated",
        },
    }

    fake_transport.result = None
    assert service.delete(8) is None
    assert fake_transport.request_json == {
        "protocol_version": 1,
        "operation": "delete",
        "payload": {"id": 8},
    }

    fake_transport.result = {"status": "ok"}
    assert service.health() == {"status": "ok"}
    assert fake_transport.request_json == {"protocol_version": 1, "operation": "health", "payload": {}}


@pytest.mark.parametrize("invalid_record", [credential(id=True), credential(username=1), {"id": 7}])
def test_service_rejects_malformed_credential_records(
    fake_transport: FakeTransport, invalid_record: dict[str, object]
) -> None:
    """Bad credential types or missing fields must not reach callers."""
    fake_transport.result = [invalid_record]

    with pytest.raises(ProtocolError) as error:
        PasswordService(fake_transport).search("github")

    assert error.value.code == "malformed_response"
    assert error.value.message == "The server returned an invalid credential record"
