"""Fernet encryption for secrets held at rest.

Copied from `noa-old` branch `MCP`. The part worth copying is the
`enc:v1:fernet:` prefix and the behaviour built around it:

- **Encryption is idempotent.** `encrypt_text` on an already-prefixed value returns it
  unchanged, so an admin PATCH that resubmits a row's untouched ciphertext cannot double-wrap
  it into something no later read can unwrap.
- **The prefix is a version marker, not decoration.** `enc:v1:fernet:` says which scheme
  produced the token, so a future `v2` can be introduced without guessing at rows already
  written.
- **Plaintext survivors are detectable.** `is_encrypted_text` / `maybe_decrypt_text` let a
  migration walk a column that holds both encrypted and legacy plaintext rows and convert
  only what needs it. `noa-old`'s `20260424_encrypt_server_secrets` migration is exactly that
  case; T54 inherits it.

`decrypt_text` refuses an unprefixed value rather than returning it. A silent passthrough
there would turn "this row never got encrypted" into a successful read, which is how a column
quietly stays in cleartext.

What encrypts: server credentials — SSH passwords and private keys, WHM/Proxmox API tokens
(C7, V52). ⊥ the database, ⊥ MCP tokens, which are SHA-256 hashed because NOA only ever
verifies those.

Settings are injected, not imported. `noa-old` reached for a module-global `settings` inside
`from_settings()`; this repo has no such singleton — `core/` services take `Settings` in
their constructor (`LDAPService`, `JWTService`) and `noa_api.main.build_runtime` is the one
caller of `get_settings()`. So the classmethod takes the argument, and `noa-old`'s
`@lru_cache get_secret_cipher()` plus its module-level `encrypt_text`/`decrypt_text` wrappers
are gone: they existed only to hide that global. `noa_api.main.build_runtime` builds the one
cipher on `AppRuntime` (T21 — the tool path needed it before T54's admin routes did), and every
decrypt site takes it as an argument.
"""

from __future__ import annotations

from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from core.config import Settings
from core.secrets.errors import SecretDecryptError, SecretKeyUnavailableError

ENCRYPTED_PREFIX: Final[str] = "enc:v1:fernet:"


class SecretCipher:
    """Encrypts and decrypts secrets stored in NOA's own tables.

    Construct with an explicit key, or with `from_settings` for the configured one. One
    instance is reusable and cheap to hold: `Fernet` is stateless after construction.
    """

    def __init__(self, *, key: str) -> None:
        normalized_key = key.strip().encode("utf-8")
        if not normalized_key:
            raise SecretKeyUnavailableError("secret encryption key is empty")
        try:
            self._fernet = Fernet(normalized_key)
        except (ValueError, TypeError) as exc:
            raise SecretKeyUnavailableError(
                "secret encryption key must be a urlsafe-base64-encoded 32-byte Fernet key"
            ) from exc

    @classmethod
    def from_settings(cls, settings: Settings) -> SecretCipher:
        """Build from `NOA_SECRET_ENCRYPTION_KEY`.

        `Settings` has already validated the key at construction, so this raises only when a
        caller hands over settings built some other way.
        """
        return cls(key=settings.secret_encryption_key)

    def encrypt_text(self, plaintext: str) -> str:
        """Return `enc:v1:fernet:<token>`. Already-encrypted input passes through."""
        if plaintext.startswith(ENCRYPTED_PREFIX):
            return plaintext
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        return f"{ENCRYPTED_PREFIX}{token}"

    def decrypt_text(self, ciphertext: str) -> str:
        """Unwrap a prefixed token. Unprefixed input is an error, ⊥ a passthrough."""
        if not ciphertext.startswith(ENCRYPTED_PREFIX):
            raise SecretDecryptError("value is not encrypted")
        token = ciphertext[len(ENCRYPTED_PREFIX) :].encode("utf-8")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise SecretDecryptError("stored secret does not decrypt under this key") from exc

    def maybe_decrypt_text(self, value: str) -> str:
        """Decrypt if prefixed, else return as-is — for columns mid-migration."""
        if value.startswith(ENCRYPTED_PREFIX):
            return self.decrypt_text(value)
        return value

    @staticmethod
    def is_encrypted_text(value: str) -> bool:
        return value.startswith(ENCRYPTED_PREFIX)


__all__ = ["ENCRYPTED_PREFIX", "SecretCipher"]
