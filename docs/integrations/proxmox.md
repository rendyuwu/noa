# Proxmox Integration Reference

Canonical reference for how NOA talks to Proxmox VE. Ported from `noa-old` branch `MCP` with the
integration layer itself (§T.17, C13), scoped to what exists here today.

Update this file whenever a Proxmox-backed feature or upstream call changes — in the same commit
as the code.

## One transport

Unlike WHM (§T.16), Proxmox exposes everything NOA needs over its HTTP API, so there is no SSH
path in this layer and a `proxmox_servers` row carries **no SSH credentials**: `base_url`,
`api_token_id`, `api_token_secret`, `verify_ssl` (I.ext).

## Connection base

- Base URL: `https://<pve-host>:8006`. Any HTTPS host+port; the client appends `/api2/json/...`
  itself, so the stored value carries no path, query or fragment.
- Auth header: `Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>`.
- `verify_ssl` defaults **off** for Proxmox (unlike WHM), because Proxmox ships a self-signed
  certificate by default.

## Upstream Proxmox calls used by code

```bash
# Credential probe — the cheapest authenticated call; used by admin validate.
curl -k -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/version'

# VM runtime state, config (with digest), and staged-but-unapplied changes. Exact node required.
curl -k -H 'Authorization: …' 'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/status/current'
curl -k -H 'Authorization: …' 'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'
curl -k -H 'Authorization: …' 'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/pending'

# Cloud-init: stored key/values, and the rendered user-data document.
curl -k -H 'Authorization: …' 'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit'
curl -k -H 'Authorization: …' 'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit/dump?type=user'

# Set the cloud-init password. `cipassword` only — `ciuser` is deliberately untouched.
curl -k -X POST -H 'Authorization: …' \
  --data-urlencode 'cipassword=<new-password>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'

# Regenerate the cloud-init drive so the change reaches the guest.
curl -k -X PUT -H 'Authorization: …' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit'

# Write one NIC line under the digest read a moment earlier.
curl -k -X POST -H 'Authorization: …' \
  --data-urlencode 'digest=<digest-from-config>' \
  --data-urlencode '<net-key>=<updated-net-config>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'

# Poll an async task.
curl -k -H 'Authorization: …' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/tasks/<upid>/status'
```

`<new-password>` is generated server-side by `core.secrets.password` and delivered out-of-band
through yopass. It is never a tool argument and never a result field — only `yopass_url` crosses
back (C15, §V.49, and `docs/integrations/yopass.md`).

**Exact node, always.** Every VM path names the node. NOA asks the operator for it rather than
sweeping cluster-wide discovery endpoints — the `/cluster/resources` scan is expensive on a large
cluster and its results are exactly the kind of large output §V.64 exists to keep out of context.

## Four failure shapes, one result shape

Proxmox reports a refusal in the status line, in a top-level `errors` object, in an `errors`
object nested under `data`, or as prose in `message` — depending on which layer refused. Every
call funnels through `ProxmoxClient._request_json`, which normalises all of them to
`{"ok": false, "error_code": ..., "message": ...}`:

| Condition | `error_code` |
|---|---|
| connect / read deadline | `timeout` |
| DNS, refused, reset, TLS | `request_failed` |
| HTTP 401 | `auth_failed` |
| HTTP 403 | `permission_denied` |
| `errors` payload, or `message`, at either level | `proxmox_api_error` |
| either of those worded as a digest conflict | `digest_mismatch` |
| any other HTTP ≥ 400 | `http_error` |
| body not JSON, or not an object | `invalid_response` |

These strings are stable; tools branch on them and §V.19 sanitises one layer up.

Two splits are load-bearing:

- **`permission_denied` ≠ `auth_failed`.** A bad token is fixed in NOA's server row; a missing
  privilege is fixed in Proxmox. The message carries Proxmox's own text —
  `Permission check failed (/sdn/zones/localnetwork/vmbr2/110, SDN.Use)` names the exact ACL path
  and privilege to grant, which is the whole fix.
- **`digest_mismatch` ≠ `proxmox_api_error`.** It is the only code that means *re-read*, not
  *retry*. Matching needs the word "digest" **and** a change word ("mismatch", "modified",
  "changed"): "digest" alone appears in ordinary config text, and misreading one as a conflict
  would send a tool into a pointless fresh preflight.

## The digest is a compare-and-set token

`get_qemu_config` returns `{"config": {...}, "digest": "..."}` and **fails closed** with
`invalid_response` if Proxmox answers a config without one.

