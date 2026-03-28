from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

_REDACTED_VALUE: Final[str] = "[redacted]"
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_token",
        "token",
        "password",
        "secret",
        "ssh_password",
        "ssh_private_key",
        "ssh_private_key_passphrase",
        "private_key",
        "passphrase",
    }
)


def is_sensitive_key(key: str) -> bool:
    return key.strip().lower() in _SENSITIVE_KEYS


def redact_sensitive_data(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): (
                _REDACTED_VALUE
                if is_sensitive_key(str(key))
                else redact_sensitive_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_sensitive_data(item) for item in value]
    return value
