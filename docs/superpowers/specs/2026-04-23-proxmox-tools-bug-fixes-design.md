# Proxmox Tools Bug Fixes — Design Spec

**Date:** 2026-04-23
**Scope:** Fix all 23 issues found in the Proxmox tools audit — bugs, security gaps, DRY violations, workflow/prompt concerns, and operational hardening.

---

## Overview

A comprehensive audit of the Proxmox tool stack identified 23 issues across the client layer, tool implementations, workflow templates, system prompt hints, and operational concerns. This spec organizes fixes into 6 layered changesets that follow the dependency graph bottom-up. Each changeset is independently testable.

---

## Changeset 1: Client Layer (`ProxmoxClient`)

**Issues addressed:** #3, #4, #17, #18

### Problem

1. `set_qemu_cloudinit_password` and `update_qemu_config` assume Proxmox always returns a UPID string in `data`. When Proxmox applies changes synchronously, `data` is `null`, causing a false-negative `invalid_response` error even though the change succeeded.
2. Every API call creates and destroys an `httpx.AsyncClient` — no connection pooling, new TLS handshake per request. Multi-step workflows (pool moves: 5-10 calls) pay significant latency.
3. All operations share a single 20-second timeout regardless of operation type.

### Design

**File:** `apps/api/src/noa_api/proxmox/integrations/client.py`

#### 1a. Persistent `httpx.AsyncClient`

Convert `ProxmoxClient` to manage a shared `httpx.AsyncClient` instance:

```python
class ProxmoxClient:
    def __init__(self, *, base_url, api_token_id, api_token_secret, verify_ssl,
                 timeout_seconds=20.0, transport=None):
        self._base_url = base_url.rstrip("/")
        self._api_token_id = api_token_id
        self._api_token_secret = api_token_secret
        self._verify_ssl = verify_ssl
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=self._verify_ssl,
                timeout=self._timeout_seconds,
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
```

- `_request_json` uses `self._get_client()` instead of `async with httpx.AsyncClient(...)`.
- Per-request timeout overrides (from 1c) are passed to `client.request(..., timeout=effective_timeout)`, not set on the client instance. The client-level timeout serves as the default.
- Callers that create short-lived clients (tool functions) don't need to call `close()` — the client will be GC'd. But long-lived clients can call `close()` explicitly.
- The `transport` parameter (used in tests) continues to work.

#### 1b. Graceful UPID handling

Add a helper method `_request_json_task` for endpoints that may return a UPID:

```python
async def _request_json_task(self, method, path, *, form_data=None,
                              query_params=None) -> dict[str, object]:
    result = await self._request_json(method, path, form_data=form_data,
                                       query_params=query_params)
    if result.get("ok") is not True:
        return result

    upid = _normalized_text(result.get("data"))
    return {
        "ok": True,
        "message": "ok",
        "upid": upid,  # None means synchronous success
        "synchronous": upid is None,
    }
```

Update callers:
- `set_qemu_cloudinit_password` → use `_request_json_task`
- `update_qemu_config` → use `_request_json_task` (replace existing UPID extraction)

#### 1c. Per-method timeout overrides

Add an optional `timeout` parameter to `_request_json`:

```python
async def _request_json(self, method, path, *, form_data=None,
                         query_params=None, timeout=None):
    effective_timeout = timeout if timeout is not None else self._timeout_seconds
    # Use effective_timeout for this specific request
```

Apply longer timeouts to:
- `regenerate_qemu_cloudinit`: 30s (cloud-init regeneration can be slow)
- `add_vms_to_pool` / `remove_vms_from_pool`: 30s

### Test impact

- Existing tests that mock `httpx.AsyncClient` via `transport` continue to work unchanged.
- Add tests for `_request_json_task` with `null` data (synchronous success case).
- Add tests for the persistent client lifecycle.

---

## Changeset 2: Shared Utilities Extraction

**Issues addressed:** #5, #12

### Problem

