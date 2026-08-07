"""Secret handling: encryption at rest, password generation, out-of-band delivery (T15).

Copied from `noa-old` branch `MCP` rather than rewritten (C13, V69) — that branch is where
`yopass.py` and `password.py` exist at all; `staging` has neither, and an agent porting from
the branch DECISIONS §2 measured would find nothing to copy.

Four modules, one job each:

- `errors` — `SecretCryptoError` / `YopassError` trees, both `NoaError` so the one shared
  handler shapes them (V73).
- `crypto` — `SecretCipher`: Fernet with an `enc:v1:fernet:` version prefix, idempotent
  encrypt, refuse-unprefixed decrypt (C7, V48).
- `password` — `_generate_password()`: server-side, shell/cloud-init-safe alphabet, never an
  LLM argument (C15, V49).
- `yopass` — `_yopass_store()`: PGPy client-side encrypt, `POST /secret`, passphrase in the
  URL fragment so the yopass server can decrypt nothing (C15, V50).

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

`noa-old`'s `redaction.py` is not here: it lands with the code that *writes* redacted audit
args, which is T73, not with the table itself. T35 created `tool_runs.args` and deliberately
left it unredacted-by-nobody — a redactor with no caller is a control no test can exercise,
which is how B2 shipped (V69). T55 reads those rows; T73 is what puts them there.
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
from core.secrets.yopass import _yopass_store

__all__ = [
    "ENCRYPTED_PREFIX",
    "PASSWORD_ALPHABET",
    "SecretCipher",
    "SecretCryptoError",
    "SecretDecryptError",
    "SecretKeyUnavailableError",
    "YopassError",
    "YopassNotConfiguredError",
    "YopassStoreError",
    "_generate_password",
    "_yopass_store",
]
