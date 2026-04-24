from __future__ import annotations

import httpx


async def test_proxmox_client_maps_401_to_auth_failed() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, text="unauthorized", request=request)

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_version()

    assert result["ok"] is False
    assert result["error_code"] == "auth_failed"


async def test_proxmox_client_maps_errors_payload_to_digest_mismatch() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=409,
            json={
                "errors": {
                    "digest": "configuration file has been modified by another user (digest mismatch)"
                }
            },
            request=request,
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_qemu_config("pve1", 101)

    assert result["ok"] is False
    assert result["error_code"] == "digest_mismatch"


async def test_proxmox_client_update_qemu_config_posts_form_encoded_body() -> None:
    from noa_api.proxmox.integrations.client import ProxmoxClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["content-type"].startswith(
            "application/x-www-form-urlencoded"
        )
        assert (
            request.content
            == b"digest=abc123&net0=virtio%3DAA%3ABB%3ACC%2Cbridge%3Dvmbr0%2Clink_down%3D1"
        )
        return httpx.Response(
            status_code=200,
            json={"data": "UPID:pve1:00000001:task"},
            request=request,
        )

    client = ProxmoxClient(
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )

    result = await client.update_qemu_config(
        "pve1",
        101,
        digest="abc123",
        net_key="net0",
        net_value="virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "upid": "UPID:pve1:00000001:task",
        "synchronous": False,
    }
