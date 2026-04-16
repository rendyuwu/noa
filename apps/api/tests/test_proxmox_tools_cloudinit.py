from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest


@dataclass
class _Server:
    id: UUID
    name: str
    base_url: str
    api_token_id: str
    api_token_secret: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime


class _Repo:
    def __init__(self, servers: list[_Server]) -> None:
        self._servers = servers

    async def list_servers(self) -> list[_Server]:
        return self._servers

    async def get_by_id(self, *, server_id: UUID) -> _Server | None:
        for server in self._servers:
            if server.id == server_id:
                return server
        return None


@dataclass
class _Session:
    pass


@dataclass
class _ClientState:
    config: dict[str, object]
    cloudinit: dict[str, object]
    cloudinit_dump_user: dict[str, object]
    calls: list[tuple[str, object]] = field(default_factory=list)
    task_statuses: list[dict[str, object]] = field(
        default_factory=lambda: [
            {"task_status": "running", "task_exit_status": None},
            {"task_status": "stopped", "task_exit_status": "OK"},
            {"task_status": "running", "task_exit_status": None},
            {"task_status": "stopped", "task_exit_status": "OK"},
        ]
    )


def _server() -> _Server:
    now = datetime.now(UTC)
    return _Server(
        id=uuid4(),
        name="pve1",
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )


def _install_client(monkeypatch, state: _ClientState) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_qemu_config(self, node: str, vmid: int) -> dict[str, object]:
            state.calls.append(("get_qemu_config", (node, vmid)))
            _ = (node, vmid)
            return state.config

        async def get_qemu_cloudinit(self, node: str, vmid: int) -> dict[str, object]:
            state.calls.append(("get_qemu_cloudinit", (node, vmid)))
            _ = (node, vmid)
            return state.cloudinit

        async def get_qemu_cloudinit_dump_user(
            self, node: str, vmid: int
        ) -> dict[str, object]:
            state.calls.append(("get_qemu_cloudinit_dump_user", (node, vmid)))
            _ = (node, vmid)
            return state.cloudinit_dump_user

        async def set_qemu_cloudinit_password(
            self, node: str, vmid: int, new_password: str
        ) -> dict[str, object]:
            state.calls.append(
                ("set_qemu_cloudinit_password", (node, vmid, new_password))
            )
            _ = (node, vmid, new_password)
            return {
                "ok": True,
                "message": "ok",
                "data": "UPID:pve1:00000001:set-password",
            }

        async def regenerate_qemu_cloudinit(
            self, node: str, vmid: int
        ) -> dict[str, object]:
            state.calls.append(("regenerate_qemu_cloudinit", (node, vmid)))
            _ = (node, vmid)
            return {
                "ok": True,
                "message": "ok",
                "data": None,
            }

        async def get_task_status(self, node: str, upid: str) -> dict[str, object]:
            state.calls.append(("get_task_status", upid))
            _ = (node, upid)
            if state.task_statuses:
                payload = state.task_statuses.pop(0)
            else:
                payload = {"task_status": "stopped", "task_exit_status": "OK"}
            return {
                "ok": True,
                "message": "ok",
                "upid": upid,
                **payload,
                "data": {},
            }

    monkeypatch.setattr(cloudinit_tools, "ProxmoxClient", _Client)
    monkeypatch.setattr(cloudinit_tools, "_TASK_POLL_DELAY_SECONDS", 0)


@pytest.mark.asyncio
async def test_proxmox_preflight_vm_cloudinit_password_reset_returns_exact_payloads(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    config_result = {
        "ok": True,
        "message": "ok",
        "config": {"digest": "digest-1", "ciuser": "rendy"},
        "digest": "digest-1",
    }
    cloudinit_result = {
        "ok": True,
        "message": "ok",
        "data": [{"key": "ciuser", "value": "rendy"}],
    }
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        _ClientState(
            config=config_result,
            cloudinit=cloudinit_result,
            cloudinit_dump_user={"ok": True, "message": "ok", "data": "unused"},
        ),
    )

    result = await cloudinit_tools.proxmox_preflight_vm_cloudinit_password_reset(
        session=_Session(),
        server_ref="pve1",
        node=" pve1-node ",
        vmid=101,
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "server_id": str(server.id),
        "node": "pve1-node",
        "vmid": 101,
        "config": config_result,
        "cloudinit": cloudinit_result,
    }
    assert result["config"] is config_result
    assert result["cloudinit"] is cloudinit_result


