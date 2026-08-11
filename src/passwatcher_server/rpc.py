"""Versioned, bounded JSON RPC dispatch for the local vault server."""

from __future__ import annotations

from dataclasses import asdict
import json

from .database import DatabaseError, NotFoundError, ValidationError, Vault


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1_048_576

_REQUEST_FIELDS = frozenset({"protocol_version", "operation", "payload"})
_PAYLOAD_FIELDS = {
    "search": frozenset({"query"}),
    "list": frozenset(),
    "create": frozenset({"service", "label", "username", "password", "notes"}),
    "update": frozenset({"id", "service", "label", "username", "password", "notes"}),
    "delete": frozenset({"id"}),
    "health": frozenset(),
}


def handle_request(raw: bytes, vault: Vault) -> bytes:
    """Handle one untrusted request and return one safe JSON response."""
    if len(raw) > MAX_REQUEST_BYTES:
        return _error("request_too_large", "The request is too large")

    try:
        request = _parse_request(raw)
        if set(request) != _REQUEST_FIELDS:
            raise _RequestError("invalid_request", "The request fields are invalid")
        operation = request["operation"]
        payload = request["payload"]
        _validate_request(operation, payload, request["protocol_version"])
        result = _dispatch(operation, payload, vault)
    except _RequestError as error:
        return _error(error.code, error.message)
    except ValidationError as error:
        return _error(error.code, error.message)
    except NotFoundError:
        return _error("not_found", "The requested credential was not found")
    except DatabaseError:
        return _error("database_error", "The vault database could not complete the request")
    except Exception:
        return _error("internal_error", "The server could not complete the request")
    return _success(result)


class _RequestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


def _parse_request(raw: bytes) -> dict[str, object]:
    try:
        request = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _RequestError("malformed_request", "The request must be valid UTF-8 JSON") from None
    if not isinstance(request, dict):
        raise _RequestError("invalid_request", "The request must be a JSON object")
    return request


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _validate_request(operation: object, payload: object, version: object) -> None:
    if type(version) is not int:
        raise _RequestError("invalid_request", "protocol_version must be an integer")
    if version != PROTOCOL_VERSION:
        raise _RequestError("incompatible_protocol", "The request uses an incompatible protocol version")
    if not isinstance(operation, str):
        raise _RequestError("invalid_request", "operation must be text")
    if operation not in _PAYLOAD_FIELDS:
        raise _RequestError("unknown_operation", "The requested operation is not supported")
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS[operation]:
        raise _RequestError("invalid_payload", "The payload fields are invalid for this operation")
    _validate_payload_types(operation, payload)


def _validate_payload_types(operation: str, payload: dict[object, object]) -> None:
    text_fields = _PAYLOAD_FIELDS[operation] - {"id"}
    if any(not isinstance(payload[field], str) for field in text_fields):
        raise _RequestError("invalid_payload", "Text payload fields must be strings")
    if "id" in payload and (type(payload["id"]) is not int):
        raise _RequestError("invalid_payload", "id must be an integer")


def _dispatch(operation: str, payload: dict[object, object], vault: Vault) -> object:
    if operation == "search":
        return [_credential(item) for item in vault.search(payload["query"])]
    if operation == "list":
        return [_credential(item) for item in vault.list_all()]
    if operation == "create":
        return _credential(vault.create(**payload))
    if operation == "update":
        credential_id = payload.pop("id")
        return _credential(vault.update(credential_id, **payload))
    if operation == "delete":
        vault.delete(payload["id"])
        return None
    if operation == "health":
        return vault.health()
    raise AssertionError("Operation should have been validated")


def _credential(value: object) -> dict[str, object]:
    return asdict(value)


def _success(result: object) -> bytes:
    return _encode({"protocol_version": PROTOCOL_VERSION, "ok": True, "result": result})


def _error(code: str, message: str) -> bytes:
    return _encode(
        {
            "protocol_version": PROTOCOL_VERSION,
            "ok": False,
            "error": {"code": code, "message": message},
        }
    )


def _encode(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