1. `_normalized_text()`, `_resolution_error()`, `_client_for_server()`, `_upstream_error()` are copy-pasted across 5 tool modules. Divergence risk.
2. `_load_crypt_lib()` uses `None` as both "never tried" and "tried and failed" — re-attempts all candidates on every call after failure.

### Design

#### 2a. Shared module

**New file:** `apps/api/src/noa_api/proxmox/tools/_shared.py`

Move these functions here (single source of truth):
- `_normalized_text(value: object) -> str | None`
- `_resolution_error(result: Any) -> dict[str, object]`
- `_client_for_server(server: Any) -> ProxmoxClient`
- `_upstream_error(result: dict[str, object], *, fallback_message: str) -> dict[str, object]`

Update all 5 tool modules to import from `_shared` and delete their local copies.

#### 2b. Crypt library sentinel

**File:** `apps/api/src/noa_api/proxmox/tools/_cloudinit_passwords.py`

```python
_NOT_LOADED = object()
_CRYPT_LIB: CDLL | None | object = _NOT_LOADED

def _load_crypt_lib() -> CDLL | None:
    global _CRYPT_LIB
    if _CRYPT_LIB is not _NOT_LOADED:
        return _CRYPT_LIB  # type: ignore[return-value]
    # ... loading logic ...
    # On failure:
    _CRYPT_LIB = None
    return None
```

This caches the failure result and avoids re-scanning library candidates on every call.

### Test impact

- No behavioral change — purely structural. Existing tests pass unchanged.
- Add a unit test for `_load_crypt_lib` sentinel behavior.

---

## Changeset 3: Preflight Server ID Resolution

**Issues addressed:** #1

### Problem

`resolve_requested_server_id()` in `preflight_validation.py` only resolves WHM servers. For all Proxmox CHANGE tools, `requested_server_id` is always `None`, making preflight matching fall back to fragile string comparison.

### Design

**File:** `apps/api/src/noa_api/core/workflows/preflight_validation.py`

```python
async def resolve_requested_server_id(
    *, args: dict[str, object], session: AsyncSession | None
) -> str | None:
    requested_server_ref = _normalized_text(args.get("server_ref"))
    if requested_server_ref is None or session is None:
        return None
    if not hasattr(session, "execute"):
        return None

    # Try WHM first (existing behavior)
    whm_repo = SQLWHMServerRepository(session)
    whm_resolution = await resolve_whm_server_ref(requested_server_ref, repo=whm_repo)
    if whm_resolution.ok and whm_resolution.server_id is not None:
        return str(whm_resolution.server_id)

    # Try Proxmox
    from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
    from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository

    proxmox_repo = SQLProxmoxServerRepository(session)
    proxmox_resolution = await resolve_proxmox_server_ref(
        requested_server_ref, repo=proxmox_repo
    )
    if proxmox_resolution.ok and proxmox_resolution.server_id is not None:
        return str(proxmox_resolution.server_id)

    return None
```

Import is deferred (inside function body) to avoid circular imports, matching the existing pattern in the codebase.

### Test impact

- Add a test that verifies `resolve_requested_server_id` returns a Proxmox server UUID when the ref matches a Proxmox server.
- Add a test that verifies WHM resolution takes priority when both match.
- Existing WHM tests pass unchanged.

---

## Changeset 4: Tool Implementation Fixes

**Issues addressed:** #6, #7, #8, #9, #10, #16, #19

### Design

#### 4a. Unified sanitization (#6)

**File:** `apps/api/src/noa_api/proxmox/tools/_shared.py`

Move the more thorough `_sanitize_payload` from `cloudinit_tools.py` into `_shared.py` as `sanitize_proxmox_payload`. It handles both:
- `key`-based redaction (pending-changes list format)
- Direct dict-key redaction (config format)

Replace `_sanitize_vm_payload` in `vm_read_tools.py` and `_sanitize_payload` in `cloudinit_tools.py` with imports from `_shared`.

#### 4b. Proxmox userid normalization (#7)

**File:** `apps/api/src/noa_api/proxmox/tools/pool_tools.py`

