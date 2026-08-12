import json
from pathlib import Path

import pytest

from passwatcher_server import Vault
from passwatcher_server.rpc import MAX_REQUEST_BYTES, handle_request


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path / "passwatcher.db", tmp_path / "backups")


def invoke(
    vault: Vault,
    operation: str,
    payload: dict[str, object],
    *,
    version: int = 1,
) -> dict[str, object]:
    raw = json.dumps(
        {"protocol_version": version, "operation": operation, "payload": payload},
        separators=(",", ":"),
    ).encode("utf-8")
    return json.loads(handle_request(raw, vault))


def assert_error(response: dict[str, object], code: str, *, version: int = 1) -> None:
    assert response["protocol_version"] == version
    assert response["ok"] is False
    assert response["error"]["code"] == code
    assert isinstance(response["error"]["message"], str)


def test_rpc_search_returns_versioned_result(vault: Vault) -> None:
    vault.create(service="github.com", label="personal", username="nika", password="secret", notes="")

    response = invoke(vault, "search", {"query": "github"})

    assert response["protocol_version"] == 1
    assert response["ok"] is True
    assert response["result"][0]["id"] == 1
    assert response["result"][0]["service"] == "github.com"
    assert response["result"][0]["label"] == "personal"
    assert response["result"][0]["username"] == "nika"
    assert response["result"][0]["password"] == "secret"
    assert response["result"][0]["notes"] == ""
    assert isinstance(response["result"][0]["created_at"], str)
    assert isinstance(response["result"][0]["updated_at"], str)


def test_rpc_v2_import_returns_non_secret_summary(vault: Vault) -> None:
    """Catches v2 import responses leaking records or omitting result counts."""
    response = invoke(
        vault,
        "import",
        {
            "records": [
                {
                    "service": "github.com",
                    "label": "",
                    "username": "nika",
                    "password": "secret",
                    "notes": "",
                }
            ],
            "duplicates": "skip",
        },
        version=2,
    )

    assert response == {
        "protocol_version": 2,
        "ok": True,
        "result": {"total": 1, "inserted": 1, "updated": 0, "skipped": 0},
    }
    assert "secret" not in repr(response)


def test_rpc_v1_existing_operation_remains_compatible(vault: Vault) -> None:
    """Catches a server upgrade breaking installed protocol-v1 clients."""
    response = invoke(vault, "health", {}, version=1)

    assert response["protocol_version"] == 1
    assert response["ok"] is True


def test_rpc_v1_cannot_use_import(vault: Vault) -> None:
    """Catches protocol-v1 callers reaching the new bulk mutation."""
    response = invoke(
        vault,
        "import",
        {"records": [], "duplicates": "skip"},
        version=1,
    )

    assert_error(response, "incompatible_protocol", version=1)


@pytest.mark.parametrize(
    "payload",
    [
        {"records": [], "duplicates": "skip"},
        {"records": [{}], "duplicates": "skip"},
        {
            "records": [
                {
                    "service": "github.com",
                    "label": "",
                    "username": "nika",
                    "password": "secret",
                    "notes": "",
                    "extra": "value",
                }
            ],
            "duplicates": "skip",
        },
        {
            "records": [
                {
                    "service": "github.com",
                    "label": "",
                    "username": "nika",
                    "password": 7,
                    "notes": "",
                }
            ],
            "duplicates": "skip",
        },
        {
            "records": [
                {
                    "service": "github.com",
                    "label": "",
                    "username": "nika",
                    "password": "secret",
                    "notes": "",
                }
            ],
            "duplicates": 1,
        },
    ],
)
def test_rpc_v2_import_rejects_invalid_nested_shapes(
    vault: Vault, payload: dict[str, object]
) -> None:
    """Catches malformed nested import values reaching the database layer."""
    response = invoke(vault, "import", payload, version=2)

    assert_error(response, "invalid_payload", version=2)


def test_rpc_v2_import_rejects_more_than_3000_rows(vault: Vault) -> None:
    """Catches the RPC boundary accepting an unbounded credential batch."""
    row = {
        "service": "github.com",
        "label": "",
        "username": "nika",
        "password": "secret",
        "notes": "",
    }

    response = invoke(
        vault,
        "import",
        {"records": [row] * 3001, "duplicates": "skip"},
        version=2,
    )

    assert_error(response, "invalid_payload", version=2)


def test_rpc_rejects_unknown_and_oversized_requests(vault: Vault) -> None:
    assert_error(invoke(vault, "unknown", {}), "unknown_operation")

    raw = b"{" + b"x" * MAX_REQUEST_BYTES
    response = json.loads(handle_request(raw, vault))

    assert_error(response, "request_too_large", version=2)


@pytest.mark.parametrize(
    ("raw_request", "response_version"),
    [
        (b"not json", 2),
        (b"\xff", 2),
        (b"[]", 2),
        (b'{"protocol_version":1,"operation":"health","payload":{},"extra":true}', 1),
        (b'{"protocol_version":true,"operation":"health","payload":{}}', 2),
        (b'{"protocol_version":3,"operation":"health","payload":{}}', 2),
        (b'{"protocol_version":1,"operation":1,"payload":{}}', 1),
        (b'{"protocol_version":1,"operation":"health","payload":[]}', 1),
    ],
)
def test_rpc_rejects_malformed_top_level_requests(
    vault: Vault, raw_request: bytes, response_version: int
) -> None:
    response = json.loads(handle_request(raw_request, vault))

    assert response["protocol_version"] == response_version
    assert response["ok"] is False
    assert response["error"]["code"] in {
        "malformed_request",
        "invalid_request",
        "invalid_payload",
        "incompatible_protocol",
    }


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("health", {"extra": "value"}),
        ("search", {}),
        ("search", {"query": "github", "extra": "value"}),
        ("create", {"service": "github.com"}),
        (
            "create",
            {
                "service": "github.com",
                "label": "",
                "username": "nika",
                "password": "secret",
                "notes": "",
                "extra": "value",
            },
        ),
        ("update", {"id": 1}),
        ("delete", {}),
        ("delete", {"id": 1, "extra": "value"}),
    ],
)
def test_rpc_requires_exact_payload_shapes(
    vault: Vault, operation: str, payload: dict[str, object]
) -> None:
    response = invoke(vault, operation, payload)

    assert_error(response, "invalid_payload")


def test_rpc_dispatches_all_mutating_operations(vault: Vault) -> None:
    created = invoke(
        vault,
        "create",
        {"service": "github.com", "label": "personal", "username": "nika", "password": "old", "notes": ""},
    )

    updated = invoke(
        vault,
        "update",
        {
            "id": created["result"]["id"],
            "service": "gitlab.com",
            "label": "work",
            "username": "nika",
            "password": "new",
            "notes": "rotated",
        },
    )
    listed = invoke(vault, "list", {})
    deleted = invoke(vault, "delete", {"id": created["result"]["id"]})

    assert updated["result"]["password"] == "new"
    assert listed["result"][0]["service"] == "gitlab.com"
    assert deleted["result"] is None
    assert invoke(vault, "list", {})["result"] == []


def test_rpc_maps_known_and_unexpected_errors_without_secrets(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    assert_error(invoke(vault, "delete", {"id": 999}), "not_found")

    monkeypatch.setattr(vault, "health", lambda: (_ for _ in ()).throw(RuntimeError("secret detail")))
    response = invoke(vault, "health", {})

    assert response == {
        "protocol_version": 1,
        "ok": False,
        "error": {"code": "internal_error", "message": "The server could not complete the request"},
    }
