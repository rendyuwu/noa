from __future__ import annotations

from functools import lru_cache
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

_ENCRYPTED_PREFIX: Final[str] = "enc:v1:fernet:"


class SecretCryptoError(Exception):
    pass


class SecretKeyUnavailableError(SecretCryptoError):
    pass


class SecretDecryptError(SecretCryptoError):
    pass


class SecretCipher:
    def __init__(self, *, key: str) -> None:
        normalized_key = key.strip().encode("utf-8")
        if not normalized_key:
            raise SecretKeyUnavailableError("NOA DB secret key is empty")
        try:
            self._fernet = Fernet(normalized_key)
        except ValueError as exc:
            raise SecretKeyUnavailableError(
                "NOA DB secret key must be a valid Fernet key"
            ) from exc

    @classmethod
    def from_settings(cls) -> SecretCipher:
        from noa_api.core.config import settings

        key = (
            settings.noa_db_secret_key.get_secret_value()
            if settings.noa_db_secret_key is not None
            else ""
        )
        if not key:
            raise SecretKeyUnavailableError("NOA DB secret key is not configured")
        return cls(key=key)

    def encrypt_text(self, plaintext: str) -> str:
        if plaintext.startswith(_ENCRYPTED_PREFIX):
            return plaintext
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        return f"{_ENCRYPTED_PREFIX}{token}"

    def decrypt_text(self, ciphertext: str) -> str:
        if not ciphertext.startswith(_ENCRYPTED_PREFIX):
            raise SecretDecryptError("Value is not encrypted")
        token = ciphertext[len(_ENCRYPTED_PREFIX) :].encode("utf-8")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise SecretDecryptError("Unable to decrypt stored secret") from exc

    def maybe_decrypt_text(self, value: str) -> str:
        if value.startswith(_ENCRYPTED_PREFIX):
            return self.decrypt_text(value)
        return value

    @staticmethod
    def is_encrypted_text(value: str) -> bool:
        return value.startswith(_ENCRYPTED_PREFIX)


@lru_cache(maxsize=1)
def get_secret_cipher() -> SecretCipher:
    return SecretCipher.from_settings()


def encrypt_text(plaintext: str) -> str:
    return get_secret_cipher().encrypt_text(plaintext)


def decrypt_text(ciphertext: str) -> str:
    return get_secret_cipher().decrypt_text(ciphertext)


def maybe_decrypt_text(value: str) -> str:
    if not SecretCipher.is_encrypted_text(value):
        return value
    return get_secret_cipher().maybe_decrypt_text(value)


def is_encrypted_text(value: str) -> bool:
    return SecretCipher.is_encrypted_text(value)