@pytest.mark.asyncio
async def test_proxmox_reset_vm_cloudinit_password_returns_exact_upstream_payloads(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    config_result = {
        "ok": True,
        "message": "ok",
        "config": {"digest": "digest-1", "ciuser": "rendy"},
        "digest": "digest-1",
    }
    cloudinit_result = {
        "ok": True,
        "message": "ok",
        "data": [
            {"key": "ciuser", "value": "rendy"},
            {"key": "cipassword", "value": "********"},
        ],
    }
    dump_result = {
        "ok": True,
        "message": "ok",
        "data": "ciuser: rendy\npassword: $6$abc123$verysecret\n",
    }
    state = _ClientState(
        config=config_result,
        cloudinit=cloudinit_result,
        cloudinit_dump_user=dump_result,
    )
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await cloudinit_tools.proxmox_reset_vm_cloudinit_password(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        new_password="new-secret",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": str(server.id),
        "node": "pve1-node",
        "vmid": 101,
        "set_password_task": {
            "ok": True,
            "message": "ok",
            "data": "UPID:pve1:00000001:set-password",
        },
        "regenerate_cloudinit": {
            "ok": True,
            "message": "ok",
            "data": None,
        },
        "cloudinit": cloudinit_result,
        "cloudinit_dump_user": {
            "ok": True,
            "message": "ok",
            "data": "ciuser: rendy\npassword: [REDACTED]\n",
        },
        "verified": True,
    }
    assert result["set_password_task"] is not None
    assert result["regenerate_cloudinit"] is not None
    assert result["cloudinit"] is cloudinit_result
    assert result["cloudinit_dump_user"] is not dump_result
    assert (
        result["cloudinit_dump_user"]["data"] == "ciuser: rendy\npassword: [REDACTED]\n"
    )
    assert "$6$abc123$verysecret" not in result["cloudinit_dump_user"]["data"]
    assert state.calls == [
        ("set_qemu_cloudinit_password", ("pve1-node", 101, "new-secret")),
        ("get_task_status", "UPID:pve1:00000001:set-password"),
        ("get_task_status", "UPID:pve1:00000001:set-password"),
        ("regenerate_qemu_cloudinit", ("pve1-node", 101)),
        ("get_qemu_cloudinit", ("pve1-node", 101)),
        ("get_qemu_cloudinit_dump_user", ("pve1-node", 101)),
    ]


@pytest.mark.asyncio
async def test_proxmox_reset_vm_cloudinit_password_fails_when_task_exit_status_is_not_ok(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    state = _ClientState(
        config={
            "ok": True,
            "message": "ok",
            "config": {"digest": "digest-1", "ciuser": "rendy"},
            "digest": "digest-1",
        },
        cloudinit={
            "ok": True,
            "message": "ok",
            "data": [{"key": "ciuser", "value": "rendy"}],
        },
        cloudinit_dump_user={"ok": True, "message": "ok", "data": ""},
        task_statuses=[
            {"task_status": "stopped", "task_exit_status": "configuration error"}
        ],
    )
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await cloudinit_tools.proxmox_reset_vm_cloudinit_password(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        new_password="new-secret",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "task_failed",
        "message": "Proxmox task finished with exit status 'configuration error'",
    }
    assert state.calls == [
        ("set_qemu_cloudinit_password", ("pve1-node", 101, "new-secret")),
        ("get_task_status", "UPID:pve1:00000001:set-password"),
    ]


@pytest.mark.asyncio
async def test_proxmox_reset_vm_cloudinit_password_fails_when_verification_fails(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    state = _ClientState(
        config={
            "ok": True,
            "message": "ok",
            "config": {"digest": "digest-1", "ciuser": "rendy"},
            "digest": "digest-1",
        },
        cloudinit={
            "ok": True,
            "message": "ok",
            "data": [
                {"key": "ciuser", "value": "rendy"},
                {"key": "cipassword", "value": "********"},
            ],
        },
        cloudinit_dump_user={
            "ok": False,
            "error_code": "upstream_error",
            "message": "cloud-init dump failed",
        },
    )
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await cloudinit_tools.proxmox_reset_vm_cloudinit_password(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        new_password="new-secret",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "upstream_error",
        "message": "cloud-init dump failed",
    }
    assert state.calls == [
        ("set_qemu_cloudinit_password", ("pve1-node", 101, "new-secret")),
        ("get_task_status", "UPID:pve1:00000001:set-password"),
        ("get_task_status", "UPID:pve1:00000001:set-password"),
        ("regenerate_qemu_cloudinit", ("pve1-node", 101)),
        ("get_qemu_cloudinit", ("pve1-node", 101)),
        ("get_qemu_cloudinit_dump_user", ("pve1-node", 101)),
    ]


@pytest.mark.asyncio
async def test_proxmox_reset_vm_cloudinit_password_fails_when_cloudinit_payload_does_not_confirm_reset(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    state = _ClientState(
        config={
            "ok": True,
            "message": "ok",
            "config": {"digest": "digest-1", "ciuser": "rendy"},
            "digest": "digest-1",
        },
        cloudinit={
            "ok": True,
            "message": "ok",
            "data": [{"key": "ciuser", "value": "rendy"}],
        },
        cloudinit_dump_user={
            "ok": True,
            "message": "ok",
            "data": "ciuser: rendy\npassword: $6$abc123$verysecret\n",
        },
    )
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await cloudinit_tools.proxmox_reset_vm_cloudinit_password(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        new_password="new-secret",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "postflight_failed",
        "message": "Proxmox cloud-init payload did not confirm the password reset",
    }


@pytest.mark.asyncio
async def test_proxmox_reset_vm_cloudinit_password_fails_on_task_timeout(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    state = _ClientState(
        config={
            "ok": True,
            "message": "ok",
            "config": {"digest": "digest-1", "ciuser": "rendy"},
            "digest": "digest-1",
        },
        cloudinit={
            "ok": True,
            "message": "ok",
            "data": [{"key": "ciuser", "value": "rendy"}],
        },
        cloudinit_dump_user={"ok": True, "message": "ok", "data": "unused"},
        task_statuses=[{"task_status": "running", "task_exit_status": None}] * 5,
    )
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await cloudinit_tools.proxmox_reset_vm_cloudinit_password(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        new_password="new-secret",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "task_timeout",
        "message": "Proxmox task did not reach a terminal state before verification timed out",
    }


@pytest.mark.asyncio
async def test_proxmox_reset_vm_cloudinit_password_redacts_password_hash_in_dump(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    state = _ClientState(
        config={
            "ok": True,
            "message": "ok",
            "config": {"digest": "digest-1", "ciuser": "rendy"},
            "digest": "digest-1",
        },
        cloudinit={
            "ok": True,
            "message": "ok",
            "data": [
                {"key": "ciuser", "value": "rendy"},
                {"key": "cipassword", "value": "********"},
            ],
        },
        cloudinit_dump_user={
            "ok": True,
            "message": "ok",
            "data": "ciuser: rendy\npassword: $6$abc123$verysecret\nssh_authorized_keys:\n  - one\n",
        },
    )
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await cloudinit_tools.proxmox_reset_vm_cloudinit_password(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        new_password="new-secret",
        reason="Ticket #1661262",
    )

    assert result["ok"] is True
    assert result["cloudinit_dump_user"]["data"] == (
        "ciuser: rendy\npassword: [REDACTED]\nssh_authorized_keys:\n  - one\n"
    )
    assert "$6$abc123$verysecret" not in result["cloudinit_dump_user"]["data"]


@pytest.mark.asyncio
async def test_proxmox_reset_vm_cloudinit_password_fails_when_dump_missing_password_stanza(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import cloudinit_tools

    server = _server()
    state = _ClientState(
        config={
            "ok": True,
            "message": "ok",
            "config": {"digest": "digest-1", "ciuser": "rendy"},
            "digest": "digest-1",
        },
        cloudinit={
            "ok": True,
            "message": "ok",
            "data": [
                {"key": "ciuser", "value": "rendy"},
                {"key": "cipassword", "value": "********"},
            ],
        },
        cloudinit_dump_user={
            "ok": True,
            "message": "ok",
            "data": "ciuser: rendy\nssh_authorized_keys:\n  - one\n",
        },
    )
    monkeypatch.setattr(
        cloudinit_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await cloudinit_tools.proxmox_reset_vm_cloudinit_password(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        new_password="new-secret",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "postflight_failed",
        "message": "Proxmox cloud-init dump did not confirm the password reset",
    }
