"""Exact Proxmox client request-contract tests.

These intentionally assert literal methods, URLs, and bodies so endpoint drift
is visible and deliberate.
"""

from __future__ import annotations

import httpx


async def test_proxmox_client_get_qemu_status_current_uses_expected_path() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/nodes/pve1/qemu/101/status/current"
        )
        return httpx.Response(
            status_code=200, json={"data": {"status": "running"}}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_qemu_status_current("pve1", 101)

    assert result == {"ok": True, "message": "ok", "data": {"status": "running"}}


async def test_proxmox_client_get_qemu_pending_uses_expected_path() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/nodes/pve1/qemu/101/pending"
        )
        return httpx.Response(
            status_code=200, json={"data": {"memory": "2048"}}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_qemu_pending("pve1", 101)

    assert result == {"ok": True, "message": "ok", "data": {"memory": "2048"}}


async def test_proxmox_client_get_qemu_cloudinit_uses_expected_path() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/nodes/pve1/qemu/101/cloudinit"
        )
        return httpx.Response(
            status_code=200,
            json={"data": [{"key": "ciuser", "value": "alice"}]},
            request=request,
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_qemu_cloudinit("pve1", 101)

    assert result == {
        "ok": True,
        "message": "ok",
        "data": [{"key": "ciuser", "value": "alice"}],
    }


async def test_proxmox_client_get_qemu_cloudinit_dump_user_uses_query_param() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/nodes/pve1/qemu/101/cloudinit/dump?type=user"
        )
        return httpx.Response(
            status_code=200, json={"data": "ciuser: alice"}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_qemu_cloudinit_dump_user("pve1", 101)

    assert result == {"ok": True, "message": "ok", "data": "ciuser: alice"}


async def test_proxmox_client_set_qemu_cloudinit_password_posts_config_form_body() -> (
    None
):
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/nodes/pve1/qemu/101/config"
        )
        assert request.headers["content-type"].startswith(
            "application/x-www-form-urlencoded"
        )
        assert request.content == b"cipassword=s3cret%21"
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

    assert result == {
        "ok": True,
        "message": "ok",
        "upid": "UPID:pve1:task",
        "synchronous": False,
    }


async def test_proxmox_client_regenerate_qemu_cloudinit_uses_put() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/nodes/pve1/qemu/101/cloudinit"
        )
        assert request.content == b""
        return httpx.Response(
            status_code=200, json={"data": "UPID:pve1:regen"}, request=request
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.regenerate_qemu_cloudinit("pve1", 101)

    assert result == {"ok": True, "message": "ok", "data": "UPID:pve1:regen"}


async def test_proxmox_client_get_user_uses_expected_path() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/access/users/alice@pve"
        )
        return httpx.Response(
            status_code=200,
            json={"data": {"email": "alice@example.com"}},
            request=request,
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_user("alice@pve")

    assert result == {
        "ok": True,
        "message": "ok",
        "data": {"email": "alice@example.com"},
    }


async def test_proxmox_client_get_pool_sends_poolid_query_param() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/pools?poolid=pool_756527_stage"
        )
        return httpx.Response(
            status_code=200,
            json={"data": [{"poolid": "pool_756527_stage", "members": []}]},
            request=request,
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_pool("pool_756527_stage")

    assert result == {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_756527_stage", "members": []}],
    }


async def test_proxmox_client_get_effective_permissions_uses_query_params() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://proxmox.example.com:8006/api2/json/access/permissions?userid=alice%40pve&path=%2Fpool%2Fpool_a"
        )
        return httpx.Response(
            status_code=200,
            json={"data": {"/pool/pool_a": {"VM.Console": 1}}},
            request=request,
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_effective_permissions("alice@pve", "/pool/pool_a")

    assert result == {
        "ok": True,
        "message": "ok",
        "data": {"/pool/pool_a": {"VM.Console": 1}},
    }


async def test_proxmox_client_add_vms_to_pool_posts_allow_move_form() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert str(request.url) == "https://proxmox.example.com:8006/api2/json/pools"
        assert request.content == b"poolid=pool_b&vms=1057%2C1058&allow-move=1"
        return httpx.Response(status_code=200, json={"data": None}, request=request)

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.add_vms_to_pool("pool_b", [1057, 1058])

    assert result == {"ok": True, "message": "ok", "data": None}


async def test_proxmox_client_remove_vms_from_pool_posts_delete_form() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert str(request.url) == "https://proxmox.example.com:8006/api2/json/pools"
        assert request.content == b"poolid=pool_a&vms=1057%2C1058&delete=1"
        return httpx.Response(status_code=200, json={"data": None}, request=request)

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.remove_vms_from_pool("pool_a", [1057, 1058])

    assert result == {"ok": True, "message": "ok", "data": None}


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
    first_internal = client._get_client()
    await client.close()
    assert first_internal.is_closed
    # After close, a new internal client is created on next call
    await client.get_version()
    second_internal = client._get_client()
    assert second_internal is not first_internal


async def test_proxmox_client_set_cloudinit_password_handles_null_upid() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"data": None}, request=request)

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


async def test_proxmox_client_set_cloudinit_password_returns_upid_when_present() -> (
    None
):
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
        return httpx.Response(status_code=200, json={"data": None}, request=request)

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
