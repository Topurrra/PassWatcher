import json

import pytest

from passwatcher.protocol import ProtocolError, make_request, parse_response


def test_request_is_versioned_json_line():
    raw = make_request("search", {"query": "github"})

    assert json.loads(raw) == {
        "protocol_version": 1,
        "operation": "search",
        "payload": {"query": "github"},
    }


def test_response_rejects_wrong_version():
    raw = b'{"protocol_version":2,"ok":true,"result":{}}'

    with pytest.raises(ProtocolError) as error:
        parse_response(raw)

    assert error.value.code == "incompatible_protocol"


def test_response_maps_safe_server_error():
    raw = (
        b'{"protocol_version":1,"ok":false,'
        b'"error":{"code":"not_found","message":"No match"}}'
    )

    with pytest.raises(ProtocolError, match="No match") as error:
        parse_response(raw)

    assert error.value.code == "not_found"
