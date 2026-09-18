"""Proxmox VE API client.

Copied from `noa-old` branch `MCP` (`proxmox/integrations/client.py`), with the method surface
scoped to what this repo's tools and admin routes actually reach for (see "What is not here").

The behaviour worth copying is the **failure normalisation**, not the HTTP. Proxmox reports a
refusal in four different shapes depending on which layer refused, and only one of them is the
status line. Every call funnels through `_request_json`, which turns each into one
`{"ok": False, "error_code": …, "message": …}` shape:

| condition | `error_code` |
|---|---|
| connect/read deadline | `timeout` |
| DNS, refused, reset, TLS | `request_failed` |
| HTTP 401 | `auth_failed` |
| HTTP 403 | `permission_denied` (carries Proxmox's privilege/path detail) |
| `errors` payload, or `message` on a failure | `proxmox_api_error` |
| either of those, worded as a digest conflict | `digest_mismatch` |
| any other HTTP ≥ 400 | `http_error` |
| body is not JSON or not an object | `invalid_response` |

`permission_denied` is split out from `auth_failed` because they need opposite operator actions:
a bad token is fixed in NOA's server row, a missing `SDN.Use` privilege is fixed in Proxmox. The
message carries Proxmox's own text — `Permission check failed (/sdn/zones/localnetwork/vmbr2/110,
SDN.Use)` names the exact ACL path to grant.

`digest_mismatch` is split out from `proxmox_api_error` because it is the only one that means
"retry from a fresh read". A NIC write is read-digest-then-write; Proxmox rejects the write
when the config changed underneath, and that CAS failure must stay legible all the way up.

Dict-returning, not exception-raising: these codes are the tool layer's material for a structured
result, and the sanitisation boundary is one layer up. The strings are stable — tools and tests
branch on them.

**Sync or async, same endpoint.** A Proxmox config write answers with a UPID string to poll, or
with `data: null` when it already finished. `_request_json_task` reports which happened
(`synchronous`) instead of leaving each caller to infer it from a null.

**A pooled client, unlike WHM's.** `httpx.AsyncClient` is held on the instance and shared, behind
an `asyncio.Semaphore(5)`. Verbatim from `noa-old`, and the right call here: task polling makes
many sequential requests per workflow, so the TLS handshake would be paid on each one. The
semaphore caps what a single workflow can aim at one node.

`close()` releases it, and `async with` is the shape that cannot forget to — a port deviation.

**Credentials.** `ProxmoxClient` takes a plaintext secret; `build_proxmox_client_from_creds` is
the one place ciphertext becomes plaintext. `noa-old` decrypted in three — another port deviation.

**What is not here**, and why — each was in the source:

- `get_user` — backs `proxmox_get_user_by_email`, on the never-implement list.
- `get_pool`, `get_effective_permissions`, `add_vms_to_pool`, `remove_vms_from_pool` — the pool
  membership move (`proxmox_move_vms_between_pools` and its preflight), also never-implement.
  Their only
  callers on `MCP` were `proxmox/tools/pool_tools.py` and `core/workflows/proxmox/postflight.py`.

The never-implement list is a management policy boundary, not a technical one. Porting the client
methods would leave the capability one line from exposure; re-adding them is an owner decision,
never an agent call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import TracebackType
from typing import Protocol

import httpx

from core.secrets.crypto import SecretCipher


def _normalized_text(value: object) -> str | None:
    """A non-empty stripped string, or `None`. Non-strings are `None`, never stringified.

    Used on values Proxmox may answer as a string, a null, or a structure — a UPID, a task
    status, a config digest. `None` means "absent", and callers branch on that.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _render_error_detail(value: object) -> str | None:
    """Flatten Proxmox's `errors` into one line.

    The field is polymorphic: a string, a list of strings, or a `{field: reason}` map (which is
    what a parameter-validation failure looks like). Renders the map as `key: reason` so the
    field name survives into the operator-visible message.
    """
    if isinstance(value, str):
        return _normalized_text(value)
    if isinstance(value, list):
        parts = [_render_error_detail(item) for item in value]
        joined = "; ".join(part for part in parts if part is not None)
        return joined or None
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            item_text = _render_error_detail(item)
            if item_text is None:
                continue
            parts.append(f"{key}: {item_text}")
        joined = "; ".join(parts)
        return joined or None
    return _normalized_text(str(value)) if value is not None else None


