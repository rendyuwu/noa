"""Proxmox VE client — exact request contracts (T17, V69).

Ported from `noa-old` branch `MCP` (`test_proxmox_client_endpoints.py`), minus the C22 methods.

These assert literal HTTP verbs, full URLs and form bodies on purpose. The port's whole claim is
that the endpoint surface came across unchanged (V69), and Proxmox's API is unforgiving about the
details: a config write is `POST` with a form body while a cloud-init regeneration is `PUT` with
an empty one, `cloudinit/dump` needs `?type=user`, and a `netN` value carries `=` and `,`
characters that must arrive percent-encoded. Endpoint drift should fail a test, ⊥ a live host.

Failure classification, digest handling and lifecycle live in `test_proxmox_client.py`.
"""

from __future__ import annotations

import httpx

from support.proxmox import BASE_URL, Handler, build_client


def _recording_handler(seen: dict[str, object], *, payload: object = None) -> Handler:
    """Record verb/URL/body/content-type, answer with a Proxmox success envelope."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["content"] = request.content
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(status_code=200, json={"data": payload}, request=request)

    return handler


# --- READ paths ---


async def test_get_version_uses_the_version_path() -> None:
    seen: dict[str, object] = {}

    result = await build_client(_recording_handler(seen, payload={"version": "8.0"})).get_version()

    assert seen["method"] == "GET"
    assert seen["url"] == f"{BASE_URL}/api2/json/version"
    assert result == {"ok": True, "message": "ok", "data": {"version": "8.0"}}


async def test_get_qemu_status_current_uses_the_exact_node_path() -> None:
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload={"status": "running"})
    ).get_qemu_status_current("pve1", 101)

    assert seen["method"] == "GET"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/status/current"
    assert result == {"ok": True, "message": "ok", "data": {"status": "running"}}


async def test_get_qemu_pending_uses_the_pending_path() -> None:
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload={"memory": "2048"})
    ).get_qemu_pending("pve1", 101)

    assert seen["method"] == "GET"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/pending"
    assert result == {"ok": True, "message": "ok", "data": {"memory": "2048"}}


async def test_get_qemu_config_uses_the_config_path() -> None:
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload={"digest": "abc123", "net0": "virtio=AA:BB:CC"})
    ).get_qemu_config("pve1", 101)

    assert seen["method"] == "GET"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/config"
    assert result["digest"] == "abc123"


async def test_get_qemu_cloudinit_uses_the_cloudinit_path() -> None:
    seen: dict[str, object] = {}
    entries = [{"key": "ciuser", "value": "alice"}]

    result = await build_client(_recording_handler(seen, payload=entries)).get_qemu_cloudinit(
        "pve1", 101
    )

    assert seen["method"] == "GET"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/cloudinit"
    assert result == {"ok": True, "message": "ok", "data": entries}


async def test_get_qemu_cloudinit_dump_user_sends_the_type_query_param() -> None:
    """`?type=user` selects the rendered user-data document — without it Proxmox answers the
    wrong section, and T27 verifies the new password against this one (V62)."""
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload="ciuser: alice")
    ).get_qemu_cloudinit_dump_user("pve1", 101)

    assert seen["method"] == "GET"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/cloudinit/dump?type=user"
    assert result == {"ok": True, "message": "ok", "data": "ciuser: alice"}


async def test_get_task_status_uses_the_node_task_path() -> None:
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload={"status": "stopped", "exitstatus": "OK"})
    ).get_task_status("pve1", "UPID:pve1:00000001:task")

    assert seen["method"] == "GET"
    assert seen["url"] == (f"{BASE_URL}/api2/json/nodes/pve1/tasks/UPID:pve1:00000001:task/status")
    assert result["task_status"] == "stopped"


# --- CHANGE paths ---


async def test_set_qemu_cloudinit_password_posts_only_cipassword() -> None:
    """`cipassword` alone: `ciuser` is deliberately absent, so resetting the password cannot
    rename the guest account."""
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload="UPID:pve1:task")
    ).set_qemu_cloudinit_password("pve1", 101, "s3cret!")

    assert seen["method"] == "POST"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/config"
    assert str(seen["content_type"]).startswith("application/x-www-form-urlencoded")
    assert seen["content"] == b"cipassword=s3cret%21"
    assert result == {
        "ok": True,
        "message": "ok",
        "upid": "UPID:pve1:task",
        "synchronous": False,
    }


async def test_update_qemu_config_posts_the_digest_beside_the_net_line() -> None:
    """The digest travels in the same body as the change it guards — that is what makes the
    write a compare-and-set. The `netN` value's `=` and `,` arrive percent-encoded."""
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload="UPID:pve1:00000001:task")
    ).update_qemu_config(
        "pve1",
        101,
        digest="abc123",
        net_key="net0",
        net_value="virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
    )

    assert seen["method"] == "POST"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/config"
    assert str(seen["content_type"]).startswith("application/x-www-form-urlencoded")
    assert seen["content"] == (
        b"digest=abc123&net0=virtio%3DAA%3ABB%3ACC%2Cbridge%3Dvmbr0%2Clink_down%3D1"
    )
    assert result == {
        "ok": True,
        "message": "ok",
        "upid": "UPID:pve1:00000001:task",
        "synchronous": False,
    }


async def test_regenerate_qemu_cloudinit_puts_an_empty_body() -> None:
    """`PUT`, no body. Ported as-is (V69): the UPID comes back as `data`, ⊥ as `upid`, because
    this call does not go through the task wrapper — see the method docstring."""
    seen: dict[str, object] = {}

    result = await build_client(
        _recording_handler(seen, payload="UPID:pve1:regen")
    ).regenerate_qemu_cloudinit("pve1", 101)

    assert seen["method"] == "PUT"
    assert seen["url"] == f"{BASE_URL}/api2/json/nodes/pve1/qemu/101/cloudinit"
    assert seen["content"] == b""
    assert result == {"ok": True, "message": "ok", "data": "UPID:pve1:regen"}
