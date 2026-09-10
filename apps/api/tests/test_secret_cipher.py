"""Fernet secret encryption.

V48 is three claims, and each gets its own assertion rather than being inferred from a
round-trip passing: the round-trip itself, the `enc:v1:fernet:` format that makes the scheme
identifiable, and the refusal to hand back a value that was never encrypted.

The negative cases are the reason this file is longer than the module: a cipher that
double-wraps on re-encrypt, or that silently returns cleartext on decrypt, still passes a
naive round-trip test while leaving a column unreadable or unencrypted in production.
`noa-old` shipped the guards; here they are pinned.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi import status

from core.errors import NoaError
from core.secrets.crypto import ENCRYPTED_PREFIX, SecretCipher
from core.secrets.errors import (
    SecretCryptoError,
    SecretDecryptError,
    SecretKeyUnavailableError,
    YopassError,
    YopassNotConfiguredError,
    YopassStoreError,
)
from noa_api.api.errors import FALLBACK_STATUS, error_body, status_for
from support.auth import build_settings

PLAINTEXT = "ssh-private-key-material"


def _cipher() -> SecretCipher:
    return SecretCipher(key=Fernet.generate_key().decode())


# --- V48: round-trip and format ---


def test_round_trip_returns_plaintext() -> None:
    cipher = _cipher()
    assert cipher.decrypt_text(cipher.encrypt_text(PLAINTEXT)) == PLAINTEXT


def test_ciphertext_carries_versioned_prefix() -> None:
    """`enc:v1:fernet:` names the scheme, so a later v2 can be told apart at rest."""
    ciphertext = _cipher().encrypt_text(PLAINTEXT)

    assert ciphertext.startswith(ENCRYPTED_PREFIX)
    assert ENCRYPTED_PREFIX == "enc:v1:fernet:"
    assert PLAINTEXT not in ciphertext
    assert SecretCipher.is_encrypted_text(ciphertext)


def test_ciphertext_differs_per_call() -> None:
    """Fernet embeds a timestamp and IV — identical inputs must not collide at rest."""
    cipher = _cipher()
    assert cipher.encrypt_text(PLAINTEXT) != cipher.encrypt_text(PLAINTEXT)


def test_encrypt_is_idempotent_on_encrypted_input() -> None:
    """A PATCH resubmitting untouched ciphertext must not double-wrap it."""
    cipher = _cipher()
    once = cipher.encrypt_text(PLAINTEXT)

    twice = cipher.encrypt_text(once)

    assert twice == once
    assert cipher.decrypt_text(twice) == PLAINTEXT


def test_decrypt_rejects_unencrypted_value() -> None:
    """Passthrough here would report a still-cleartext column as a successful read."""
    with pytest.raises(SecretDecryptError):
        _cipher().decrypt_text(PLAINTEXT)


def test_decrypt_with_a_different_key_raises() -> None:
    ciphertext = _cipher().encrypt_text(PLAINTEXT)

    with pytest.raises(SecretDecryptError):
        _cipher().decrypt_text(ciphertext)


def test_maybe_decrypt_passes_plaintext_through() -> None:
    """The migration path: a column holding both encrypted and legacy rows."""
    cipher = _cipher()

    assert cipher.maybe_decrypt_text(PLAINTEXT) == PLAINTEXT
    assert cipher.maybe_decrypt_text(cipher.encrypt_text(PLAINTEXT)) == PLAINTEXT


def test_is_encrypted_text_reads_only_the_prefix() -> None:
    assert SecretCipher.is_encrypted_text(f"{ENCRYPTED_PREFIX}anything") is True
    assert SecretCipher.is_encrypted_text("enc:v2:aesgcm:anything") is False
    assert SecretCipher.is_encrypted_text("") is False


# --- Construction ---


def test_from_settings_uses_the_configured_key() -> None:
    """V52's key is what encrypts server credentials — not a second key built elsewhere."""
    key = Fernet.generate_key().decode()
    settings = build_settings(noa_secret_encryption_key=key)

    ciphertext = SecretCipher.from_settings(settings).encrypt_text(PLAINTEXT)

    assert SecretCipher(key=key).decrypt_text(ciphertext) == PLAINTEXT


def test_from_settings_uses_the_dev_generated_key() -> None:
    """Dev generates a key, so `from_settings` works without one configured."""
    settings = build_settings()

    cipher = SecretCipher.from_settings(settings)

    assert cipher.decrypt_text(cipher.encrypt_text(PLAINTEXT)) == PLAINTEXT


@pytest.mark.parametrize("key", ["", "   ", "not-a-fernet-key"])
def test_unusable_key_raises_at_construction(key: str) -> None:
    """Fail when the cipher is built, not at the first credential decrypt."""
    with pytest.raises(SecretKeyUnavailableError):
        SecretCipher(key=key)


# --- V73: one handler shapes these too ---


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (SecretCryptoError(), status.HTTP_500_INTERNAL_SERVER_ERROR),
        (SecretKeyUnavailableError(), status.HTTP_500_INTERNAL_SERVER_ERROR),
        (SecretDecryptError(), status.HTTP_500_INTERNAL_SERVER_ERROR),
        (YopassError(), status.HTTP_502_BAD_GATEWAY),
        (YopassStoreError(), status.HTTP_502_BAD_GATEWAY),
        (YopassNotConfiguredError(), status.HTTP_500_INTERNAL_SERVER_ERROR),
    ],
)
def test_secret_errors_have_an_explicit_status_mapping(
    error: NoaError, expected_status: int
) -> None:
    """No secret error may take the 503 fallback — that reads as "NOA is down"."""
    assert status_for(error) != FALLBACK_STATUS
    assert status_for(error) == expected_status


def test_secret_error_codes_are_the_stable_strings() -> None:
    """C15 names `yopass_not_configured`; callers and receipts branch on these."""
    assert YopassNotConfiguredError().error_code == "yopass_not_configured"
    assert YopassStoreError().error_code == "yopass_store_failed"
    assert SecretDecryptError().error_code == "secret_decrypt_failed"
    assert SecretKeyUnavailableError().error_code == "secret_key_unavailable"


def test_secret_error_bodies_carry_no_internal_detail() -> None:
    """V8: `detail` names the configuration fault and stays in the logs."""
    error = SecretDecryptError("key rotated on 2026-08-01, row written under the old one")

    body = error_body(error)

    assert set(body) == {"error_code", "message"}
    assert "rotated" not in body["message"]
