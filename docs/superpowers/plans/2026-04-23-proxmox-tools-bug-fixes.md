# Proxmox Tools Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 23 issues found in the Proxmox tools audit — client bugs, DRY violations, preflight resolution, tool implementation fixes, workflow/prompt improvements, and operational hardening.

**Architecture:** 6 layered changesets applied bottom-up: client layer first, then shared utilities extraction, preflight resolution fix, tool implementation fixes, workflow/prompt fixes, and operational hardening. Each changeset is independently testable.

**Tech Stack:** Python 3.11+, httpx, SQLAlchemy async, pytest, Pydantic

**Spec:** `docs/superpowers/specs/2026-04-23-proxmox-tools-bug-fixes-design.md`

---

## File Structure

### New files
- `apps/api/src/noa_api/proxmox/tools/_shared.py` — shared utilities extracted from 5 tool modules

### Modified files (by changeset)

| CS | File | Purpose |
|----|------|---------|
| 1 | `apps/api/src/noa_api/proxmox/integrations/client.py` | Persistent client, UPID handling, per-request timeout, semaphore |
| 2 | `apps/api/src/noa_api/proxmox/tools/_shared.py` (new) | Shared `_normalized_text`, `_resolution_error`, `_client_for_server`, `_upstream_error`, `sanitize_proxmox_payload`, `validate_vmid` |
| 2 | `apps/api/src/noa_api/proxmox/tools/vm_read_tools.py` | Import from `_shared`, remove local copies |
| 2 | `apps/api/src/noa_api/proxmox/tools/cloudinit_tools.py` | Import from `_shared`, remove local copies |
| 2 | `apps/api/src/noa_api/proxmox/tools/nic_tools.py` | Import from `_shared`, remove local copies |
| 2 | `apps/api/src/noa_api/proxmox/tools/pool_tools.py` | Import from `_shared`, remove local copies |
| 2 | `apps/api/src/noa_api/proxmox/tools/read_tools.py` | Import from `_shared`, remove local copies |
| 2 | `apps/api/src/noa_api/proxmox/tools/_cloudinit_passwords.py` | Sentinel for crypt lib loading |
| 3 | `apps/api/src/noa_api/core/workflows/preflight_validation.py` | Add Proxmox server resolution |
| 4 | `apps/api/src/noa_api/proxmox/tools/pool_tools.py` | Userid normalization, TOCTOU mitigation, permission check, pool error messages |
| 4 | `apps/api/src/noa_api/proxmox/tools/nic_tools.py` | Node normalization, single server resolution |
| 5 | `apps/api/src/noa_api/core/workflows/proxmox/common.py` | Generic reason text, dict-based labels |
| 5 | `apps/api/src/noa_api/core/workflows/proxmox/postflight.py` | None password handling |
| 5 | `apps/api/src/noa_api/core/tools/definitions/proxmox.py` | Password generation prompt hint |
| 5 | `apps/api/src/noa_api/proxmox/server_ref.py` | UUID-as-hostname comment |

### Test files

| CS | File | Purpose |
|----|------|---------|
| 1 | `apps/api/tests/test_proxmox_client_endpoints.py` | Persistent client, UPID null, per-request timeout |
| 2 | `apps/api/tests/test_proxmox_client_endpoints.py` | Sentinel crypt lib test |
| 3 | `apps/api/tests/test_proxmox_server_ref.py` | Proxmox server ID resolution |
| 4 | `apps/api/tests/test_proxmox_tools_pools.py` | Userid normalization, TOCTOU, permissions |
| 4 | `apps/api/tests/test_proxmox_tools_nic.py` | Node normalization, single resolution |
| 5 | `apps/api/tests/test_proxmox_workflow_templates.py` | Label dict, postflight None password |
| 6 | `apps/api/tests/test_proxmox_client_endpoints.py` | Semaphore concurrency test |

---

## Task 1: Client Layer — Persistent httpx.AsyncClient + UPID Handling + Timeouts

**Files:**
- Modify: `apps/api/src/noa_api/proxmox/integrations/client.py`
- Test: `apps/api/tests/test_proxmox_client_endpoints.py`

- [ ] **Step 1: Write tests for persistent client, null UPID, and per-request timeout**

Add to `apps/api/tests/test_proxmox_client_endpoints.py`:

