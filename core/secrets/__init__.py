"""Secret handling: encryption at rest, password generation, out-of-band delivery.

Copied from `noa-old` branch `MCP` rather than rewritten — that branch is where
`yopass.py` and `password.py` exist at all; `staging` has neither, and an agent porting from
the branch DECISIONS section 2 measured would find nothing to copy.

Six modules, one job each:

- `errors` — `SecretCryptoError` / `YopassError` trees, both `NoaError` so the one shared
  handler shapes them.
- `crypto` — `SecretCipher`: Fernet with an `enc:v1:fernet:` version prefix, idempotent
  encrypt, refuse-unprefixed decrypt.
- `password` — `_generate_password()`: server-side, shell/cloud-init-safe alphabet, never an
  LLM argument.
- `yopass` — `_yopass_store()`: PGPy client-side encrypt, `POST /secret`, passphrase in the
  URL fragment so the yopass server can decrypt nothing.
- `delivery` — `SecretDelivery`: the seam the tool path holds, with the three yopass settings
  bound once at startup so `McpToolContext` carries a callable and not a `Settings`.
- `redaction` — `redact_sensitive_data()`: one-way, by key name, for anything persisted or
  logged.

Three boundaries this package exists to hold:

- The generated plaintext lives only in the caller's `execute()` frame. Never persisted,
  never logged, never returned across the LLM boundary — the tool returns a `yopass_url`.
- Fernet encrypts **server credentials**, never the database and never MCP tokens, which are
  SHA-256 hashed because NOA only verifies those.
- These are internal helpers. Neither is registered as an MCP tool, and neither takes a
  secret as an argument.

Consumers: `proxmox_reset_vm_password` for the generate → deliver → apply flow, and
the admin server CRUD + validate routes for credentials at rest. Reference doc:
`docs/integrations/yopass.md`.

`redaction.py` landed with the tool-run writer, not with the earlier secrets port — together
with the `tool_runs` writer that calls it: a redactor with no caller is a control no test can
exercise, which is how the inert host-key pin shipped.
It departs from `noa-old` on one point — that repo *encrypted* sensitive audit args so they
could be read back, and the run-row rules say **redacted**, so here the replacement is one-way
(see the module docstring).
"""

from core.secrets.crypto import ENCRYPTED_PREFIX, SecretCipher
from core.secrets.delivery import SecretDelivery, build_yopass_delivery
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
    "SecretDelivery",
    "SecretKeyUnavailableError",
    "YopassError",
    "YopassNotConfiguredError",
    "YopassStoreError",
    "_generate_password",
    "_yopass_store",
    "build_yopass_delivery",
    "is_sensitive_key",
    "redact_sensitive_data",
]