def _is_digest_error(*, message: str | None, errors: object | None = None) -> bool:
    """Is this refusal a config-digest conflict?

    Two shapes, because Proxmox uses both: a `digest` key inside `errors`, or prose in `message`
    (`configuration file has been modified by another user (digest mismatch)`). The prose match
    needs "digest" *and* a change word — "digest" alone appears in successful config reads.
    """
    message_text = (message or "").lower()
    if "digest" in message_text and (
        "mismatch" in message_text or "modified" in message_text or "changed" in message_text
    ):
        return True

    if isinstance(errors, dict):
        for key, value in errors.items():
            if not isinstance(key, str):
                continue
            if key.lower() == "digest":
                return True
            value_text = _render_error_detail(value)
            if value_text is None:
                continue
            if _is_digest_error(message=value_text, errors=None):
                return True
    return False


def _response_message(response: httpx.Response) -> str:
    """Proxmox's own `message` out of a body of any shape, or `""`.

    Used on a 403, where the status line already settles the verdict and the body only adds
    detail — so a body that is absent, non-JSON, or not an object is not a failure here, it just
    means there is nothing to add.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, Mapping):
        return ""
    return str(body.get("message") or "").strip()


def _payload_error(payload: Mapping[str, object], *, status_code: int) -> dict[str, object] | None:
    """Read a refusal out of the body, or `None` if the body reports none.

    Checked at the top level *and* under `data`, because Proxmox nests the same `errors` /
    `message` pair one level down depending on the endpoint.

    Ported as-is, with two properties worth stating rather than rediscovering:

    - **A `message` key is read as a failure signal wherever it appears, including on a 200.** No
      endpoint this client calls answers one on success, so it holds for the current method
      surface — but it is an assumption, and a new endpoint has to be checked against it.
    - **The trailing `status_code >= 400` block is unreachable.** Reaching it means the loop found
      no message, and it then re-reads the same `payload["message"]` the loop's first source
      already normalised to `None`. A 4xx with prose therefore comes back `proxmox_api_error`
      from the loop, not `http_error` from here; a 4xx with nothing in the body falls past this
      function to `_request_json`'s own `http_error`. Kept for fidelity, pinned by
      `test_maps_a_bare_failure_message_to_proxmox_api_error`.
    """
    error_sources: list[tuple[object | None, str | None]] = []
    error_sources.append((payload.get("errors"), _normalized_text(payload.get("message"))))

    data = payload.get("data")
    if isinstance(data, dict):
        error_sources.append((data.get("errors"), _normalized_text(data.get("message"))))

    for errors, message in error_sources:
        detail = _render_error_detail(errors)
        if detail is None and message is None:
            continue
        rendered_message = detail or message or "Proxmox API error"
        error_code = (
            "digest_mismatch"
            if _is_digest_error(message=rendered_message, errors=errors)
            else "proxmox_api_error"
        )
        return {"ok": False, "error_code": error_code, "message": rendered_message}

    if status_code >= 400:
        message = _normalized_text(payload.get("message"))
        if message is not None:
            error_code = (
                "digest_mismatch"
                if _is_digest_error(message=message, errors=None)
                else "http_error"
            )
            return {"ok": False, "error_code": error_code, "message": message}

    return None


class ProxmoxClient:
    """One authenticated Proxmox VE endpoint. Construct via `build_proxmox_client_from_creds`.

    `api_token_secret` is plaintext here — decryption happens in the factory, so this class
    stays testable without a cipher and there is exactly one decrypt site.

    Holds an `httpx.AsyncClient`. Use `async with`, or call `close()`.
    """

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

    async def __aenter__(self) -> ProxmoxClient:
        """Port deviation. `close()` exists on `noa-old` and is called nowhere there — every
        tool call leaked a socket and a TLS context. This is the shape that cannot."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def _get_client(self) -> httpx.AsyncClient:
        """The shared client, rebuilt if absent or closed.

        Lazy rather than built in `__init__` so constructing a client is not an event-loop
        operation — the admin routes build one per request and may never call it.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=self._verify_ssl,
                timeout=self._timeout_seconds,
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        """Release the shared client. Idempotent; a later call rebuilds."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": (f"PVEAPIToken={self._api_token_id}={self._api_token_secret}"),
            "Accept": "application/json",
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        form_data: Mapping[str, object] | None = None,
        query_params: Mapping[str, object] | None = None,
        # ASYNC109: `timeout` is httpx's per-request deadline, not an asyncio one. `asyncio.timeout`
        # would raise `CancelledError` past this function, losing the `timeout` error_code that
        # tells a caller to retry (and, on a CHANGE, that the write may have landed anyway).
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> dict[str, object]:
        """Call one `/api2/json/...` path and normalise every outcome (see module docstring)."""
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
            return {"ok": False, "error_code": "timeout", "message": "Request timed out"}
        except httpx.RequestError as exc:
            # `exc` reports transport state (host, errno), never the token in `_headers`.
            return {
                "ok": False,
                "error_code": "request_failed",
                "message": f"Request failed: {exc}",
            }

        if response.status_code == 401:
            return {
                "ok": False,
                "error_code": "auth_failed",
                "message": "Proxmox authentication failed",
            }

        if response.status_code == 403:
            # Distinct from 401 on purpose: the token is valid, the ACL is not. Proxmox names
            # the path and privilege it wanted, which is the whole fix.
            detail = _response_message(response)
            return {
                "ok": False,
                "error_code": "permission_denied",
                "message": (
                    f"Proxmox permission denied: {detail}"
                    if detail
                    else "Proxmox permission denied"
                ),
            }

        try:
            payload = response.json()
        except ValueError:
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "error_code": "http_error",
                    "message": f"Proxmox returned HTTP {response.status_code}",
                }
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned a non-JSON response",
            }

        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned an unexpected response shape",
            }

        # Body before status: a digest conflict arrives as a 4xx *and* an `errors` payload, and
        # the payload is the half that says which of the two it is.
        payload_error = _payload_error(payload, status_code=response.status_code)
        if payload_error is not None:
            return payload_error

        if response.status_code >= 400:
            return {
                "ok": False,
                "error_code": "http_error",
                "message": f"Proxmox returned HTTP {response.status_code}",
            }

        return {"ok": True, "message": "ok", "data": payload.get("data")}

    async def _request_json_task(
        self,
        method: str,
        path: str,
        *,
        form_data: Mapping[str, object] | None = None,
        query_params: Mapping[str, object] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 — httpx's deadline, see `_request_json`
    ) -> dict[str, object]:
        """As `_request_json`, for endpoints that may answer with a task to poll.

        Proxmox returns a UPID string when the work was queued, and `data: null` when it
        finished inline. `synchronous` states which, so no caller has to read that off a null.
        """
        result = await self._request_json(
            method,
            path,
            form_data=form_data,
            query_params=query_params,
            timeout=timeout,
        )
        if result.get("ok") is not True:
            return result

        upid = _normalized_text(result.get("data"))
        return {"ok": True, "message": "ok", "upid": upid, "synchronous": upid is None}

    async def get_version(self) -> dict[str, object]:
        """Cheapest authenticated call Proxmox offers — the credential probe for admin validate."""
        return await self._request_json("GET", "/api2/json/version")

    async def get_qemu_status_current(self, node: str, vmid: int) -> dict[str, object]:
        """Runtime state plus CPU/memory/disk/net counters. Exact node, never a cluster-wide
        search."""
        return await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/qemu/{vmid}/status/current",
        )

    async def get_qemu_pending(self, node: str, vmid: int) -> dict[str, object]:
        """Config changes staged but not yet applied to the running VM."""
        return await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/qemu/{vmid}/pending",
        )

    async def get_qemu_cloudinit(self, node: str, vmid: int) -> dict[str, object]:
        """Cloud-init key/value pairs as Proxmox holds them — the password reset's
        before-state."""
        return await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/qemu/{vmid}/cloudinit",
        )

    async def get_qemu_cloudinit_dump_user(self, node: str, vmid: int) -> dict[str, object]:
        """The rendered user-data document. Carries the crypt hash the reset verifies
        against."""
        return await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/qemu/{vmid}/cloudinit/dump",
            query_params={"type": "user"},
        )

    async def set_qemu_cloudinit_password(
        self, node: str, vmid: int, new_password: str
    ) -> dict[str, object]:
        """Write `cipassword` only, leaving `ciuser` alone. Backs `proxmox_reset_vm_password`.

        `new_password` is generated server-side and never an LLM argument. It is not
        echoed into the result: the return carries `{ok, message, upid, synchronous}` and
        nothing else, so the plaintext stays inside the caller's `execute()` scope.
        """
        return await self._request_json_task(
            "POST",
            f"/api2/json/nodes/{node}/qemu/{vmid}/config",
            form_data={"cipassword": new_password},
        )

    async def regenerate_qemu_cloudinit(self, node: str, vmid: int) -> dict[str, object]:
        """Rewrite the cloud-init drive so the new password reaches the guest on next boot.

        Ported as-is: this goes through `_request_json`, not `_request_json_task`, so a UPID
        arrives as `data` and is not polled. Deliberate on `noa-old`, kept — the reset workflow
        verifies by re-reading cloud-init rather than by waiting on this task. Whether to poll
        it is the password-reset runner's call, not this layer's.

        30s, not the default 20: regeneration writes the drive image.
        """
        return await self._request_json(
            "PUT",
            f"/api2/json/nodes/{node}/qemu/{vmid}/cloudinit",
            timeout=30.0,
        )

    async def get_qemu_config(self, node: str, vmid: int) -> dict[str, object]:
        """VM config plus its digest → `{"ok": True, "config": {...}, "digest": "..."}`.

        A config without a digest is `invalid_response`, never a soft pass. The digest is the
        compare-and-set token for `update_qemu_config`: the NIC tool reads it, then writes with
        it, and Proxmox refuses the write if the config moved. Handing back a digest-free config
        would turn that fail-closed CAS into a blind overwrite.
        """
        result = await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/qemu/{vmid}/config",
        )
        if result.get("ok") is not True:
            return result

        data = result.get("data")
        if not isinstance(data, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned an unexpected QEMU config payload",
            }

        digest = _normalized_text(data.get("digest"))
        if digest is None:
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox QEMU config payload is missing a digest",
            }

        return {"ok": True, "message": "ok", "config": data, "digest": digest}

    async def update_qemu_config(
        self,
        node: str,
        vmid: int,
        *,
        digest: str,
        net_key: str,
        net_value: str,
    ) -> dict[str, object]:
        """Write one `netN` line under a digest. Backs `proxmox_vm_nic`.

        `digest` is required, never optional: it must be the value from the `get_qemu_config` read
        this write is based on. Proxmox answers `digest_mismatch` when the config changed
        underneath, and the caller then needs a fresh preflight, not a retry.
        """
        return await self._request_json_task(
            "POST",
            f"/api2/json/nodes/{node}/qemu/{vmid}/config",
            form_data={"digest": digest, net_key: net_value},
        )

    async def get_task_status(self, node: str, upid: str) -> dict[str, object]:
        """Poll one UPID → `{"upid", "task_status", "task_exit_status", "data"}`.

        Both status fields are `_normalized_text`, so "absent" is `None` rather than `""`:
        terminality is `task_status == "stopped"`, or an exit status present while the task is
        no longer running — and an empty string would read as present.
        """
        result = await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/tasks/{upid}/status",
        )
        if result.get("ok") is not True:
            return result

        data = result.get("data")
        if not isinstance(data, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned an unexpected task status payload",
            }

        return {
            "ok": True,
            "message": "ok",
            "upid": upid,
            "task_status": _normalized_text(data.get("status")),
            "task_exit_status": _normalized_text(data.get("exitstatus")),
            "data": data,
        }


