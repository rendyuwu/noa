from __future__ import annotations

from cryptography.fernet import Fernet


def test_secret_cipher_round_trip_encrypts_and_decrypts() -> None:
    from noa_api.core.secrets.crypto import SecretCipher

    cipher = SecretCipher(key=Fernet.generate_key().decode("utf-8"))

    encrypted = cipher.encrypt_text("super-secret")

    assert encrypted.startswith("enc:v1:fernet:")
    assert cipher.decrypt_text(encrypted) == "super-secret"


def test_secret_cipher_maybe_decrypt_accepts_plaintext() -> None:
    from noa_api.core.secrets.crypto import SecretCipher

    cipher = SecretCipher(key=Fernet.generate_key().decode("utf-8"))

    assert cipher.maybe_decrypt_text("plain-text") == "plain-text"