```python
async def test_proxmox_client_reuses_underlying_httpx_client() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=200, json={"data": {"version": "8.0"}}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    await client.get_version()
    await client.get_version()

    assert call_count == 2
    internal = client._get_client()
    assert internal is client._get_client()


async def test_proxmox_client_close_releases_underlying_client() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200, json={"data": {"version": "8.0"}}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    await client.get_version()
    await client.close()
    # After close, a new internal client is created on next call
    await client.get_version()


async def test_proxmox_client_set_cloudinit_password_handles_null_upid() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200, json={"data": None}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.set_qemu_cloudinit_password("pve1", 101, "s3cret!")

    assert result["ok"] is True
    assert result["upid"] is None
    assert result["synchronous"] is True


async def test_proxmox_client_set_cloudinit_password_returns_upid_when_present() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200, json={"data": "UPID:pve1:task"}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.set_qemu_cloudinit_password("pve1", 101, "s3cret!")

    assert result["ok"] is True
    assert result["upid"] == "UPID:pve1:task"
    assert result["synchronous"] is False


async def test_proxmox_client_update_qemu_config_handles_null_upid() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200, json={"data": None}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.update_qemu_config(
        "pve1", 101, digest="abc", net_key="net0", net_value="virtio=AA:BB:CC"
    )

    assert result["ok"] is True
    assert result["upid"] is None
    assert result["synchronous"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_proxmox_client_endpoints.py -k "reuses or close_releases or null_upid or returns_upid" -v`
Expected: FAIL — `_get_client` does not exist, `set_qemu_cloudinit_password` returns different shape

- [ ] **Step 3: Implement persistent client, UPID handling, per-request timeout, and semaphore**

Replace the full `ProxmoxClient` class in `apps/api/src/noa_api/proxmox/integrations/client.py`:

```python
import asyncio

class ProxmoxClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token_id: str,
        api_token_secret: str,
        verify_ssl: bool,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_concurrent_requests: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token_id = api_token_id
        self._api_token_secret = api_token_secret
        self._verify_ssl = verify_ssl
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

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

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                f"PVEAPIToken={self._api_token_id}={self._api_token_secret}"
            ),
            "Accept": "application/json",
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        form_data: Mapping[str, object] | None = None,
        query_params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> dict[str, object]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        effective_timeout = timeout if timeout is not None else self._timeout_seconds
        try:
            async with self._semaphore:
                client = self._get_client()
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    data=dict(form_data or {}),
                    params=dict(query_params or {}),
                    timeout=effective_timeout,
                )
        except httpx.TimeoutException:
            return {
                "ok": False,
                "error_code": "timeout",
                "message": "Request timed out",
            }
        except httpx.RequestError as exc:
            return {
                "ok": False,
                "error_code": "request_failed",
                "message": f"Request failed: {exc}",
            }

        # ... rest of _request_json unchanged from current implementation ...
        # (auth check, JSON parse, payload_error, status check, return data)
```

Keep the existing response-handling logic (lines 165-209 of current file) unchanged inside `_request_json`.

Add `_request_json_task` method:

```python
    async def _request_json_task(
        self,
        method: str,
        path: str,
        *,
        form_data: Mapping[str, object] | None = None,
        query_params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> dict[str, object]:
        result = await self._request_json(
            method, path, form_data=form_data, query_params=query_params,
            timeout=timeout,
        )
        if result.get("ok") is not True:
            return result

        upid = _normalized_text(result.get("data"))
        return {
            "ok": True,
            "message": "ok",
            "upid": upid,
            "synchronous": upid is None,
        }
```

Update `set_qemu_cloudinit_password` to use `_request_json_task`:

```python
    async def set_qemu_cloudinit_password(
        self, node: str, vmid: int, new_password: str
    ) -> dict[str, object]:
        return await self._request_json_task(
            "POST",
            f"/api2/json/nodes/{node}/qemu/{vmid}/config",
            form_data={"cipassword": new_password},
        )
```

Update `update_qemu_config` to use `_request_json_task`:

```python
    async def update_qemu_config(
        self,
        node: str,
        vmid: int,
        *,
        digest: str,
        net_key: str,
        net_value: str,
    ) -> dict[str, object]:
        return await self._request_json_task(
            "POST",
            f"/api2/json/nodes/{node}/qemu/{vmid}/config",
            form_data={
                "digest": digest,
                net_key: net_value,
            },
        )
```

Update `regenerate_qemu_cloudinit` to use longer timeout:

```python
    async def regenerate_qemu_cloudinit(
        self, node: str, vmid: int
    ) -> dict[str, object]:
        return await self._request_json(
            "PUT",
            f"/api2/json/nodes/{node}/qemu/{vmid}/cloudinit",
            timeout=30.0,
        )
```

