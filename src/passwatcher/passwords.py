"""Cryptographically secure password generation with explicit policies."""

from __future__ import annotations

import secrets
import string


SYMBOLS = "!@#$%^&*()-_=+[]{}"


class PasswordPolicyError(ValueError):
    """Raised when a requested password policy cannot be satisfied."""


def generate_password(
    length: int = 24,
    lower: bool = True,
    upper: bool = True,
    digits: bool = True,
    symbols: bool = True,
) -> str:
    """Return a random password containing every requested character category."""
    categories = [
        alphabet
        for enabled, alphabet in (
            (lower, string.ascii_lowercase),
            (upper, string.ascii_uppercase),
            (digits, string.digits),
            (symbols, SYMBOLS),
        )
        if enabled
    ]
    if not 8 <= length <= 256:
        raise PasswordPolicyError("password length must be between 8 and 256")
    if not categories:
        raise PasswordPolicyError("at least one character category is required")
    if length < len(categories):
        raise PasswordPolicyError("password length is shorter than the enabled categories")

    alphabet = "".join(categories)
    password = [secrets.choice(category) for category in categories]
    password.extend(secrets.choice(alphabet) for _ in range(length - len(password)))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)
