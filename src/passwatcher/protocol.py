"""Strict JSON-line protocol helpers for the Passwatcher client."""

from __future__ import annotations

import json
from typing import cast


PROTOCOL_VERSION = 1
_MALFORMED_MESSAGE = "The server returned an invalid response"


class ProtocolError(Exception):
    """A safe, structured error returned by the Passwatcher protocol."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def make_request(operation: str, payload: dict[str, object]) -> bytes:
    """Encode one compact, UTF-8 JSON request line."""
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "operation": operation,
        "payload": payload,
    }
    return (
        json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def parse_response(raw: bytes) -> dict[str, object]:
    """Decode a validated protocol response or raise a safe error."""
    try:
        response = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _malformed_response() from None

    if not isinstance(response, dict):
        raise _malformed_response()

    version = response.get("protocol_version")
    if type(version) is not int:
        raise _malformed_response()
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            "incompatible_protocol", "The server uses an incompatible protocol version"
        )

    ok = response.get("ok")
    if type(ok) is not bool:
        raise _malformed_response()

    if ok:
        if "result" not in response:
            raise _malformed_response()
        return cast(dict[str, object], response)

    error = response.get("error")
    if not isinstance(error, dict):
        raise _malformed_response()
    code = error.get("code")
    message = error.get("message")
    if not isinstance(code, str) or not code or not isinstance(message, str) or not message:
        raise _malformed_response()
    return _raise_server_error(code, message)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _malformed_response() -> ProtocolError:
    return ProtocolError("malformed_response", _MALFORMED_MESSAGE)


def _raise_server_error(code: str, message: str) -> dict[str, object]:
    raise ProtocolError(code, message)