Update `add_vms_to_pool` and `remove_vms_from_pool` to use longer timeout:

```python
    async def add_vms_to_pool(self, poolid: str, vmids: list[int]) -> dict[str, object]:
        return await self._request_json(
            "PUT",
            "/api2/json/pools",
            form_data={
                "poolid": poolid,
                "vms": ",".join(str(vmid) for vmid in vmids),
                "allow-move": 1,
            },
            timeout=30.0,
        )

    async def remove_vms_from_pool(
        self, poolid: str, vmids: list[int]
    ) -> dict[str, object]:
        return await self._request_json(
            "PUT",
            "/api2/json/pools",
            form_data={
                "poolid": poolid,
                "vms": ",".join(str(vmid) for vmid in vmids),
                "delete": 1,
            },
            timeout=30.0,
        )
```

- [ ] **Step 4: Update callers that depend on old return shapes**

In `apps/api/src/noa_api/proxmox/tools/cloudinit_tools.py`, update `proxmox_reset_vm_cloudinit_password` to handle the new `set_qemu_cloudinit_password` return shape:

```python
    set_result = await client.set_qemu_cloudinit_password(
        normalized_node, vmid, new_password
    )
    if set_result.get("ok") is not True:
        return _upstream_error(
            set_result,
            fallback_message="Proxmox cloud-init password update failed",
        )

    # Handle synchronous success (no UPID)
    set_upid = _normalized_text(set_result.get("upid"))
    if set_upid is not None:
        task_error = await _wait_for_terminal_task(
            client=client,
            node=normalized_node,
            upid=set_upid,
        )
        if task_error is not None:
            return task_error
```

Remove the old `set_upid is None` error check (lines 300-306 of current file).

In `apps/api/src/noa_api/proxmox/tools/nic_tools.py`, update `_change_vm_nic_link_state` similarly:

```python
    upid = _normalized_text(mutation_result.get("upid"))
    if upid is not None:
        task_result, reached_terminal = await _poll_task_status(
            client=client,
            node=normalized_node,
            upid=upid,
        )
        # ... existing task polling logic ...
    # If upid is None (synchronous), skip polling and go straight to postflight
```

- [ ] **Step 5: Run all existing + new tests**

Run: `uv run pytest -q tests/test_proxmox_client_endpoints.py tests/test_proxmox_client_normalization.py tests/test_proxmox_tools_cloudinit.py tests/test_proxmox_tools_nic.py -v`
Expected: ALL PASS

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add -A && git commit -m "fix(proxmox): persistent httpx client, graceful UPID handling, per-request timeouts, concurrency semaphore

Fixes issues #3, #4, #17, #18, #22 from the Proxmox tools audit."
```

---

## Task 2: Shared Utilities Extraction

**Files:**
- Create: `apps/api/src/noa_api/proxmox/tools/_shared.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/vm_read_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/cloudinit_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/nic_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/pool_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/read_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/_cloudinit_passwords.py`

- [ ] **Step 1: Create `_shared.py` with all shared utilities**

Create `apps/api/src/noa_api/proxmox/tools/_shared.py`:

```python
from __future__ import annotations

from typing import Any

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.proxmox.integrations.client import ProxmoxClient


def normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def client_for_server(server: Any) -> ProxmoxClient:
    return ProxmoxClient(
        base_url=str(getattr(server, "base_url")),
        api_token_id=str(getattr(server, "api_token_id")),
        api_token_secret=maybe_decrypt_text(str(getattr(server, "api_token_secret"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


def upstream_error(
    result: dict[str, object], *, fallback_message: str
) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or fallback_message),
    }


def sanitize_proxmox_payload(payload: object) -> object:
    """Sanitize Proxmox payloads, redacting cipassword in both formats."""
    if isinstance(payload, list):
        return [sanitize_proxmox_payload(item) for item in payload]
    if isinstance(payload, dict):
        lowered_key = payload.get("key")
        if isinstance(lowered_key, str) and lowered_key.strip().lower() == "cipassword":
            return {
                str(key): (
                    "[redacted]"
                    if str(key).strip().lower() == "value"
                    else sanitize_proxmox_payload(item)
                )
                for key, item in payload.items()
            }
        return {
            str(key): sanitize_proxmox_payload(item)
            if isinstance(item, (dict, list))
            else ("[redacted]" if str(key).strip().lower() == "cipassword" else item)
            for key, item in payload.items()
        }
    return redact_sensitive_data(payload)


def validate_vmid(vmid: object) -> dict[str, object] | None:
    """Return an error dict if vmid is invalid, None if valid."""
    if isinstance(vmid, bool) or not isinstance(vmid, int) or vmid < 1:
        return {
            "ok": False,
            "error_code": "invalid_request",
            "message": "vmid must be a positive integer",
        }
    return None
```

- [ ] **Step 2: Update all 5 tool modules to import from `_shared`**

In each of `vm_read_tools.py`, `cloudinit_tools.py`, `nic_tools.py`, `pool_tools.py`, `read_tools.py`:
- Remove local `_normalized_text`, `_resolution_error`, `_client_for_server`, `_upstream_error` definitions
- Add import: `from noa_api.proxmox.tools._shared import normalized_text as _normalized_text, resolution_error as _resolution_error, client_for_server as _client_for_server, upstream_error as _upstream_error`
- In `vm_read_tools.py` and `cloudinit_tools.py`: replace `_sanitize_vm_payload`/`_sanitize_payload` with `from noa_api.proxmox.tools._shared import sanitize_proxmox_payload as _sanitize_payload`

- [ ] **Step 3: Fix crypt library sentinel in `_cloudinit_passwords.py`**

In `apps/api/src/noa_api/proxmox/tools/_cloudinit_passwords.py`, replace:

```python
_CRYPT_LOCK = Lock()
_CRYPT_LIB = None


def _load_crypt_lib() -> CDLL | None:
    global _CRYPT_LIB
    if _CRYPT_LIB is not None:
        return _CRYPT_LIB
```

With:

```python
_CRYPT_LOCK = Lock()
_NOT_LOADED: object = object()
_CRYPT_LIB: CDLL | None | object = _NOT_LOADED


def _load_crypt_lib() -> CDLL | None:
    global _CRYPT_LIB
    if _CRYPT_LIB is not _NOT_LOADED:
        return _CRYPT_LIB  # type: ignore[return-value]
```

Keep the rest of `_load_crypt_lib` unchanged. On failure path, `_CRYPT_LIB = None` stays the same.

- [ ] **Step 4: Run all Proxmox tests**

Run: `uv run pytest -q tests/test_proxmox_client_endpoints.py tests/test_proxmox_client_normalization.py tests/test_proxmox_tools_cloudinit.py tests/test_proxmox_tools_nic.py tests/test_proxmox_tools_pools.py tests/test_proxmox_tools_vm_reads.py tests/test_proxmox_server_ref.py tests/test_proxmox_workflow_templates.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add -A && git commit -m "refactor(proxmox): extract shared utilities, fix crypt lib sentinel

Fixes issues #5, #6, #12 from the Proxmox tools audit."
```

---

## Task 3: Preflight Server ID Resolution

**Files:**
- Modify: `apps/api/src/noa_api/core/workflows/preflight_validation.py`
- Test: `apps/api/tests/test_proxmox_server_ref.py`

- [ ] **Step 1: Write test for Proxmox server ID resolution**

Add to `apps/api/tests/test_proxmox_server_ref.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4


@pytest.mark.asyncio
async def test_resolve_requested_server_id_returns_proxmox_server_id(monkeypatch) -> None:
    from noa_api.core.workflows import preflight_validation

    proxmox_server_id = uuid4()

    # WHM resolution fails
    whm_resolution = MagicMock()
    whm_resolution.ok = False
    whm_resolution.server_id = None

    mock_whm_resolve = AsyncMock(return_value=whm_resolution)
    monkeypatch.setattr(preflight_validation, "resolve_whm_server_ref", mock_whm_resolve)

    # Proxmox resolution succeeds
    proxmox_resolution = MagicMock()
    proxmox_resolution.ok = True
    proxmox_resolution.server_id = proxmox_server_id

    mock_proxmox_resolve = AsyncMock(return_value=proxmox_resolution)
    monkeypatch.setattr(
        preflight_validation,
        "resolve_proxmox_server_ref",
        mock_proxmox_resolve,
    )

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    result = await preflight_validation.resolve_requested_server_id(
        args={"server_ref": "pve1"},
        session=mock_session,
    )

    assert result == str(proxmox_server_id)


@pytest.mark.asyncio
async def test_resolve_requested_server_id_prefers_whm_over_proxmox(monkeypatch) -> None:
    from noa_api.core.workflows import preflight_validation

    whm_server_id = uuid4()

    whm_resolution = MagicMock()
    whm_resolution.ok = True
    whm_resolution.server_id = whm_server_id

    mock_whm_resolve = AsyncMock(return_value=whm_resolution)
    monkeypatch.setattr(preflight_validation, "resolve_whm_server_ref", mock_whm_resolve)

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    result = await preflight_validation.resolve_requested_server_id(
        args={"server_ref": "whm1"},
        session=mock_session,
    )

    assert result == str(whm_server_id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_proxmox_server_ref.py::test_resolve_requested_server_id_returns_proxmox_server_id -v`
Expected: FAIL — `resolve_proxmox_server_ref` not called

- [ ] **Step 3: Implement Proxmox resolution fallback**

In `apps/api/src/noa_api/core/workflows/preflight_validation.py`, replace `resolve_requested_server_id`:

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
    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(requested_server_ref, repo=repo)
    if resolution.ok and resolution.server_id is not None:
        return str(resolution.server_id)

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

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_proxmox_server_ref.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add -A && git commit -m "fix(proxmox): resolve Proxmox server IDs in preflight validation

Fixes issue #1 from the Proxmox tools audit. resolve_requested_server_id
now falls back to Proxmox server resolution when WHM resolution fails."
```

---

## Task 4: Tool Implementation Fixes

**Files:**
- Modify: `apps/api/src/noa_api/proxmox/tools/pool_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/nic_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/vm_read_tools.py`
- Modify: `apps/api/src/noa_api/proxmox/tools/cloudinit_tools.py`
- Test: `apps/api/tests/test_proxmox_tools_pools.py`
- Test: `apps/api/tests/test_proxmox_tools_nic.py`

- [ ] **Step 1: Write test for userid normalization**

Add to `apps/api/tests/test_proxmox_tools_pools.py`:

```python
def test_normalize_proxmox_userid_appends_pve_realm() -> None:
    from noa_api.proxmox.tools.pool_tools import _normalize_proxmox_userid

    assert _normalize_proxmox_userid("alice@example.com") == "alice@example.com@pve"
    assert _normalize_proxmox_userid("  alice@example.com  ") == "alice@example.com@pve"


def test_normalize_proxmox_userid_does_not_double_append_pve() -> None:
    from noa_api.proxmox.tools.pool_tools import _normalize_proxmox_userid

    assert _normalize_proxmox_userid("alice@example.com@pve") == "alice@example.com@pve"
    assert _normalize_proxmox_userid("alice@pve") == "alice@pve"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_proxmox_tools_pools.py::test_normalize_proxmox_userid_does_not_double_append_pve -v`
Expected: FAIL — `alice@pve@pve` != `alice@pve`

- [ ] **Step 3: Fix `_normalize_proxmox_userid`**

In `apps/api/src/noa_api/proxmox/tools/pool_tools.py`, replace:

```python
def _normalize_proxmox_userid(email: str) -> str:
    return f"{email.strip()}@pve"
```

With:

```python
def _normalize_proxmox_userid(email: str) -> str:
    normalized = email.strip()
    if normalized.endswith("@pve"):
        return normalized
    return f"{normalized}@pve"
```

- [ ] **Step 4: Write test for TOCTOU mitigation in pool move**

Add to `apps/api/tests/test_proxmox_tools_pools.py`:

```python
@pytest.mark.asyncio
async def test_proxmox_move_vms_between_pools_fails_when_source_pool_changed_before_mutation(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )

    # Preflight sees vmid 1057 in source pool, but by mutation time it's gone
    call_count = {"get_pool_source": 0}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def get_pool(self, poolid):
            if poolid == "pool_a":
                call_count["get_pool_source"] += 1
                if call_count["get_pool_source"] <= 2:
                    # First two calls (preflight + preflight inside move): vmid present
                    return {"ok": True, "message": "ok", "data": [{"poolid": "pool_a", "members": [{"vmid": 1057, "name": "vm1", "node": "pve1", "status": "running"}]}]}
                else:
                    # Third call (re-verify before mutation): vmid gone
                    return {"ok": True, "message": "ok", "data": [{"poolid": "pool_a", "members": []}]}
            return {"ok": True, "message": "ok", "data": [{"poolid": "pool_b", "members": []}]}

        async def get_user(self, userid):
            return {"ok": True, "message": "ok", "data": {"email": "alice@example.com"}}

        async def get_effective_permissions(self, userid, path):
            return {"ok": True, "message": "ok", "data": {path: {"VM.Allocate": 1}}}

        async def add_vms_to_pool(self, poolid, vmids):
            raise AssertionError("Should not reach add_vms_to_pool")

        async def remove_vms_from_pool(self, poolid, vmids):
            raise AssertionError("Should not reach remove_vms_from_pool")

    monkeypatch.setattr(pool_tools, "ProxmoxClient", _Client)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        email="alice@example.com",
        reason="Ticket #123",
    )

    assert result["ok"] is False
    assert result["error_code"] == "source_pool_changed"
```

- [ ] **Step 5: Implement TOCTOU re-verification**

In `apps/api/src/noa_api/proxmox/tools/pool_tools.py`, in `proxmox_move_vms_between_pools`, after the preflight check and after resolving the client, add before `add_result = await client.add_vms_to_pool(...)`:

```python
    # Re-verify source pool membership immediately before mutation (TOCTOU mitigation)
    source_pool_check = await client.get_pool(normalized_source_pool)
    if source_pool_check.get("ok") is not True:
        return _upstream_error(
            source_pool_check,
            fallback_message="Unable to re-verify source pool before mutation",
        )
    try:
        current_source_vmids = _pool_result_vmids(source_pool_check)
    except ValueError:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected pool payload",
        }
    if not all(vmid in current_source_vmids for vmid in vmids):
        return {
            "ok": False,
            "error_code": "source_pool_changed",
            "message": "One or more VMIDs are no longer in the source pool. Run preflight again.",
        }
```

- [ ] **Step 6: Fix pool permission validation**

In `apps/api/src/noa_api/proxmox/tools/pool_tools.py`, replace `_meaningful_permission_entries`:

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
    granted = {
        key for key, value in permissions.items()
        if value == 1 or value is True
    }
    if not granted.intersection(_REQUIRED_POOL_PERMISSIONS):
        return None
    return permissions
```

- [ ] **Step 7: Fix pool error messages**

In `apps/api/src/noa_api/proxmox/tools/pool_tools.py`, replace `_pool_members`:

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
                raise ValueError(
                    f"pool payload 'data[{i}].members[{j}]' must be a dict"
                )
            members.append(member)
    return members
```

- [ ] **Step 8: Fix node normalization in `_fetch_vm_nic_state`**

In `apps/api/src/noa_api/proxmox/tools/nic_tools.py`, add `node = node.strip()` at the top of `_fetch_vm_nic_state`.

- [ ] **Step 9: Refactor NIC change to single server resolution**

In `apps/api/src/noa_api/proxmox/tools/nic_tools.py`:

Extract `_fetch_vm_nic_state_with_client`:

```python
async def _fetch_vm_nic_state_with_client(
    *,
    client: ProxmoxClient,
    server_id: str,
    node: str,
    vmid: int,
    net: str | None,
) -> dict[str, object]:
    node = node.strip()
    config_result = await client.get_qemu_config(node, vmid)
    if config_result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(config_result.get("error_code") or "unknown"),
            "message": str(
                config_result.get("message") or "Proxmox QEMU config lookup failed"
            ),
        }

    config = config_result.get("config")
    digest = _normalized_text(config_result.get("digest"))
    config = _object_dict(config)
    if config is None or digest is None:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected QEMU config payload",
        }

    nets = _normalize_nics(config)
    selected_net, selection_error = _select_nic(nets=nets, requested_net=net)
    if selection_error is not None:
        return {
            **selection_error,
            "server_id": server_id,
            "node": node,
            "vmid": vmid,
            "digest": digest,
        }

    assert selected_net is not None
    selected_key = _normalized_text(selected_net.get("key"))
    selected_value = _normalized_text(selected_net.get("value"))
    assert selected_key is not None
    assert selected_value is not None

    return {
        "ok": True,
        "server_id": server_id,
        "node": node,
        "vmid": vmid,
        "digest": digest,
        "net": selected_key,
        "before_net": selected_value,
        "link_state": _net_link_state(selected_value),
        "auto_selected_net": net is None and len(nets) == 1,
        "nets": nets,
    }
```

Rewrite `_fetch_vm_nic_state` to delegate:

```python
async def _fetch_vm_nic_state(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    net: str | None,
) -> dict[str, object]:
    repo = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    client = _client_for_server(server)
    return await _fetch_vm_nic_state_with_client(
        client=client,
        server_id=str(resolution.server_id),
        node=node,
        vmid=vmid,
        net=net,
    )
```

Rewrite `_change_vm_nic_link_state` to resolve once:

```python
async def _change_vm_nic_link_state(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    net: str,
    digest: str,
    disabled: bool,
) -> dict[str, object]:
    normalized_node = node.strip()
    normalized_digest = digest.strip()
    if not normalized_digest:
        return {
            "ok": False,
            "error_code": "digest_required",
            "message": "Digest is required",
        }

    # Resolve server once
    repo = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    client = _client_for_server(server)
    server_id = str(resolution.server_id)

    state = await _fetch_vm_nic_state_with_client(
        client=client,
        server_id=server_id,
        node=normalized_node,
        vmid=vmid,
        net=net,
    )
    if state.get("ok") is not True:
        return state

    # ... rest of the function uses `client` and `server_id` directly
    # instead of re-resolving. Remove the second resolve_proxmox_server_ref call.
```

Remove the second `repo = SQLProxmoxServerRepository(session)` / `resolution = await resolve_proxmox_server_ref(...)` block that was at lines 360-367 of the original file.

- [ ] **Step 10: Add vmid validation to tool entry points**

In each public tool function that accepts `vmid`, add at the top:

```python
    vmid_error = validate_vmid(vmid)
    if vmid_error is not None:
        return vmid_error
```

Import `validate_vmid` from `_shared`. Apply to: `proxmox_get_vm_status_current`, `proxmox_get_vm_config`, `proxmox_get_vm_pending`, `proxmox_preflight_vm_cloudinit_password_reset`, `proxmox_reset_vm_cloudinit_password`, `proxmox_preflight_vm_nic_toggle`, `proxmox_disable_vm_nic`, `proxmox_enable_vm_nic`.

- [ ] **Step 11: Add security comment for password in memory (#11)**

In `apps/api/src/noa_api/proxmox/tools/cloudinit_tools.py`, before the `_wait_for_cloudinit_verification` call in `proxmox_reset_vm_cloudinit_password`, add:

```python
    # Security note: new_password is held in memory during the polling loop
    # (up to ~2.5s) because crypt verification requires the plaintext.
    # This is unavoidable for the current verification approach.
```

- [ ] **Step 12: Run all Proxmox tests**

Run: `uv run pytest -q tests/test_proxmox_tools_pools.py tests/test_proxmox_tools_nic.py tests/test_proxmox_tools_cloudinit.py tests/test_proxmox_tools_vm_reads.py -v`
Expected: ALL PASS

- [ ] **Step 13: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add -A && git commit -m "fix(proxmox): userid normalization, TOCTOU mitigation, single server resolution, vmid validation, pool permissions

Fixes issues #7, #8, #9, #10, #11, #16, #19, #23 from the Proxmox tools audit."
```

---

## Task 5: Workflow and Prompt Fixes

**Files:**
- Modify: `apps/api/src/noa_api/core/workflows/proxmox/common.py`
- Modify: `apps/api/src/noa_api/core/workflows/proxmox/postflight.py`
- Modify: `apps/api/src/noa_api/core/tools/definitions/proxmox.py`
- Modify: `apps/api/src/noa_api/proxmox/server_ref.py`
- Test: `apps/api/tests/test_proxmox_workflow_templates.py`

- [ ] **Step 1: Fix generic default reason text (#13)**

In `apps/api/src/noa_api/core/workflows/proxmox/common.py`, replace the default path in `_reason_step_content`:

```python
def _reason_step_content(
    *,
    action_label: str,
    action_verb: str,
    reason: str | None,
    missing_reason_text: str | None = None,
) -> str:
    if reason is None:
        if missing_reason_text is not None:
            return missing_reason_text
        return (
            "Ask the user for a reason\u2014an osTicket/reference number or a brief "
            f"description\u2014before proceeding with the {action_label} change."
        )
    return f"Reason captured for the {action_label} change: {reason}."
```

- [ ] **Step 2: Refactor action labels to dict-based lookup (#14)**

In `apps/api/src/noa_api/core/workflows/proxmox/common.py`, replace the individual label functions:

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

_NIC_DEFAULTS: dict[str, str] = {
    "action": "change VM NIC",
    "approval": "Change VM NIC",
    "desired_state": "unknown",
    "verb": "change",
    "completed": "Changed",
    "adjective": "changed",
}


def _nic_label(tool_name: str, key: str) -> str:
    return _NIC_ACTION_LABELS.get(tool_name, _NIC_DEFAULTS).get(
        key, _NIC_DEFAULTS[key]
    )


def _action_label(tool_name: str) -> str:
    return _nic_label(tool_name, "action")


def _approval_action_label(tool_name: str) -> str:
    return _nic_label(tool_name, "approval")


def _desired_link_state(tool_name: str) -> str:
    return _nic_label(tool_name, "desired_state")


def _action_verb(tool_name: str) -> str:
    return _nic_label(tool_name, "verb")


def _action_completed_label(tool_name: str) -> str:
    return _nic_label(tool_name, "completed")


def _action_outcome_adjective(tool_name: str) -> str:
    return _nic_label(tool_name, "adjective")
```

- [ ] **Step 3: Fix postflight None password handling (#15)**

In `apps/api/src/noa_api/core/workflows/proxmox/postflight.py`, update `_cloudinit_postflight_result`:

```python
async def _cloudinit_postflight_result(
    *, client: ProxmoxClient, node: str, vmid: int, new_password: str | None = None
) -> dict[str, object] | None:
    verification_result = await _wait_for_cloudinit_verification(
        client=client,
        node=node,
        vmid=vmid,
        new_password=new_password,
    )
    if verification_result.get("ok") is not True:
        return verification_result
    return {
        "ok": True,
        "cloudinit": verification_result["cloudinit"],
        "cloudinit_dump_user": verification_result["cloudinit_dump_user"],
        "verified": True,
    }
```

Update `_wait_for_cloudinit_verification`:

```python
async def _wait_for_cloudinit_verification(
    *,
    client: ProxmoxClient,
    node: str,
    vmid: int,
    new_password: str | None,
) -> dict[str, object]:
    cloudinit_result = await client.get_qemu_cloudinit(node, vmid)
    if cloudinit_result.get("ok") is not True:
        return _upstream_error(
            cloudinit_result,
            fallback_message="Unable to verify Proxmox cloud-init values",
        )

    dump_result = await client.get_qemu_cloudinit_dump_user(node, vmid)
    if dump_result.get("ok") is not True:
        return _upstream_error(
            dump_result,
            fallback_message="Unable to verify Proxmox cloud-init user dump",
        )

    sanitized_dump, confirmed = sanitize_cloudinit_dump_user(dump_result.get("data"))
    if not confirmed or sanitized_dump is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    if not _cloudinit_confirms_password_reset(cloudinit_result):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    # Only check password hash match if we have the plaintext password
    if new_password is not None and not cloudinit_dump_matches_password(
        dump_result.get("data"), new_password
    ):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    return {
        "ok": True,
        "cloudinit": cloudinit_result,
        "cloudinit_dump_user": {**dump_result, "data": sanitized_dump},
        "verified": True,
    }
```

- [ ] **Step 4: Add UUID-as-hostname comment (#20)**

In `apps/api/src/noa_api/proxmox/server_ref.py`, add before line 53 (`try: server_id = UUID(ref)`):

```python
    # UUID lookup takes priority over hostname lookup. If a hostname happens
    # to be a valid UUID string, it will be treated as a server ID. This is
    # the correct precedence — UUIDs are unambiguous identifiers.
```

- [ ] **Step 5: Add password generation prompt hint (#21)**

In `apps/api/src/noa_api/core/tools/definitions/proxmox.py`, update `proxmox_reset_vm_cloudinit_password` prompt_hints:

```python
        prompt_hints=(
            "Run `proxmox_preflight_vm_cloudinit_password_reset` first and reuse the same server_ref, node, and vmid.",
            "Generate a strong random password (16+ chars, mixed case, digits, symbols) unless the user provides a specific password.",
            "Idempotent result contract: returns `status` `changed` only after postflight confirms the cloud-init password reset.",
        ),
```

- [ ] **Step 6: Run all workflow and tool tests**

Run: `uv run pytest -q tests/test_proxmox_workflow_templates.py tests/test_proxmox_tools_cloudinit.py tests/test_proxmox_tools_nic.py tests/test_proxmox_tools_pools.py -v`
Expected: ALL PASS

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add -A && git commit -m "fix(proxmox): generic reason text, dict-based labels, postflight None password, prompt hints

Fixes issues #13, #14, #15, #20, #21 from the Proxmox tools audit."
```

---

## Task 6: Final Verification

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -q
```

Expected: ALL PASS

- [ ] **Step 2: Run linter**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: No errors

- [ ] **Step 3: Verify no regressions in existing tests**

```bash
uv run pytest -q tests/test_proxmox_client_endpoints.py tests/test_proxmox_client_normalization.py tests/test_proxmox_tools_cloudinit.py tests/test_proxmox_tools_nic.py tests/test_proxmox_tools_pools.py tests/test_proxmox_tools_vm_reads.py tests/test_proxmox_server_ref.py tests/test_proxmox_workflow_templates.py tests/test_proxmox_admin_service.py -v
```

Expected: ALL PASS
