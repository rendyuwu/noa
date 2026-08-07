"""Proxmox VE client — failure classification, digest discipline, credentials, lifecycle.

T17, C7, C22, V8, V48, V62, V69. Ported from `noa-old` branch `MCP`
(`test_proxmox_client_normalization.py`, `test_proxmox_client_endpoints.py`) and extended.

The error-mapping cases are the point of the file. Proxmox reports a refusal in four shapes
depending on which layer refused — status line, top-level `errors`, `errors` nested under `data`,
or prose in `message` — and the tool layer branches on the resulting `error_code` to decide what
an operator should do about it. Two splits matter enough to have their own tests:

- `permission_denied` vs `auth_failed` — an ACL gap is fixed in Proxmox, a bad token in NOA.
- `digest_mismatch` vs `proxmox_api_error` — only the former means "re-read, ⊥ retry".

Request-contract tests (literal verbs, URLs, bodies) live in `test_proxmox_client_endpoints.py`.

`httpx.MockTransport` throughout — no network, no live Proxmox.
"""

from __future__ import annotations

import httpx
import pytest

from core.integrations.proxmox.client import ProxmoxClient, build_proxmox_client_from_creds
from support.proxmox import BASE_URL, build_client, data_handler, json_handler
from support.secrets import build_cipher

# --- V19 (feeds): every failure gets a stable, distinct code ---


async def test_maps_401_to_auth_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, text="unauthorized", request=request)

    result = await build_client(handler).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "auth_failed"


async def test_maps_403_to_permission_denied_and_keeps_the_privilege_detail() -> None:
    """⊥ folded into `auth_failed`: the token is fine, the ACL is not, and Proxmox names the
    exact path and privilege to grant — which is the whole fix."""
    payload = {
        "message": "Permission check failed (/sdn/zones/localnetwork/vmbr2/110, SDN.Use)\n",
        "data": None,
    }

    result = await build_client(json_handler(payload, status_code=403)).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "permission_denied"
    assert "SDN.Use" in str(result["message"])


async def test_maps_403_without_a_body_to_permission_denied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=403, text="", request=request)

    result = await build_client(handler).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "permission_denied"
    assert result["message"] == "Proxmox permission denied"


async def test_maps_timeout_to_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    result = await build_client(handler, timeout_seconds=0.01).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "timeout"


async def test_maps_transport_failure_to_request_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = await build_client(handler).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "request_failed"


async def test_maps_server_error_to_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, text="boom", request=request)

    result = await build_client(handler).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "http_error"


async def test_maps_non_json_to_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, text="not json", request=request)

    result = await build_client(handler).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


@pytest.mark.parametrize("payload", [["a", "list"], "text", 7])
async def test_maps_non_object_payloads_to_invalid_response(payload: object) -> None:
    result = await build_client(json_handler(payload)).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


async def test_maps_errors_payload_to_proxmox_api_error_with_the_field_name() -> None:
    """`errors` is a `{field: reason}` map on a parameter-validation failure. The field name has
    to survive into the message — "invalid parameter" alone tells an operator nothing."""
    payload = {"errors": {"vmid": "value does not look like a valid VM ID"}}

    result = await build_client(json_handler(payload, status_code=400)).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "proxmox_api_error"
    assert "vmid" in str(result["message"])
    assert "valid VM ID" in str(result["message"])


async def test_maps_errors_nested_under_data_to_proxmox_api_error() -> None:
    """Proxmox nests the same `errors` pair one level down on some endpoints."""
    payload = {"data": {"errors": ["first problem", "second problem"]}}

    result = await build_client(json_handler(payload, status_code=400)).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "proxmox_api_error"
    assert result["message"] == "first problem; second problem"


async def test_maps_a_bare_failure_message_to_proxmox_api_error() -> None:
    """No `errors`, just prose on a 4xx. Pins which of `_payload_error`'s two branches answers:
    the first (`proxmox_api_error`, carrying the prose), ⊥ its trailing `status_code >= 400`
    block — that block re-reads the same `message` the first branch already consumed, so it is
    unreachable. Ported that way (V69); this test is what fails if someone "revives" it."""
    result = await build_client(
        json_handler({"message": "no such VM"}, status_code=400)
    ).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "proxmox_api_error"
    assert result["message"] == "no such VM"


async def test_maps_a_4xx_with_no_message_at_all_to_http_error() -> None:
    """The generic fallback: nothing in the body identifies the failure, so the status line does."""
    result = await build_client(json_handler({"data": None}, status_code=400)).get_version()

    assert result["ok"] is False
    assert result["error_code"] == "http_error"
    assert "400" in str(result["message"])


