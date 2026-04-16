from __future__ import annotations

from dataclasses import dataclass
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


def _install_client(monkeypatch, responses: dict[str, dict[str, object]]) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_qemu_status_current(
            self, node: str, vmid: int
        ) -> dict[str, object]:
            _ = (node, vmid)
            return responses["status_current"]

        async def get_qemu_config(self, node: str, vmid: int) -> dict[str, object]:
            _ = (node, vmid)
            return responses["config"]

        async def get_qemu_pending(self, node: str, vmid: int) -> dict[str, object]:
            _ = (node, vmid)
            return responses["pending"]

    monkeypatch.setattr(vm_read_tools, "ProxmoxClient", _Client)


@pytest.mark.asyncio
async def test_proxmox_get_vm_status_current_returns_upstream_data(monkeypatch) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    server = _server()
    monkeypatch.setattr(
        vm_read_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        {
            "status_current": {
                "ok": True,
                "message": "ok",
                "data": {"status": "running", "uptime": 123},
            },
            "config": {"ok": True, "message": "ok", "data": {}},
            "pending": {"ok": True, "message": "ok", "data": {}},
        },
    )

    result = await vm_read_tools.proxmox_get_vm_status_current(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "data": {"status": "running", "uptime": 123},
    }


@pytest.mark.asyncio
async def test_proxmox_get_vm_config_preserves_resolution_errors(monkeypatch) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    monkeypatch.setattr(
        vm_read_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([]),
    )
    _install_client(
        monkeypatch,
        {
            "status_current": {"ok": True, "message": "ok", "data": {}},
            "config": {"ok": True, "message": "ok", "data": {}},
            "pending": {"ok": True, "message": "ok", "data": {}},
        },
    )

    result = await vm_read_tools.proxmox_get_vm_config(
        session=_Session(),
        server_ref="missing",
        node="pve1-node",
        vmid=101,
    )

    assert result["ok"] is False
    assert result["error_code"] == "host_not_found"


@pytest.mark.asyncio
async def test_proxmox_get_vm_pending_returns_upstream_data(monkeypatch) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    server = _server()
    monkeypatch.setattr(
        vm_read_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        {
            "status_current": {"ok": True, "message": "ok", "data": {}},
            "config": {"ok": True, "message": "ok", "data": {}},
            "pending": {
                "ok": True,
                "message": "ok",
                "data": {"digest": "abc", "pending": []},
            },
        },
    )

    result = await vm_read_tools.proxmox_get_vm_pending(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "data": {"digest": "abc", "pending": []},
    }


@pytest.mark.asyncio
async def test_proxmox_get_vm_config_returns_real_config_payload(monkeypatch) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    server = _server()
    monkeypatch.setattr(
        vm_read_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        {
            "status_current": {"ok": True, "message": "ok", "data": {}},
            "config": {
                "ok": True,
                "message": "ok",
                "config": {"name": "vm101", "cores": 2},
                "digest": "digest-1",
            },
            "pending": {"ok": True, "message": "ok", "data": {}},
        },
    )

    result = await vm_read_tools.proxmox_get_vm_config(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "data": {"name": "vm101", "cores": 2},
    }


@pytest.mark.asyncio
async def test_proxmox_get_vm_status_current_preserves_client_errors(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    server = _server()
    monkeypatch.setattr(
        vm_read_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        {
            "status_current": {
                "ok": False,
                "error_code": "upstream_error",
                "message": "upstream failed",
            },
            "config": {"ok": True, "message": "ok", "data": {}},
            "pending": {"ok": True, "message": "ok", "data": {}},
        },
    )

    result = await vm_read_tools.proxmox_get_vm_status_current(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": False,
        "error_code": "upstream_error",
        "message": "upstream failed",
    }
