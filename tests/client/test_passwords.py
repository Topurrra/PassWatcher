from __future__ import annotations

import pytest

from passwatcher.passwords import PasswordPolicyError, generate_password


def test_generated_password_has_requested_categories():
    """Catches generation that omits an enabled character class."""
    value = generate_password(24)

    assert len(value) == 24
    assert any(character.islower() for character in value)
    assert any(character.isupper() for character in value)
    assert any(character.isdigit() for character in value)
    assert any(character in "!@#$%^&*()-_=+[]{}" for character in value)


def test_generator_rejects_impossible_policy():
    """Catches policies that cannot fit one character per enabled class."""
    with pytest.raises(PasswordPolicyError):
        generate_password(3, lower=True, upper=True, digits=True, symbols=True)
