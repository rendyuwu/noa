"""Server-side password generation.

V49's first half — the password is generated here, never supplied as a tool argument — is
structural: `_generate_password` takes a length and nothing else, so there is no argument a
model could fill. What is testable is the alphabet, and it is the part that broke in
production: the generated value is embedded in a cloud-init payload and passed through a
shell-quoted remote command, so a space, quote, backtick or backslash silently corrupts the
credential that was just set on a customer VM.

The delivery half of V49 (plaintext never logged) is asserted against the real helper in
`test_yopass_store.py::test_logs_carry_no_plaintext_or_passphrase`.
"""

from __future__ import annotations

import string

import pytest

from core.secrets.password import PASSWORD_ALPHABET, _generate_password
from support.auth import build_settings

FORBIDDEN = " \"'`\\"


def test_password_alphabet_excludes_shell_breaking_symbols() -> None:
    """Space, both quote styles, backtick, backslash — each has broken a layer."""
    for char in FORBIDDEN:
        assert char not in PASSWORD_ALPHABET

    # Everything else stays, so the exclusion costs ~5 symbols of entropy, not the class.
    for char in string.ascii_letters + string.digits:
        assert char in PASSWORD_ALPHABET
    assert "!" in PASSWORD_ALPHABET
    assert "@" in PASSWORD_ALPHABET


def test_generated_passwords_stay_inside_the_alphabet() -> None:
    """Sampled rather than reasoned about: the charset is what reaches the VM."""
    for _ in range(200):
        assert set(_generate_password(32)) <= set(PASSWORD_ALPHABET)


@pytest.mark.parametrize("length", [1, 8, 24, 64])
def test_password_honours_requested_length(length: int) -> None:
    assert len(_generate_password(length)) == length


def test_default_length_matches_the_configured_default() -> None:
    """`SECRET_PASSWORD_LENGTH` defaults to 24; a caller without settings gets the same."""
    assert len(_generate_password()) == build_settings().secret_password_length == 24


def test_length_comes_from_settings_when_the_caller_passes_it() -> None:
    """T27 reads the operator-configurable length rather than hardcoding one."""
    settings = build_settings(secret_password_length=40)

    assert len(_generate_password(settings.secret_password_length)) == 40


def test_two_passwords_differ() -> None:
    """A constant here would hand every VM the same credential."""
    assert len({_generate_password() for _ in range(50)}) == 50


@pytest.mark.parametrize("length", [0, -1])
def test_zero_length_rejected(length: int) -> None:
    """An empty password must be a caller bug, ⊥ a silently accepted empty string."""
    with pytest.raises(ValueError, match="password length"):
        _generate_password(length)
