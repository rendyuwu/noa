# yopass Secret Delivery Reference

Canonical reference for how NOA delivers generated secrets to operators via
[yopass](https://github.com/jhaals/yopass). Ported from `noa-old` branch `MCP` with the
helpers themselves.

Today the only planned consumer is the Proxmox cloud-init password reset tool,
`proxmox_reset_vm_password`. The helpers are shared and internal, not welded to that
tool — DECISIONS section 8.5.

Update this file whenever the secret-delivery pattern, config, or consumers change, in the
same commit as the code.

## Why server-side delivery (`noa-old` GH #91)

The cloud-init password reset previously accepted the new password as an LLM tool argument
(`new_password`) and echoed it back in the result. That put plaintext on the LLM boundary: in
the prompt, in the model context, in the stored transcript, and potentially in the logs.

The internal-delivery pattern removes the password from the LLM boundary entirely:

- The password is **generated internally** inside the tool's `execute()` call stack
  (`core/secrets/password.py`), not supplied by the model.
- It is **delivered out-of-band** via a yopass share link. Only the link (`yopass_url`)
  crosses back across the LLM boundary.
- The operator copies the link and emails it to the customer. **NOA never sends email.**

What crosses the LLM boundary:

| Direction | Value |
|-----------|-------|
| in | `server_ref`, `node`, `vmid`, `username` |
| out | `status`, `yopass_url`, verification metadata (no plaintext) |

No `reason` in either column: the reason is typed by the operator in the approval card and
never enters a tool schema.

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
   `{message: <ciphertext>, expiration: <seconds>, one_time: <bool>}`. The ciphertext field is
   `message` — yopass decodes the request into the same struct it serves back, and drops an
   unknown key without complaint, so naming it `secret` still answers HTTP 200 with a fresh
   uuid while storing the empty string. That is a *successful* delivery of nothing, and the
   caller goes on to change the VM password behind it.
4. The response `{message: <uuid>}` becomes the share URL:
   `<YOPASS_BASE_URL>/#/s/<uuid>/<passphrase>`. The frontend route is
   `/:format/:key/:password`, format `s` for a secret.

The decryption passphrase rides in the **URL fragment** (after `#`). Browsers never send the
fragment to the server, so the yopass instance can decrypt nothing — only whoever holds the
full link can.

### Deliver-first ordering

The reset tool stores the secret **before** mutating the VM:

```
generate password → yopass store (encrypt + POST → URL)
                  → set cipassword → regenerate cloud-init → verify
```

- yopass fails → abort before any VM change. Nothing changed; no operator lockout.
- set fails after yopass succeeded → the VM keeps its old credentials (no lockout), and the
  tool returns failure **without** `yopass_url`, so the unapplied link is never relayed or
  emailed.
- libcrypt unavailable at verify time → the receipt states `verification_unavailable`, never
  a silent pass.

## Configuration

Set via environment (pydantic-settings, no prefix). See `.env.example`; fields live in
`core/config.py`.

| Env var | Setting | Default | Notes |
|---------|---------|---------|-------|
| `YOPASS_BASE_URL` | `yopass_base_url` | _(unset)_ | Base URL of the yopass instance. Absent → tool error `yopass_not_configured`; the app still boots. |
| `YOPASS_SECRET_EXPIRATION_SECONDS` | `yopass_secret_expiration_seconds` | `604800` (7 days) | How long the stored secret lives. |
| `YOPASS_ONE_TIME` | `yopass_one_time` | `false` | `false` means the link is multi-fetch within the expiry window — chosen so real-customer email-open latency does not burn the secret on a preview fetch. |
| `SECRET_PASSWORD_LENGTH` | `secret_password_length` | `24` | Generated password length. Charset = letters + digits + safe symbols; never space, quote, backtick, or backslash (they break shell / cloud-init quoting). |

`NOA_SECRET_ENCRYPTION_KEY` is a different mechanism: Fernet encryption of stored server
credentials at rest. It has nothing to do with yopass delivery.

### What the operator surfaces may say about a delivered link

Both rows above are read by the approval card as well as by the store call, and the rule is that
**no surface spells either value into a sentence**. The reset runner shipped `it opens once` for
as long as `YOPASS_ONE_TIME` has been `false`, which was simply untrue, and replacing it with a
literal `7 days` would have been the same defect with a new number — the expiry is a setting too,
so a spelled-out duration is true for today's deployment and silently false the day someone
overrides it. The runner composes that clause from both settings instead
(`apps/api/src/noa_api/mcp_tools/proxmox_password_runner.py`), so a one-time deployment gets the
one-open wording back because the flag says so, and everyone else is told how long the link
actually lives. The duration is stated in the largest unit that divides the configured expiry
exactly, so an hour reads as an hour rather than rounding away to nothing.

The copied ticket block says neither. It is rendered in the web app, which cannot read these
variables at all, so it states only what holds under both settings: the link is left out of the
paste because whoever reads the ticket can use it. **The link stays on the card and never in the
copied block**, and with `one_time=false` the reason is the stronger one — a fetchable link in a
ticket hands the password to every reader of that ticket until it expires, rather than merely
being spent by the first.

## Residual risk (accepted)

The yopass URL contains the decryption key in its fragment, so the URL itself is a secret. The
LLM relays it, so it lands in LibreChat's stored transcript (assume a LibreChat admin
can read tool-result artifacts). Accepted because the deployment uses a local self-hosted
model on owned hardware. With `one_time=false` the link is reusable until expiry, bounded by
the 7-day expiration and mitigated by manual operator email delivery.

## Consumers

| Tool | Risk | Delivery |
|------|------|----------|
| `proxmox_reset_vm_password` | CHANGE | yopass link for the reset cloud-init password |

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
