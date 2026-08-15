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

**Whose read the digest comes from is a decision, and §T.28 made it**: the runner's own, taken
milliseconds before the write — not the one the approval card was built from minutes earlier. See
`proxmox_vm_nic` below for what a gate-time digest would cost.

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
workflow verified by re-reading cloud-init rather than by waiting on that task. **§T.27 answered
that question and kept the asymmetry**: the reset polls the *config write*'s task and then
re-reads cloud-init until the crypt compare agrees, which is a stronger check than waiting on the
regeneration task would have been.

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

## `proxmox_reset_vm_password` (§T.27)

Built 2026-08-15. Two halves either side of the approval gate (§V.22):

- `noa_api/mcp_tools/proxmox_password.py` — the tool. Runs the preflight in-process (C9, §V.17),
  opens an `action_requests` row, executes nothing.
- `noa_api/mcp_tools/proxmox_password_runner.py` — the runner, reached only from
  `core.approvals.execution` once an operator approved.

**The order is the safety property** (§V.62): generate → deliver to yopass → write `cipassword` →
poll the task → regenerate the drive → crypt-verify. A delivery failure aborts with the VM
untouched.

**Which failures hand over the yopass link.** The rule is: the link goes out exactly when NOA
cannot rule out that the new password reached the VM.

| Outcome | Link |
|---|---|
| yopass failed | no link — nothing was stored, nothing was written |
| Proxmox refused the config write, or its task exited non-`OK` | **no link** — the VM never took the password, and the old credentials still work (§V.62's named residual case) |
| regeneration failed, task poll timed out, verification unavailable, verification mismatched | **link ships** — the write was accepted, so withholding the only copy of a possibly-live credential is a lockout NOA created |

`noa-old` withheld the link on the verification branches. That is the one behavioural correction
to its flow beyond §T.69.

**Refused, not gated**: a VM whose `ciuser` is set to somebody other than the requested
`username`. The password would change `ciuser`'s credentials while the delivered blob named
another account. A VM with **no** `ciuser` is allowed — Proxmox applies `cipassword` to the
image's default account — and the card shows `ciuser: null` so the operator sees which case it is.

**What the result carries**: the server, node, vmid, username, a verdict, and `yopass_url`. It
carries nothing read out of the cloud-init dump. That payload becomes `tool_runs.result_summary`
and `noa_get_action_result` hands the summary to a model, so the crypt hash of the password NOA
just set would otherwise reach the LLM through §V.45's audit row (C15, §V.49).

## `proxmox_vm_nic` (CHANGE, §T.28)

One tool with an `action` enum where `noa-old` had `proxmox_enable_vm_nic` and
`proxmox_disable_vm_nic` (DECISIONS §9). Two halves again:

- `core/integrations/proxmox/nic.py` — the `netN` codec. Here rather than in `mcp_tools/` because
  the grammar is Proxmox's, and because both halves need it (§V.66).
- `noa_api/mcp_tools/proxmox_nic.py` — the tool. Preflight in-process, opens the row, executes
  nothing.
- `noa_api/mcp_tools/proxmox_nic_runner.py` — the runner, reached only after an approval.

**A rewrite is a whole-line write.** There is no "set the `link_down` field" call: the change is a
write of the entire `netN` value, so a segment the codec drops is a segment deleted from a live
VM. `set_link_down` keeps order, keeps unrecognised segments, and never emits a second
`link_down`. Enabling **removes** the key rather than writing `link_down=0` — equivalent to
Proxmox, and it makes a never-disabled NIC and a re-enabled one read identically. `link_down=0` is
*up*, and a valueless `link_down` is *down*: Proxmox's truthiness, not Python's.

**The digest is the runner's own, not the gate's** — the one place §T.28 departs from `noa-old`
deliberately, and it is why the section above says the CAS window matters. There the digest was a
*tool argument*: a preflight read it and the model handed it back on the change call, seconds
apart. An approval gate turns those seconds into minutes, and a gate-time digest then has two
costs an operator pays. Any unrelated edit to the VM — memory, a disk, a description — answers
`digest_mismatch` **after** they typed a reason and pressed Approve. And writing back the
gate-time `netN` value reverts a bridge or tag edit made in the meantime, silently, as a side
effect of a change that was about the link state.

So the runner re-reads the config, toggles `link_down` on the line **as it is now**, and writes
under the digest from that same read. The compare-and-set window is its own read→write. What the
approval window is checked against is the fact the operator actually approved — the NIC's **link
state**:

| At execution time | Outcome |
|---|---|
| still in the state the card described | the change runs |
| already in the state that was asked for | `no_op` — somebody reached it first, and nothing is written |
| the interface is gone | `net_not_found` — a line that no longer exists cannot be edited |

**Which interface** (C10, §V.18): a VM with exactly one NIC has it inferred, and the card records
`auto_selected: true` so the operator sees that NOA chose. Two or more without `net` named returns
the list as `choices` rather than a pick — disabling the wrong interface is a machine cut off the
network on the strength of a coin flip.

**The postflight asks the change's own question** (§V.97): it re-reads the `netN` line and
recomputes the link state from it, not from the task's exit status, which says only that Proxmox
accepted a write. A read that cannot answer is `changed` + `verified: false` +
`verification: unavailable` — never a bare `false` (§V.62, §V.86: unavailable is not verified, and
not refuted either).

**What the result carries**: the server, node, vmid, net, action, a link state and a verdict. Not
the interface line, its MAC or its bridge — that payload becomes `tool_runs.result_summary` and
`noa_get_action_result` hands it to a model (§V.45, §V.76, §V.26).

## Not built yet

| Surface | Task |
|---|---|
| Internal reads `proxmox_get_vm_status_current` / `_config` / `_pending`, `proxmox_list_servers`, `proxmox_validate_server` | I.mcp |
| Admin routes `/admin/proxmox/servers…` + `POST …/validate` | §T.54 |

**Never implement** (C22, management policy — not a technical limit):
`proxmox_move_vms_between_pools`, `proxmox_preflight_move_vms_between_pools`,
`proxmox_get_user_by_email`. The `ProxmoxClient` methods behind them — `get_user`, `get_pool`,
`get_effective_permissions`, `add_vms_to_pool`, `remove_vms_from_pool` — are deliberately absent,
so the capability is not one line from exposure. Re-adding any of them is an owner decision.

The pool-move workflow on `noa-old` was the "change email PIC" flow (move VMs between pools,
never touch a Proxmox user's email field). It is documented here only so a future reader knows
what the missing methods were for.

## Caveats for the tools that will use this

- A cloud-init password change is **not** an immediate in-guest reset. The guest reads its
  cloud-init drive at next boot, and §T.27's result message says so; the card carries the VM's run
  state so an operator can see whether a restart is owed.
- NIC selection: when a VM has exactly one NIC, preflight may infer it; otherwise it must return
  the NIC list and refuse to guess (C10, §V.18). The CHANGE then uses the concrete `netN` key.
  **Built at §T.28**, and the inference is recorded (`auto_selected`) rather than hidden — an
  operator approving a change to a `netN` key they never typed should be able to see it.
- libcrypt absent ⇒ the reset reports `verification: unavailable`, ⊥ a silent pass and ⊥ a
  mismatch. Verification-unavailable ≠ verified, and ≠ refuted (§V.62, §T.69). Built in
  `core/integrations/proxmox/cloudinit.py`: `CryptVerdict` has three values, and each unavailable
  one carries the cause (`crypt_library_unavailable`, `cloudinit_password_hash_absent`,
  `crypt_refused_stored_hash`). `noa-old` answered a `bool` and collapsed all three into "does not
  match", which reported a change that had in fact succeeded as one to repeat.

## Code references

- Package overview: `core/integrations/proxmox/__init__.py`
- API client + credential factory: `core/integrations/proxmox/client.py`
- Cloud-init reading + the crypt guard: `core/integrations/proxmox/cloudinit.py` (§T.69)
- The `netN` codec: `core/integrations/proxmox/nic.py` (§T.28)
- Server-ref resolution: `core/servers/proxmox_ref.py` over the shared
  `core/servers/reference.py` (§T.27 extracted the policy the three systems share, §V.66)
- Tests: `apps/api/tests/test_proxmox_client.py` (failure classification, digest, credentials,
  lifecycle), `test_proxmox_client_endpoints.py` (literal request contracts),
  `test_proxmox_cloudinit_crypt.py` (§T.69's three verdicts + the negative control),
  `test_proxmox_server_ref.py`, `test_proxmox_server_repository.py`,
  `test_proxmox_tools_reset_password.py` (the tool half),
  `test_proxmox_reset_password_runner.py` (the runner half),
  `test_proxmox_nic_codec.py` (§T.28's grammar, no tool context),
  `test_proxmox_tools_vm_nic.py` (the enum, the ambiguity refusals, the no-op),
  `test_proxmox_nic_runner.py` (the re-read, the lost-update case, the postflight)
- Secret delivery for §T.27: `docs/integrations/yopass.md`