The Proxmox convention is `user@realm`. An email like `user@example.com` becomes `user@example.com@pve`. This is correct. The risk is someone passing `user@pve` directly, which would become `user@pve@pve`. Guard against double-suffix:

```python
def _normalize_proxmox_userid(email: str) -> str:
    normalized = email.strip()
    if normalized.endswith("@pve"):
        return normalized
    return f"{normalized}@pve"
```

#### 4c. TOCTOU mitigation in pool move (#8)

**File:** `apps/api/src/noa_api/proxmox/tools/pool_tools.py`

In `proxmox_move_vms_between_pools`, after the preflight passes and before calling `add_vms_to_pool`, re-fetch the source pool and verify VMIDs are still present:

```python
# Re-verify source pool membership immediately before mutation
source_pool_check = await client.get_pool(normalized_source_pool)
if source_pool_check.get("ok") is not True:
    return _upstream_error(source_pool_check, fallback_message="...")
try:
    current_source_vmids = _pool_result_vmids(source_pool_check)
except ValueError:
    return {"ok": False, "error_code": "invalid_response", "message": "..."}
if not all(vmid in current_source_vmids for vmid in vmids):
    return {
        "ok": False,
        "error_code": "source_pool_changed",
        "message": "One or more VMIDs are no longer in the source pool. Run preflight again.",
    }
```

#### 4d. Node normalization in `_fetch_vm_nic_state` (#9)

**File:** `apps/api/src/noa_api/proxmox/tools/nic_tools.py`

Add `node = node.strip()` at the top of `_fetch_vm_nic_state`.

#### 4e. Single server resolution in NIC change (#10)

**File:** `apps/api/src/noa_api/proxmox/tools/nic_tools.py`

Refactor `_change_vm_nic_link_state`:
1. Resolve the server once at the top of the function.
2. Pass the client to a new `_fetch_vm_nic_state_with_client` that accepts an already-resolved client instead of resolving internally.
3. `_fetch_vm_nic_state` (the public entry point) continues to resolve internally for the preflight use case.

```python
async def _fetch_vm_nic_state_with_client(
    *, client: ProxmoxClient, server_id: str, node: str, vmid: int, net: str | None
) -> dict[str, object]:
    # Same logic as _fetch_vm_nic_state but uses provided client/server_id
    ...

async def _fetch_vm_nic_state(
    *, session, server_ref, node, vmid, net
) -> dict[str, object]:
    # Resolve server, then delegate to _fetch_vm_nic_state_with_client
    ...

async def _change_vm_nic_link_state(
    *, session, server_ref, node, vmid, net, digest, disabled
) -> dict[str, object]:
    # Resolve once
    repo = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)
    client = _client_for_server(resolution.server)
    server_id = str(resolution.server_id)

    # Use client for both state fetch and mutation
    state = await _fetch_vm_nic_state_with_client(
        client=client, server_id=server_id, node=node, vmid=vmid, net=net
    )
    # ... rest of logic using same client ...
```

#### 4f. Defensive vmid validation (#16)

Add to each tool entry point that accepts `vmid`:

```python
if not isinstance(vmid, int) or isinstance(vmid, bool) or vmid < 1:
    return {"ok": False, "error_code": "invalid_request", "message": "vmid must be a positive integer"}
```

Apply to: `proxmox_get_vm_status_current`, `proxmox_get_vm_config`, `proxmox_get_vm_pending`, `proxmox_preflight_vm_cloudinit_password_reset`, `proxmox_reset_vm_cloudinit_password`, `proxmox_preflight_vm_nic_toggle`, `proxmox_disable_vm_nic`, `proxmox_enable_vm_nic`.

Put this in `_shared.py` as `validate_vmid(vmid) -> dict | None` to avoid repetition.

#### 4g. Better pool error messages (#19)

**File:** `apps/api/src/noa_api/proxmox/tools/pool_tools.py`

Replace generic `raise ValueError("invalid pool payload")` with specific messages:

