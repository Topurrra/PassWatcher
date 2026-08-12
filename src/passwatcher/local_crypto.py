"""Current-user Windows DPAPI protection for the local vault."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Protocol


CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4
DESCRIPTION = "Passwatcher local credential v1"


class ProtectionError(RuntimeError):
    """A safe local data-protection failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DataProtector(Protocol):
    """The byte protection interface used by local storage."""

    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class NativeDpapi(Protocol):
    """Injectable native boundary used to test DPAPI policy separately."""

    def protect(self, value: bytes, *, description: str, flags: int) -> bytes: ...

    def unprotect(self, value: bytes, *, flags: int) -> bytes: ...


class DpapiProtector:
    """Protect bytes for the current Windows user without displaying UI."""

    def __init__(self, api: NativeDpapi | None = None) -> None:
        if api is None:
            if sys.platform != "win32":
                raise ProtectionError(
                    "unsupported_platform",
                    "The local vault requires Windows current-user protection",
                )
            api = _CtypesDpapi()
        self._api = api

    def protect(self, plaintext: bytes) -> bytes:
        if type(plaintext) is not bytes:
            raise TypeError("plaintext must be bytes")
        try:
            return self._api.protect(
                plaintext,
                description=DESCRIPTION,
                flags=CRYPTPROTECT_UI_FORBIDDEN,
            )
        except (OSError, ValueError):
            raise ProtectionError(
                "protect_failed", "Windows could not protect local vault data"
            ) from None

    def unprotect(self, ciphertext: bytes) -> bytes:
        if type(ciphertext) is not bytes:
            raise TypeError("ciphertext must be bytes")
        try:
            return self._api.unprotect(
                ciphertext,
                flags=CRYPTPROTECT_UI_FORBIDDEN,
            )
        except (OSError, ValueError):
            raise ProtectionError(
                "unprotect_failed", "Windows could not read protected local vault data"
            ) from None


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _CtypesDpapi:
    """Minimal ownership-safe wrapper around Crypt32 DPAPI functions."""

    def __init__(self) -> None:
        crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
        kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)

        self._protect = crypt32.CryptProtectData
        self._protect.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._protect.restype = wintypes.BOOL

        self._unprotect = crypt32.CryptUnprotectData
        self._unprotect.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._unprotect.restype = wintypes.BOOL

        self._local_free = kernel32.LocalFree
        self._local_free.argtypes = [ctypes.c_void_p]
        self._local_free.restype = ctypes.c_void_p

    def protect(self, value: bytes, *, description: str, flags: int) -> bytes:
        return self._transform(self._protect, value, flags, description)

    def unprotect(self, value: bytes, *, flags: int) -> bytes:
        return self._transform(self._unprotect, value, flags, None)

    def _transform(
        self,
        function: object,
        value: bytes,
        flags: int,
        description: str | None,
    ) -> bytes:
        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        incoming = _DataBlob(
            len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        )
        outgoing = _DataBlob()

        if description is None:
            succeeded = function(
                ctypes.byref(incoming),
                None,
                None,
                None,
                None,
                flags,
                ctypes.byref(outgoing),
            )
        else:
            succeeded = function(
                ctypes.byref(incoming),
                description,
                None,
                None,
                None,
                flags,
                ctypes.byref(outgoing),
            )
        if not succeeded:
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            return ctypes.string_at(outgoing.pbData, outgoing.cbData)
        finally:
            if outgoing.pbData:
                self._local_free(ctypes.cast(outgoing.pbData, ctypes.c_void_p))