# --- V62: a digest conflict stays legible, because it means re-read, ⊥ retry ---


async def test_digest_key_in_errors_maps_to_digest_mismatch() -> None:
    payload = {
        "errors": {
            "digest": "configuration file has been modified by another user (digest mismatch)"
        }
    }

    result = await build_client(json_handler(payload, status_code=409)).get_qemu_config("pve1", 101)

    assert result["ok"] is False
    assert result["error_code"] == "digest_mismatch"


async def test_digest_wording_in_a_bare_message_maps_to_digest_mismatch() -> None:
    """The other shape Proxmox uses: prose, no `errors` key."""
    payload = {"message": "configuration file has been modified by another user (digest mismatch)"}

    result = await build_client(json_handler(payload, status_code=500)).get_qemu_config("pve1", 101)

    assert result["ok"] is False
    assert result["error_code"] == "digest_mismatch"


async def test_the_word_digest_alone_is_not_a_digest_mismatch() -> None:
    """ "digest" needs a change word beside it — the token appears in ordinary config text, and
    misreading one as a CAS failure would send T28 into a pointless fresh preflight."""
    payload = {"errors": {"detail": "digest algorithm not supported"}}

    result = await build_client(json_handler(payload, status_code=400)).get_qemu_config("pve1", 101)

    assert result["error_code"] == "proxmox_api_error"


async def test_get_qemu_config_returns_the_digest_alongside_the_config() -> None:
    config = {"digest": "abc123", "net0": "virtio=AA:BB:CC,bridge=vmbr0", "memory": 2048}

    result = await build_client(data_handler(config)).get_qemu_config("pve1", 101)

    assert result["ok"] is True
    assert result["digest"] == "abc123"
    assert result["config"] == config


@pytest.mark.parametrize("config", [{"net0": "virtio=AA:BB:CC"}, {"digest": "   "}, {"digest": 7}])
async def test_get_qemu_config_without_a_usable_digest_is_invalid_response(config: object) -> None:
    """Fails closed. The digest is the compare-and-set token for `update_qemu_config`; handing
    back a config without one would turn T28's fail-closed write into a blind overwrite."""
    result = await build_client(data_handler(config)).get_qemu_config("pve1", 101)

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"
    assert "digest" in str(result["message"])


@pytest.mark.parametrize("data", ["a string", ["a", "list"], None])
async def test_get_qemu_config_with_a_non_object_payload_is_invalid_response(data: object) -> None:
    result = await build_client(data_handler(data)).get_qemu_config("pve1", 101)

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


# --- sync-or-async: the same endpoint answers both ways ---


async def test_task_call_reports_the_upid_when_proxmox_queued_the_work() -> None:
    result = await build_client(data_handler("UPID:pve1:00000001:task")).update_qemu_config(
        "pve1", 101, digest="abc", net_key="net0", net_value="virtio=AA:BB:CC"
    )

    assert result["upid"] == "UPID:pve1:00000001:task"
    assert result["synchronous"] is False


async def test_task_call_reports_synchronous_when_data_is_null() -> None:
    """`data: null` means it already finished. `synchronous` says so, rather than leaving each
    caller to infer it from a missing UPID."""
    result = await build_client(data_handler(None)).update_qemu_config(
        "pve1", 101, digest="abc", net_key="net0", net_value="virtio=AA:BB:CC"
    )

    assert result["ok"] is True
    assert result["upid"] is None
    assert result["synchronous"] is True


async def test_task_call_returns_the_failure_untouched() -> None:
    result = await build_client(
        json_handler({"errors": {"digest": "digest mismatch"}}, status_code=409)
    ).update_qemu_config("pve1", 101, digest="stale", net_key="net0", net_value="virtio=AA:BB:CC")

    assert result["error_code"] == "digest_mismatch"
    assert "upid" not in result


async def test_get_task_status_normalizes_both_status_fields() -> None:
    data = {"status": "stopped", "exitstatus": "OK", "pid": 4242}

    result = await build_client(data_handler(data)).get_task_status("pve1", "UPID:pve1:task")

    assert result["ok"] is True
    assert result["upid"] == "UPID:pve1:task"
    assert result["task_status"] == "stopped"
    assert result["task_exit_status"] == "OK"
    assert result["data"] == data


async def test_get_task_status_reports_a_missing_exit_status_as_none() -> None:
    """`None`, ⊥ `""`: terminality is "an exit status is present while the task is not running",
    and an empty string would read as present."""
    result = await build_client(
        data_handler({"status": "running", "exitstatus": "  "})
    ).get_task_status("pve1", "UPID:pve1:task")

    assert result["task_status"] == "running"
    assert result["task_exit_status"] is None