```python
def _pool_members(result: dict[str, object]) -> list[dict[str, object]]:
    data = result.get("data")
    if not isinstance(data, list):
        raise ValueError("pool payload 'data' must be a list")
    members: list[dict[str, object]] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"pool payload 'data[{i}]' must be a dict")
        entry_members = entry.get("members")
        if not isinstance(entry_members, list):
            raise ValueError(f"pool payload 'data[{i}].members' must be a list")
        for j, member in enumerate(entry_members):
            if not isinstance(member, dict):
                raise ValueError(f"pool payload 'data[{i}].members[{j}]' must be a dict")
            members.append(member)
    return members
```

### Test impact

- Add tests for `_normalize_proxmox_userid` edge cases (`user@pve`, `user@example.com`).
- Add test for TOCTOU re-verification in pool move.
- Existing NIC and cloud-init tests updated to use shared imports.

---

## Changeset 5: Workflow & Prompt Fixes

**Issues addressed:** #13, #14, #15, #20, #21, #23

### Design

#### 5a. Generic default reason text (#13)

**File:** `apps/api/src/noa_api/core/workflows/proxmox/common.py`

Change `_reason_step_content` default:

```python
def _reason_step_content(*, action_label, action_verb, reason,
                          missing_reason_text=None):
    if reason is None:
        if missing_reason_text is not None:
            return missing_reason_text
        return (
            "Ask the user for a reason—an osTicket/reference number or a brief "
            f"description—before proceeding with the {action_label} change."
        )
    return f"Reason captured for the {action_label} change: {reason}."
```

#### 5b. Dict-based action labels (#14)

**File:** `apps/api/src/noa_api/core/workflows/proxmox/common.py`

```python
_NIC_ACTION_LABELS: dict[str, dict[str, str]] = {
    "proxmox_enable_vm_nic": {
        "action": "enable VM NIC",
        "approval": "Enable VM NIC",
        "desired_state": "up",
        "verb": "enable",
        "completed": "Enabled",
        "adjective": "enabled",
    },
    "proxmox_disable_vm_nic": {
        "action": "disable VM NIC",
        "approval": "Disable VM NIC",
        "desired_state": "down",
        "verb": "disable",
        "completed": "Disabled",
        "adjective": "disabled",
    },
}

def _action_label(tool_name: str) -> str:
    return _NIC_ACTION_LABELS.get(tool_name, {}).get("action", "change VM NIC")
```

Apply the same pattern to all the label functions.

#### 5c. Postflight empty password handling (#15)

**File:** `apps/api/src/noa_api/core/workflows/proxmox/postflight.py`

```python
async def _cloudinit_postflight_result(
    *, client, node, vmid, new_password=None
):
    verification_result = await _wait_for_cloudinit_verification(
        client=client, node=node, vmid=vmid, new_password=new_password,
    )
    ...

async def _wait_for_cloudinit_verification(
    *, client, node, vmid, new_password,
):
    # ... existing checks ...
    if new_password is not None and not cloudinit_dump_matches_password(
        dump_result.get("data"), new_password
    ):
        return {"ok": False, "error_code": "postflight_failed", ...}
    # If new_password is None, skip the hash match — we can still confirm
    # cloud-init state changed via the other checks
```

#### 5d. Document UUID-as-hostname edge case (#20)

**File:** `apps/api/src/noa_api/proxmox/server_ref.py`

Add a comment at line 53:

```python
# UUID lookup takes priority over hostname lookup. If a hostname happens
# to be a valid UUID string, it will be treated as a server ID. This is
# the correct precedence — UUIDs are unambiguous identifiers.
```

No functional change.

#### 5e. Password generation prompt hint (#21)

**File:** `apps/api/src/noa_api/core/tools/definitions/proxmox.py`

Update `proxmox_reset_vm_cloudinit_password` prompt hints:

```python
prompt_hints=(
    "Run `proxmox_preflight_vm_cloudinit_password_reset` first and reuse the same server_ref, node, and vmid.",
    "Generate a strong random password (16+ chars, mixed case, digits, symbols) unless the user provides a specific password.",
    "Idempotent result contract: returns `status` `changed` only after postflight confirms the cloud-init password reset.",
),
```