That is not defensive noise. A NIC change is read-digest-then-write: `update_qemu_config` sends
the digest in the same body as the change it guards, and Proxmox refuses the write if the config
moved in between. A digest-free config handed back to the caller would turn that fail-closed CAS
into a blind overwrite of whatever another operator just did (§V.62's fail-closed discipline).

## Sync or async, same endpoint

A Proxmox config write answers either with a UPID string to poll, or with `data: null` because it
finished inline. `_request_json_task` reports which:

```python
{"ok": True, "message": "ok", "upid": "UPID:pve1:…", "synchronous": False}
{"ok": True, "message": "ok", "upid": None, "synchronous": True}
```

so no caller has to read "it already finished" off a missing UPID. `get_task_status` returns
`task_status` and `task_exit_status` as `None` when absent rather than `""` — terminality is "an
exit status is present while the task is no longer running", and an empty string would read as
present.

One asymmetry, ported deliberately: `regenerate_qemu_cloudinit` returns a UPID but does **not**
go through the task wrapper, so its result carries `data`, not `upid`. On `noa-old` the reset
workflow verified by re-reading cloud-init rather than by waiting on that task. Whether to poll
it is §T.27's call, not this layer's.

## Connection lifetime

`ProxmoxClient` holds one `httpx.AsyncClient` and shares it across calls, behind an
`asyncio.Semaphore(5)` — unlike `WHMClient`, which opens one per request. Task polling makes many
sequential calls per workflow, so the TLS handshake would otherwise be paid on each one, and the
semaphore caps what a single workflow can aim at one node.

Use `async with`:

```python
async with build_proxmox_client_from_creds(..., cipher=cipher) as client:
    result = await client.get_qemu_config(node, vmid)
```

`close()` also exists and is idempotent. On `noa-old` it was **never called** from anywhere, so
every tool call left behind a socket and a TLS context; `async with` is the shape that cannot.

## Credentials at rest

`api_token_secret` is Fernet-encrypted with the `enc:v1:fernet:` prefix under
`NOA_SECRET_ENCRYPTION_KEY` (C7, §V.48, §V.52). Decryption happens in exactly one place,
`build_proxmox_client_from_creds`, taking an injected `SecretCipher` — `noa-old` decrypted in
three (admin validate service, tool-layer `client_for_server`, workflow postflight). It uses
`maybe_decrypt_text`, so a row written before encryption still works, which is what lets the
column be migrated in place.

## Not built yet

| Surface | Task |
|---|---|
| MCP tool `proxmox_reset_vm_password` (CHANGE) — yopass-delivered, crypt-verified | §T.27 |
| MCP tool `proxmox_vm_nic(action: enable\|disable)` (CHANGE) — single tool, enum action | §T.28 |
| Internal reads `proxmox_get_vm_status_current` / `_config` / `_pending`, `proxmox_list_servers`, `proxmox_validate_server` | I.mcp |
| Admin routes `/admin/proxmox/servers…` + `POST …/validate` | §T.54 |
| `resolve_proxmox_server_ref` (UUID / name / host → candidates on ambiguity, C10/§V.18) | tool layer |

**Never implement** (C22, management policy — not a technical limit):
`proxmox_move_vms_between_pools`, `proxmox_preflight_move_vms_between_pools`,
`proxmox_get_user_by_email`. The `ProxmoxClient` methods behind them — `get_user`, `get_pool`,
`get_effective_permissions`, `add_vms_to_pool`, `remove_vms_from_pool` — are deliberately absent,
so the capability is not one line from exposure. Re-adding any of them is an owner decision.

The pool-move workflow on `noa-old` was the "change email PIC" flow (move VMs between pools,
never touch a Proxmox user's email field). It is documented here only so a future reader knows
what the missing methods were for.

## Caveats for the tools that will use this

- A cloud-init password change is **not** an immediate in-guest reset. The guest may need a
  restart or a stop/start cycle before it takes effect, and §T.27's receipt has to say so.
- NIC selection: when a VM has exactly one NIC, preflight may infer it; otherwise it must return
  the NIC list and refuse to guess (C10, §V.18). The CHANGE then uses the concrete `netN` key.
- libcrypt absent ⇒ the reset receipt states `verification_unavailable`, ⊥ a silent pass.
  Verification-unavailable ≠ verified (§V.62, §T.69).

## Code references

- Package overview: `core/integrations/proxmox/__init__.py`
- API client + credential factory: `core/integrations/proxmox/client.py`
- Tests: `apps/api/tests/test_proxmox_client.py` (failure classification, digest, credentials,
  lifecycle), `test_proxmox_client_endpoints.py` (literal request contracts)
- Secret delivery for §T.27: `docs/integrations/yopass.md`
