from __future__ import annotations

import sys

import pytest

from passwatcher.local_crypto import (
    CRYPTPROTECT_LOCAL_MACHINE,
    CRYPTPROTECT_UI_FORBIDDEN,
    DpapiProtector,
    ProtectionError,
)


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, int]] = []

    def protect(self, value: bytes, *, description: str, flags: int) -> bytes:
        self.calls.append((description, value, flags))
        return b"protected:" + value[::-1]

    def unprotect(self, value: bytes, *, flags: int) -> bytes:
        self.calls.append(("unprotect", value, flags))
        return value.removeprefix(b"protected:")[::-1]


def test_dpapi_protector_uses_noninteractive_current_user_scope() -> None:
    """Catches local data being machine-scoped or invoking a Windows prompt."""
    api = FakeApi()
    protector = DpapiProtector(api)

    protected = protector.protect(b"secret")

    assert protector.unprotect(protected) == b"secret"
    assert all(
        flags == CRYPTPROTECT_UI_FORBIDDEN for _name, _value, flags in api.calls
    )
    assert all(
        flags & CRYPTPROTECT_LOCAL_MACHINE == 0 for _name, _value, flags in api.calls
    )


def test_dpapi_failure_never_contains_input() -> None:
    """Catches native error translation leaking the credential bytes."""
    class BrokenApi(FakeApi):
        def protect(self, value: bytes, *, description: str, flags: int) -> bytes:
            raise OSError(5, f"access denied for {value!r}")

    with pytest.raises(ProtectionError) as raised:
        DpapiProtector(BrokenApi()).protect(b"hidden-secret")

    assert raised.value.code == "protect_failed"
    assert "hidden-secret" not in str(raised.value)


def test_dpapi_rejects_non_byte_values() -> None:
    """Catches accidental implicit text encoding at the protection boundary."""
    protector = DpapiProtector(FakeApi())

    with pytest.raises(TypeError):
        protector.protect("secret")  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI contract")
def test_native_dpapi_round_trip_is_opaque() -> None:
    """Catches incorrect ctypes layouts, flags, or Windows allocation handling."""
    protector = DpapiProtector()
    plaintext = b"local-dpapi-secret"

    protected = protector.protect(plaintext)

    assert plaintext not in protected
    assert protector.unprotect(protected) == plaintext


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI contract")
def test_native_dpapi_rejects_tampered_blob() -> None:
    """Catches protected data being accepted without DPAPI's integrity check."""
    protector = DpapiProtector()
    protected = bytearray(protector.protect(b"secret"))
    protected[len(protected) // 2] ^= 1

    with pytest.raises(ProtectionError) as raised:
        protector.unprotect(bytes(protected))

    assert raised.value.code == "unprotect_failed"