#### 5f. Pool permission validation (#23)

**File:** `apps/api/src/noa_api/proxmox/tools/pool_tools.py`

```python
_REQUIRED_POOL_PERMISSIONS = {"VM.Allocate", "Pool.Allocate", "Pool.Audit"}

def _meaningful_permission_entries(
    result: dict[str, object], path: str
) -> dict[str, object] | None:
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    permissions = data.get(path)
    if not isinstance(permissions, dict) or not permissions:
        return None
    # Check that at least one relevant permission is granted
    granted = {
        key for key, value in permissions.items()
        if value == 1 or value is True
    }
    if not granted.intersection(_REQUIRED_POOL_PERMISSIONS):
        return None
    return permissions
```

### Test impact

- Update workflow template tests for new label dict behavior.
- Add test for `_meaningful_permission_entries` with specific permission checks.
- Add test for postflight with `None` password.

---

## Changeset 6: Operational Hardening

**Issues addressed:** #22

### Design

**File:** `apps/api/src/noa_api/proxmox/integrations/client.py`

Add a per-client concurrency semaphore:

```python
import asyncio

class ProxmoxClient:
    def __init__(self, ..., max_concurrent_requests: int = 5):
        ...
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def _request_json(self, method, path, **kwargs):
        async with self._semaphore:
            # ... existing request logic ...
```

This limits concurrent requests per `ProxmoxClient` instance (i.e., per Proxmox server). Since each tool call creates its own client, this primarily protects against concurrent tool executions targeting the same server.

Verify existing polling delays are reasonable:
- `_TASK_POLL_DELAY_SECONDS = 0.1` (NIC/cloud-init) — OK
- `_VERIFICATION_POLL_DELAY_SECONDS = 0.25` (cloud-init verification) — OK

### Test impact

- Add a test that verifies the semaphore limits concurrent requests.

---

## Issue #11 (Password in memory) — Documentation Only (included in CS4)

Add a code comment in `cloudinit_tools.py` at the `_wait_for_cloudinit_verification` call:

```python
# Security note: new_password is held in memory during the polling loop
# (up to ~2.5s) because crypt verification requires the plaintext.
# This is unavoidable for the current verification approach.
```

No functional change. Applied as part of Changeset 4 since it touches `cloudinit_tools.py`.

---

## Changeset Dependency Order

```
Changeset 1 (Client)
    ↓
Changeset 2 (Shared utilities) — depends on new client API from CS1
    ↓
Changeset 3 (Preflight resolution) — independent, can parallel with CS2
    ↓
Changeset 4 (Tool fixes) — depends on CS1 + CS2
    ↓
Changeset 5 (Workflow/prompt) — depends on CS4 for shared imports
    ↓
Changeset 6 (Hardening) — depends on CS1 for client changes
```

Parallelizable: CS2 and CS3 can be done in parallel after CS1.

---

## Files Modified

| Changeset | Files |
|-----------|-------|
| CS1 | `proxmox/integrations/client.py` |
| CS2 | New: `proxmox/tools/_shared.py`. Modified: `proxmox/tools/vm_read_tools.py`, `cloudinit_tools.py`, `nic_tools.py`, `pool_tools.py`, `read_tools.py`, `_cloudinit_passwords.py` |
| CS3 | `core/workflows/preflight_validation.py` |
| CS4 | `proxmox/tools/_shared.py`, `pool_tools.py`, `nic_tools.py`, `vm_read_tools.py`, `cloudinit_tools.py` |
| CS5 | `core/workflows/proxmox/common.py`, `core/workflows/proxmox/postflight.py`, `core/tools/definitions/proxmox.py`, `proxmox/tools/pool_tools.py`, `proxmox/server_ref.py` |
| CS6 | `proxmox/integrations/client.py` |

## Testing Strategy

- All existing tests must continue to pass after each changeset.
- New tests are added alongside each changeset (not deferred).
- Run `uv run pytest -q` after each changeset to verify.
- Run `uv run ruff check src tests && uv run ruff format src tests` for lint compliance.
