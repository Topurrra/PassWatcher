"""Authenticated deterministic protector used only by local-vault tests."""

from __future__ import annotations

import hashlib
import hmac


class TestProtector:
    __test__ = False
    _KEY = b"passwatcher-local-vault-test-key"

    def protect(self, plaintext: bytes) -> bytes:
        encrypted = bytes(value ^ self._KEY[index % len(self._KEY)] for index, value in enumerate(plaintext))
        return b"PT1" + hmac.digest(self._KEY, encrypted, hashlib.sha256) + encrypted

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"PT1") or len(ciphertext) < 35:
            raise ValueError("invalid protected data")
        digest, encrypted = ciphertext[3:35], ciphertext[35:]
        if not hmac.compare_digest(digest, hmac.digest(self._KEY, encrypted, hashlib.sha256)):
            raise ValueError("invalid protected data")
        return bytes(value ^ self._KEY[index % len(self._KEY)] for index, value in enumerate(encrypted))
