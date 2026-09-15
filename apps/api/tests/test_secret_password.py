"""Server-side password generation.

The password-generation rule's first half — the password is generated here, never supplied as a tool
argument — is structural: `_generate_password` takes a length and nothing else, so there is no
argument a model could fill. What is testable is the alphabet. It is letters and digits and nothing
else, because the customer reads the value back and types it by hand once the operator has passed
the yopass link on — an owner-stated reason, recorded in `docs/integrations/yopass.md`. The layer
that broke
in production is still underneath it — the value is embedded in a cloud-init payload and passed
through a shell-quoted remote command, where a space, quote, backtick or backslash silently corrupts
the credential just set on a customer VM — but that hazard is now satisfied by construction rather
than by a filter. So the assertion below is the whole-class one, and it is what catches punctuation
creeping back into the constant.

The delivery half of that rule (plaintext never logged) is asserted against the real helper in
`test_yopass_store.py::test_logs_carry_no_plaintext_or_passphrase`.
"""

from __future__ import annotations

import string

import pytest

from core.secrets.password import PASSWORD_ALPHABET, _generate_password
from support.auth import build_settings


def test_password_alphabet_is_letters_and_digits_only() -> None:
    """A customer types this value back, so the symbol class is gone entirely.

    One whole-class assertion rather than a list of the five shell-breaking characters (space,
    both quote styles, backtick, backslash): those are a subset of it now, and with no
    punctuation in the alphabet there is nothing left for a filter to remove.
    """
    assert set(PASSWORD_ALPHABET) == set(string.ascii_letters + string.digits)
    # Set equality would hide a repeated character, which would skew the draw.
    assert len(PASSWORD_ALPHABET) == 62
    assert not set(string.punctuation + " ") & set(PASSWORD_ALPHABET)


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
    """The password-reset runner reads the configured length, never a hardcoded one."""
    settings = build_settings(secret_password_length=40)

    assert len(_generate_password(settings.secret_password_length)) == 40


def test_two_passwords_differ() -> None:
    """A constant here would hand every VM the same credential."""
    assert len({_generate_password() for _ in range(50)}) == 50


@pytest.mark.parametrize("length", [0, -1])
def test_zero_length_rejected(length: int) -> None:
    """An empty password must be a caller bug, never a silently accepted empty string."""
    with pytest.raises(ValueError, match="password length"):
        _generate_password(length)