async def test_get_task_status_with_a_non_object_payload_is_invalid_response() -> None:
    result = await build_client(data_handler("not a task")).get_task_status(
        "pve1", "UPID:pve1:task"
    )

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


# --- V8: the generated password does not come back out ---


async def test_set_cloudinit_password_result_carries_no_password() -> None:
    """C15/V49 generate it server-side and it stays in the caller's `execute()` scope. If the
    client echoed it into the result it would reach a `tool_runs` row and an LLM transcript."""
    result = await build_client(data_handler("UPID:pve1:task")).set_qemu_cloudinit_password(
        "pve1", 101, "generated-s3cret"
    )

    assert set(result) == {"ok", "message", "upid", "synchronous"}
    assert "generated-s3cret" not in str(result)


# --- C22: the never-implement boundary holds at the client, not only at the tool ---


@pytest.mark.parametrize(
    "method",
    [
        "get_user",
        "get_pool",
        "get_effective_permissions",
        "add_vms_to_pool",
        "remove_vms_from_pool",
    ],
)
def test_never_implement_client_methods_are_absent(method: str) -> None:
    """These five backed `proxmox_move_vms_between_pools`, its preflight, and
    `proxmox_get_user_by_email` — all C22. ⊥ port, ⊥ expose, ⊥ re-add: keeping the client method
    would leave the capability one line from exposure, and re-adding it is an owner decision."""
    assert not hasattr(ProxmoxClient, method)


# --- C7 / V48: one decrypt site, and it is the factory ---


async def test_build_from_creds_decrypts_the_secret_into_the_pveapitoken_header() -> None:
    cipher = build_cipher()
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(status_code=200, json={"data": {"version": "8.0"}}, request=request)

    client = build_proxmox_client_from_creds(
        base_url=BASE_URL,
        api_token_id="noa@pve!ops",
        encrypted_token_secret=cipher.encrypt_text("REAL_SECRET"),
        verify_ssl=True,
        cipher=cipher,
        transport=httpx.MockTransport(handler),
    )
    result = await client.get_version()
    await client.close()

    assert result["ok"] is True
    assert seen["auth"] == "PVEAPIToken=noa@pve!ops=REAL_SECRET"


async def test_build_from_creds_passes_through_an_unencrypted_column() -> None:
    """`maybe_decrypt_text`, ⊥ `decrypt_text`: a row written before encryption still works,
    which is what lets T54 migrate the column in place."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(status_code=200, json={"data": {"version": "8.0"}}, request=request)

    client = build_proxmox_client_from_creds(
        base_url=BASE_URL,
        api_token_id="root@pam!token",
        encrypted_token_secret="PLAINTEXT_SECRET",
        verify_ssl=True,
        cipher=build_cipher(),
        transport=httpx.MockTransport(handler),
    )
    async with client:
        await client.get_version()

    assert seen["auth"] == "PVEAPIToken=root@pam!token=PLAINTEXT_SECRET"


# --- lifecycle: T17 deviation (c) ---


async def test_reuses_one_underlying_client_across_calls() -> None:
    """Unlike WHM's client-per-request. Task polling makes many sequential calls per workflow,
    so the TLS handshake would otherwise be paid on each one."""
    client = build_client(data_handler({"version": "8.0"}))

    await client.get_version()
    first = client._get_client()
    await client.get_version()

    assert client._get_client() is first
    await client.close()


async def test_close_releases_the_client_and_a_later_call_rebuilds() -> None:
    client = build_client(data_handler({"version": "8.0"}))

    await client.get_version()
    first = client._get_client()
    await client.close()
    assert first.is_closed

    await client.get_version()
    assert client._get_client() is not first
    await client.close()


async def test_close_is_idempotent() -> None:
    client = build_client(data_handler({"version": "8.0"}))

    await client.get_version()
    await client.close()
    await client.close()


async def test_async_with_closes_on_exit() -> None:
    """The shape that cannot leak. `noa-old` had `close()` and called it nowhere, so every tool
    call left behind a socket and a TLS context."""
    async with build_client(data_handler({"version": "8.0"})) as client:
        await client.get_version()
        inner = client._get_client()

    assert inner.is_closed


async def test_async_with_closes_even_when_the_body_raises() -> None:
    client = build_client(data_handler({"version": "8.0"}))

    with pytest.raises(RuntimeError):
        async with client:
            await client.get_version()
            inner = client._get_client()
            raise RuntimeError("tool blew up mid-workflow")

    assert inner.is_closed
