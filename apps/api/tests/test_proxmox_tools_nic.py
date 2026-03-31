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
    update_calls: list[dict[str, object]] = field(default_factory=list)
    task_statuses: list[dict[str, object]] = field(
        default_factory=lambda: [
            {"task_status": "running", "task_exit_status": None},
            {"task_status": "stopped", "task_exit_status": "OK"},
        ]
    )
    next_digest: str = "digest-after"


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
    from noa_api.proxmox.tools import nic_tools

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_qemu_config(self, node: str, vmid: int) -> dict[str, object]:
            _ = (node, vmid)
            return {
                "ok": True,
                "message": "ok",
                "config": dict(state.config),
                "digest": str(state.config["digest"]),
            }

        async def update_qemu_config(
            self,
            node: str,
            vmid: int,
            *,
            digest: str,
            net_key: str,
            net_value: str,
        ) -> dict[str, object]:
            _ = (node, vmid)
            state.update_calls.append(
                {
                    "digest": digest,
                    "net_key": net_key,
                    "net_value": net_value,
                }
            )
            state.config[net_key] = net_value
            state.config["digest"] = state.next_digest
            return {
                "ok": True,
                "message": "ok",
                "upid": "UPID:pve1:00000001:task",
            }

        async def get_task_status(self, node: str, upid: str) -> dict[str, object]:
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

    monkeypatch.setattr(nic_tools, "ProxmoxClient", _Client)
    monkeypatch.setattr(nic_tools, "_TASK_POLL_DELAY_SECONDS", 0)


@pytest.mark.asyncio
async def test_proxmox_preflight_vm_nic_toggle_auto_selects_single_nic(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import nic_tools

    server = _server()
    monkeypatch.setattr(
        nic_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        _ClientState(
            config={
                "digest": "digest-1",
                "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
            }
        ),
    )

    result = await nic_tools.proxmox_preflight_vm_nic_toggle(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result["ok"] is True
    assert result["net"] == "net0"
    assert result["auto_selected_net"] is True
    assert result["link_state"] == "up"
    assert len(result["nets"]) == 1


@pytest.mark.asyncio
async def test_proxmox_preflight_vm_nic_toggle_requires_selection_when_multiple_nics(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import nic_tools

    server = _server()
    monkeypatch.setattr(
        nic_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(
        monkeypatch,
        _ClientState(
            config={
                "digest": "digest-1",
                "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
                "net1": "e1000=11:22:33:44:55:66,bridge=vmbr1,link_down=1",
            }
        ),
    )

    result = await nic_tools.proxmox_preflight_vm_nic_toggle(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
    )

    assert result["ok"] is False
    assert result["error_code"] == "net_selection_required"
    assert [net["key"] for net in result["nets"]] == ["net0", "net1"]


@pytest.mark.asyncio
async def test_proxmox_disable_vm_nic_rejects_digest_mismatch(monkeypatch) -> None:
    from noa_api.proxmox.tools import nic_tools

    server = _server()
    monkeypatch.setattr(
        nic_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    state = _ClientState(
        config={
            "digest": "digest-1",
            "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
        }
    )
    _install_client(monkeypatch, state)

    result = await nic_tools.proxmox_disable_vm_nic(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        net="net0",
        digest="stale-digest",
    )

    assert result == {
        "ok": False,
        "error_code": "digest_mismatch",
        "message": "The VM configuration digest changed. Run preflight again before retrying.",
    }
    assert state.update_calls == []


@pytest.mark.asyncio
async def test_proxmox_disable_vm_nic_fails_when_task_exit_status_is_not_ok(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import nic_tools

    server = _server()
    monkeypatch.setattr(
        nic_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    state = _ClientState(
        config={
            "digest": "digest-1",
            "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
        },
        task_statuses=[
            {"task_status": "stopped", "task_exit_status": "configuration error"}
        ],
    )
    _install_client(monkeypatch, state)

    result = await nic_tools.proxmox_disable_vm_nic(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        net="net0",
        digest="digest-1",
    )

    assert result == {
        "ok": False,
        "error_code": "task_failed",
        "message": "Proxmox task finished with exit status 'configuration error'",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "initial_net", "expected_status", "expected_link_state", "updated"),
    [
        (
            "proxmox_disable_vm_nic",
            "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,link_down=1",
            "no-op",
            "down",
            False,
        ),
        (
            "proxmox_disable_vm_nic",
            "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
            "changed",
            "down",
            True,
        ),
        (
            "proxmox_enable_vm_nic",
            "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
            "no-op",
            "up",
            False,
        ),
        (
            "proxmox_enable_vm_nic",
            "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,link_down=1",
            "changed",
            "up",
            True,
        ),
    ],
)
async def test_proxmox_nic_change_tools_handle_noop_and_change_paths(
    monkeypatch,
    tool_name: str,
    initial_net: str,
    expected_status: str,
    expected_link_state: str,
    updated: bool,
) -> None:
    from noa_api.proxmox.tools import nic_tools

    server = _server()
    monkeypatch.setattr(
        nic_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    state = _ClientState(
        config={
            "digest": "digest-1",
            "net0": initial_net,
        }
    )
    _install_client(monkeypatch, state)

    tool = getattr(nic_tools, tool_name)
    result = await tool(
        session=_Session(),
        server_ref="pve1",
        node="pve1-node",
        vmid=101,
        net="net0",
        digest="digest-1",
    )

    assert result["ok"] is True
    assert result["status"] == expected_status
    assert result["link_state"] == expected_link_state
    assert result["verified"] is True
    assert len(state.update_calls) == (1 if updated else 0)

    if tool_name == "proxmox_disable_vm_nic":
        if updated:
            assert "link_down=1" in result["after_net"]
        else:
            assert result["after_net"].endswith("link_down=1")
    else:
        assert "link_down" not in result["after_net"]
