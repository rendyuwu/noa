"""Secret handling: encryption at rest, password generation, out-of-band delivery (T15).

Copied from `noa-old` branch `MCP` rather than rewritten (C13, V69) — that branch is where
`yopass.py` and `password.py` exist at all; `staging` has neither, and an agent porting from
the branch DECISIONS §2 measured would find nothing to copy.

Five modules, one job each:

- `errors` — `SecretCryptoError` / `YopassError` trees, both `NoaError` so the one shared
  handler shapes them (V73).
- `crypto` — `SecretCipher`: Fernet with an `enc:v1:fernet:` version prefix, idempotent
  encrypt, refuse-unprefixed decrypt (C7, V48).
- `password` — `_generate_password()`: server-side, shell/cloud-init-safe alphabet, never an
  LLM argument (C15, V49).
- `yopass` — `_yopass_store()`: PGPy client-side encrypt, `POST /secret`, passphrase in the
  URL fragment so the yopass server can decrypt nothing (C15, V50).
- `redaction` — `redact_sensitive_data()`: one-way, by key name, for anything persisted or
  logged (T73, V8, V45).

Three boundaries this package exists to hold:

- The generated plaintext lives only in the caller's `execute()` frame. ⊥ persisted,
  ⊥ logged, ⊥ returned across the LLM boundary — the tool returns a `yopass_url` (V49).
- Fernet encrypts **server credentials**, ⊥ the database (C7) and ⊥ MCP tokens, which are
  SHA-256 hashed because NOA only verifies those (C5, V2).
- These are internal helpers. Neither is registered as an MCP tool, and neither takes a
  secret as an argument.

Consumers: `proxmox_reset_vm_password` (T27) for the generate → deliver → apply flow, and
the admin server CRUD + validate routes (T54) for credentials at rest. Reference doc:
`docs/integrations/yopass.md`.

`redaction.py` landed at T73 rather than T15, with the `tool_runs` writer that calls it: a
redactor with no caller is a control no test can exercise, which is how B2 shipped (V69).
It departs from `noa-old` on one point — that repo *encrypted* sensitive audit args so they
could be read back, and §V45/V47 say **redacted**, so here the replacement is one-way (see
the module docstring).
"""

from core.secrets.crypto import ENCRYPTED_PREFIX, SecretCipher
from core.secrets.errors import (
    SecretCryptoError,
    SecretDecryptError,
    SecretKeyUnavailableError,
    YopassError,
    YopassNotConfiguredError,
    YopassStoreError,
)
from core.secrets.password import PASSWORD_ALPHABET, _generate_password
from core.secrets.redaction import (
    REDACTED,
    SENSITIVE_KEYS,
    is_sensitive_key,
    redact_sensitive_data,
)
from core.secrets.yopass import _yopass_store

__all__ = [
    "ENCRYPTED_PREFIX",
    "PASSWORD_ALPHABET",
    "REDACTED",
    "SENSITIVE_KEYS",
    "SecretCipher",
    "SecretCryptoError",
    "SecretDecryptError",
    "SecretKeyUnavailableError",
    "YopassError",
    "YopassNotConfiguredError",
    "YopassStoreError",
    "_generate_password",
    "_yopass_store",
    "is_sensitive_key",
    "redact_sensitive_data",
]