def build_proxmox_client_from_creds(
    *,
    base_url: str,
    api_token_id: str,
    encrypted_token_secret: str,
    verify_ssl: bool,
    cipher: SecretCipher,
    timeout_seconds: float = 20.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProxmoxClient:
    """Single construction point for an authenticated Proxmox client.

    `encrypted_token_secret` is the at-rest `proxmox_servers.api_token_secret` column value;
    `maybe_decrypt_text` unwraps it, tolerating a row that predates encryption. One
    decrypt site means one place to audit and one place to change when a `v2` scheme lands.

    Port deviation: `noa-old` decrypted in three places — the admin validate service, the
    tool-layer `client_for_server`, and the postflight helper — each reaching for a module-level
    `maybe_decrypt_text` deleted here along with the settings singleton behind it.
    `noa_api.main.build_runtime` builds the one `SecretCipher` on `AppRuntime` and it
    arrives here as an argument.

    `transport` is a test seam — `httpx.MockTransport` in `test_proxmox_client.py`.
    """
    return ProxmoxClient(
        base_url=base_url,
        api_token_id=api_token_id,
        api_token_secret=cipher.maybe_decrypt_text(encrypted_token_secret),
        verify_ssl=verify_ssl,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )


class ProxmoxServerSecretLike(Protocol):
    """The `proxmox_servers` columns this layer reads (`core.db.models.ProxmoxServer`).

    A `Protocol` rather than the ORM class, the way `WHMServerSecretLike` is: it keeps `core/`'s
    integration layer independent of the session that loaded the row, and lets a test pass a
    plain object instead of constructing a mapped instance.

    Narrower than WHM's by four SSH fields, because there are none on this table — Proxmox is an
    HTTP API and nothing else (I.ext), so there is no second transport to authenticate.
    """

    base_url: str
    api_token_id: str
    api_token_secret: str
    verify_ssl: bool


def build_proxmox_client(
    server: ProxmoxServerSecretLike,
    *,
    cipher: SecretCipher,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProxmoxClient:
    """Row → authenticated Proxmox client.

    The twin of `build_whm_client` and here for its reason: a tool holds the row it
    resolved and needs a client from *that* row, while the one decrypt site stays
    `build_proxmox_client_from_creds` below it.

    `transport` is forwarded because it is the test seam, and it is a parameter rather than an
    attribute a test reaches into afterwards. A tool test that swaps the transport keeps the real
    client, the real cipher and the real decrypt site in the path — only the socket is doubled.
    """
    return build_proxmox_client_from_creds(
        base_url=server.base_url,
        api_token_id=server.api_token_id,
        encrypted_token_secret=server.api_token_secret,
        verify_ssl=server.verify_ssl,
        cipher=cipher,
        transport=transport,
    )


class ProxmoxClientFactory(Protocol):
    """How the tool path asks for a Proxmox client.

    `build_proxmox_client` is the production implementation and the default everywhere. The
    Protocol exists so `McpToolContext` can name the seam in a type instead of a
    `Callable[..., ProxmoxClient]` that would accept any signature — `WHMClientFactory`'s
    argument, one system over, and deliberately the same call shape so a reader of one recognises
    the other.
    """

    def __call__(
        self,
        server: ProxmoxServerSecretLike,
        *,
        cipher: SecretCipher,
    ) -> ProxmoxClient: ...
