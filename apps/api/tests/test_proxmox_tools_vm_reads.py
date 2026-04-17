from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
        def __init__(self, **_: object) -> None:
            pass

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
        session=object(),
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
        session=object(),
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
        session=object(),
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
    config_payload = {"name": "vm101", "cores": 2, "digest": "digest-1"}
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
                "config": config_payload,
                "digest": "digest-1",
            },
            "pending": {"ok": True, "message": "ok", "data": {}},
        },
    )

    result = await vm_read_tools.proxmox_get_vm_config(
        session=object(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "data": config_payload,
    }


@pytest.mark.asyncio
async def test_proxmox_get_vm_config_handles_upstream_failure(monkeypatch) -> None:
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
                "ok": False,
                "error_code": "http_error",
                "message": "Proxmox returned HTTP 500",
            },
            "pending": {"ok": True, "message": "ok", "data": {}},
        },
    )

    result = await vm_read_tools.proxmox_get_vm_config(
        session=object(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": False,
        "error_code": "http_error",
        "message": "Proxmox returned HTTP 500",
    }


@pytest.mark.asyncio
async def test_proxmox_get_vm_config_redacts_cipassword_in_payload(monkeypatch) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    server = _server()
    config_payload = {
        "name": "vm101",
        "cipassword": "super-secret",
        "digest": "digest-1",
    }
    monkeypatch.setattr(
        vm_read_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        {
            "status_current": {"ok": True, "message": "ok", "data": {}},
            "config": {"ok": True, "message": "ok", "config": config_payload},
            "pending": {"ok": True, "message": "ok", "data": {}},
        },
    )

    result = await vm_read_tools.proxmox_get_vm_config(
        session=object(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result["ok"] is True
    assert result["data"]["cipassword"] == "[redacted]"
    assert result["data"]["name"] == "vm101"


@pytest.mark.asyncio
async def test_proxmox_get_vm_pending_redacts_cipassword_in_payload(
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
            "status_current": {"ok": True, "message": "ok", "data": {}},
            "config": {"ok": True, "message": "ok", "data": {}},
            "pending": {
                "ok": True,
                "message": "ok",
                "data": {"cipassword": "super-secret", "digest": "abc"},
            },
        },
    )

    result = await vm_read_tools.proxmox_get_vm_pending(
        session=object(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result["ok"] is True
    assert result["data"]["cipassword"] == "[redacted]"
    assert result["data"]["digest"] == "abc"


@pytest.mark.asyncio
async def test_proxmox_get_vm_pending_handles_upstream_failure(monkeypatch) -> None:
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
            "config": {"ok": True, "message": "ok", "config": {}, "digest": "digest-1"},
            "pending": {
                "ok": False,
                "error_code": "upstream_error",
                "message": "pending failed",
            },
        },
    )

    result = await vm_read_tools.proxmox_get_vm_pending(
        session=object(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": False,
        "error_code": "upstream_error",
        "message": "pending failed",
    }


@pytest.mark.asyncio
async def test_proxmox_get_vm_status_current_preserves_ambiguous_choices(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import vm_read_tools

    now = datetime.now(UTC)
    server_a = _Server(
        id=uuid4(),
        name="pve1",
        base_url="https://proxmox-a.example.com:8006",
        api_token_id="root@pam!token-a",
        api_token_secret="SECRET-A",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    server_b = _Server(
        id=uuid4(),
        name="pve1",
        base_url="https://proxmox-b.example.com:8006",
        api_token_id="root@pam!token-b",
        api_token_secret="SECRET-B",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(
        vm_read_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server_a, server_b]),
    )

    result = await vm_read_tools.proxmox_get_vm_status_current(
        session=object(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result["ok"] is False
    assert result["error_code"] == "host_ambiguous"
    assert result["choices"] == [
        {
            "id": str(server_a.id),
            "name": "pve1",
            "base_url": "https://proxmox-a.example.com:8006",
        },
        {
            "id": str(server_b.id),
            "name": "pve1",
            "base_url": "https://proxmox-b.example.com:8006",
        },
    ]


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
        session=object(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result == {
        "ok": False,
        "error_code": "upstream_error",
        "message": "upstream failed",
    }
