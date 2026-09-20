"""Secret-handling errors.

`noa-old` declared these inline in `crypto.py` and `yopass.py` as bare `Exception` subclasses, with
yopass carrying a hand-rolled `error_code` class attribute. Here they collect into one module and
derive from `core.errors.NoaError`, for the reason `core/remote_exec/errors.py` already states: both
trees reach an HTTP response — the admin validate route's `POST
/admin/{whm,proxmox,pmg}/servers/{id}/validate` decrypts stored credentials before it connects — and
the shared error-handling seam requires one handler shaping every body. `error_code` was already the
field yopass exposed, so the base class formalizes what the source improvised.

The split between the two trees is what a caller acts on:

- `SecretCryptoError` — NOA cannot read or write its own ciphertext. Configuration or key
  rotation, never the operator's input. Nothing to retry.
- `YopassError` — the delivery hop failed. Server-side password generation, kept off the LLM
  path, makes this recoverable by design: the reset tool stores the secret *before* it touches
  the VM, so a yopass failure aborts with nothing changed and no operator locked out.

Codes raised by this package, all stable strings clients and tests branch on:

- `secret_key_unavailable` — key absent or not a valid Fernet key.
- `secret_decrypt_failed`  — value is not encrypted, or will not decrypt under this key.
- `yopass_not_configured`  — `YOPASS_BASE_URL` unset; a tool error, never a crash.
- `yopass_store_failed`    — the yopass POST failed or returned something unusable.

Messages stay credential-free: they name which step failed, never the key, the
passphrase, or the plaintext being protected.
"""

from __future__ import annotations

from core.errors import NoaError


class SecretCryptoError(NoaError):
    """Encryption or decryption of a stored secret failed."""

    error_code: str = "secret_crypto_failed"
    message: str = "A stored secret could not be processed. Contact an administrator."
    # 500: NOA cannot read or write its own ciphertext. The operator's request was fine and
    # retrying changes nothing — the key is absent, wrong, or the row was never encrypted.
    # Which of those it is stays in `detail`. Subclasses inherit this attribute.
    status_code = 500


class SecretKeyUnavailableError(SecretCryptoError):
    """No usable Fernet key.

    In practice unreachable in a booted app: `Settings._resolve_encryption_key` validates
    the key at construction, so a bad key is a startup failure. Kept because
    `SecretCipher` is also constructible with an explicit key, and a caller that passes
    garbage should hear about it here rather than at the first `encrypt`.
    """

    error_code: str = "secret_key_unavailable"
    message: str = "Secret encryption is not configured. Contact an administrator."


class SecretDecryptError(SecretCryptoError):
    """Ciphertext will not decrypt, or was never encrypted in the first place.

    Both cases share a code deliberately: from the caller's side the stored value is
    unusable either way, and the distinction (wrong key vs. never encrypted) is a
    deployment detail that belongs in `detail`, not in a body.
    """

    error_code: str = "secret_decrypt_failed"
    message: str = "A stored secret could not be decrypted. Contact an administrator."


class YopassError(NoaError):
    """Base for the internal yopass delivery helper."""

    error_code: str = "yopass_store_failed"
    message: str = "The secret could not be delivered. Nothing was changed."
    # 502 for the delivery hop, same reading as `SSHExecutionError`: NOA works, the system it
    # depends on did not answer usably. Server-side generation's deliver-first ordering means
    # nothing was changed when this is raised.
    status_code = 502


class YopassNotConfiguredError(YopassError):
    """`yopass_base_url` is absent (a tool error, never a crash)."""

    error_code: str = "yopass_not_configured"
    message: str = "Secret delivery is not configured. Contact an administrator."
    # 500, not the 502 it would inherit from `YopassError`: a missing configuration is NOA's
    # own gap, not the upstream's.
    status_code = 500


class YopassStoreError(YopassError):
    """The yopass POST failed, or the response carried no secret id."""

    error_code: str = "yopass_store_failed"
    message: str = "The secret could not be delivered. Nothing was changed."
