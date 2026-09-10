# yopass Secret Delivery Reference

Canonical reference for how NOA delivers generated secrets to operators via
[yopass](https://github.com/jhaals/yopass). Ported from `noa-old` branch `MCP` with the
helpers themselves (§T.15, C13).

Today the only planned consumer is the Proxmox cloud-init password reset tool,
`proxmox_reset_vm_password` (§T.27). The helpers are shared and internal, not welded to that
tool — DECISIONS §8.5.

Update this file whenever the secret-delivery pattern, config, or consumers change, in the
same commit as the code.

## Why server-side delivery (`noa-old` GH #91)

The cloud-init password reset previously accepted the new password as an LLM tool argument
(`new_password`) and echoed it back in the result. That put plaintext on the LLM boundary: in
the prompt, in the model context, in the stored transcript, and potentially in the logs.

The internal-delivery pattern removes the password from the LLM boundary entirely:

- The password is **generated internally** inside the tool's `execute()` call stack
  (`core/secrets/password.py`), not supplied by the model (C15, §V.49).
- It is **delivered out-of-band** via a yopass share link. Only the link (`yopass_url`)
  crosses back across the LLM boundary.
- The operator copies the link and emails it to the customer. **NOA never sends email.**

What crosses the LLM boundary:

| Direction | Value |
|-----------|-------|
| in | `server_ref`, `node`, `vmid`, `username` |
| out | `status`, `yopass_url`, verification metadata (no plaintext) |

No `reason` in either column: the reason is typed by the operator in the approval card and
never enters a tool schema (C8, §V.15, §V.43).

The generated plaintext lives only in the `execute()` stack for the duration of the set +
verification window. Never persisted, never stored in a vault, never handed back as a
`password_ref`, never logged.

## How it works

`_yopass_store(username, password, *, settings)` (`core/secrets/yopass.py`) is a code-level
helper, **not** an MCP tool:

1. Build the cleartext blob: `username` + `password` only.
2. Generate a random passphrase and **client-side OpenPGP symmetric encrypt** the blob with
   [PGPy](https://github.com/SecurityInnovation/PGPy) (pure-Python, AES-256). The yopass
   server only ever sees ciphertext.
3. `POST <YOPASS_BASE_URL>/secret` with
   `{secret: <ciphertext>, expiration: <seconds>, one_time: <bool>}`.
4. The response `{message: <uuid>}` becomes the share URL:
   `<YOPASS_BASE_URL>/#/s/<uuid>/<passphrase>`.

The decryption passphrase rides in the **URL fragment** (after `#`). Browsers never send the
fragment to the server, so the yopass instance can decrypt nothing — only whoever holds the
full link can (§V.50).

### Deliver-first ordering

The reset tool stores the secret **before** mutating the VM (§V.62):

```
generate password → yopass store (encrypt + POST → URL)
                  → set cipassword → regenerate cloud-init → verify
```

- yopass fails → abort before any VM change. Nothing changed; no operator lockout.
- set fails after yopass succeeded → the VM keeps its old credentials (no lockout), and the
  tool returns failure **without** `yopass_url`, so the unapplied link is never relayed or
  emailed.
- libcrypt unavailable at verify time → the receipt states `verification_unavailable`, never
  a silent pass (§V.62).

## Configuration

Set via environment (pydantic-settings, no prefix). See `.env.example`; fields live in
`core/config.py`.

| Env var | Setting | Default | Notes |
|---------|---------|---------|-------|
| `YOPASS_BASE_URL` | `yopass_base_url` | _(unset)_ | Base URL of the yopass instance. Absent → tool error `yopass_not_configured`; the app still boots. |
| `YOPASS_SECRET_EXPIRATION_SECONDS` | `yopass_secret_expiration_seconds` | `604800` (7 days) | How long the stored secret lives. |
| `YOPASS_ONE_TIME` | `yopass_one_time` | `false` | `false` ⇒ the link is multi-fetch within the expiry window — chosen so real-customer email-open latency does not burn the secret on a preview fetch. |
| `SECRET_PASSWORD_LENGTH` | `secret_password_length` | `24` | Generated password length. Charset = letters + digits + safe symbols; never space, quote, backtick, or backslash (they break shell / cloud-init quoting). |

`NOA_SECRET_ENCRYPTION_KEY` is a different mechanism: Fernet encryption of stored server
credentials at rest (C7, §V.48, §V.52). It has nothing to do with yopass delivery.

## Residual risk (accepted)

The yopass URL contains the decryption key in its fragment, so the URL itself is a secret. The
LLM relays it, so it lands in LibreChat's stored transcript (§V.26 — assume a LibreChat admin
can read tool-result artifacts). Accepted because the deployment uses a local self-hosted
model on owned hardware. With `one_time=false` the link is reusable until expiry, bounded by
the 7-day expiration and mitigated by manual operator email delivery.

## Consumers

| Tool | Risk | Delivery |
|------|------|----------|
| `proxmox_reset_vm_password` (§T.27) | CHANGE | yopass link for the reset cloud-init password |

Approval and receipt surfaces render the password field as
`generated (hidden), delivered via yopass` — never the plaintext.

## Code references

- Password generation: `core/secrets/password.py`
- yopass client helper: `core/secrets/yopass.py`
- Fernet cipher for credentials at rest: `core/secrets/crypto.py`
- Error taxonomy (`yopass_not_configured`, `yopass_store_failed`): `core/secrets/errors.py`
- Config fields: `core/config.py`
- Tests: `apps/api/tests/test_yopass_store.py`, `test_secret_password.py`,
  `test_secret_cipher.py`
