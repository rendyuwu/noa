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
    get_pool_results: dict[str, dict[str, object]]
    get_user_result: dict[str, object]
    get_effective_permissions_result: dict[str, object]
    add_vms_to_pool_result: dict[str, object]
    remove_vms_from_pool_result: dict[str, object]
    calls: list[tuple[str, object]] = field(default_factory=list)


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
    from noa_api.proxmox.tools import pool_tools

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_pool(self, poolid: str) -> dict[str, object]:
            state.calls.append(("get_pool", poolid))
            return state.get_pool_results[poolid]

        async def get_user(self, userid: str) -> dict[str, object]:
            state.calls.append(("get_user", userid))
            return state.get_user_result

        async def get_effective_permissions(
            self, userid: str, path: str
        ) -> dict[str, object]:
            state.calls.append(("get_effective_permissions", (userid, path)))
            return state.get_effective_permissions_result

        async def add_vms_to_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            state.calls.append(("add_vms_to_pool", (poolid, vmids)))
            return state.add_vms_to_pool_result

        async def remove_vms_from_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            state.calls.append(("remove_vms_from_pool", (poolid, vmids)))
            return state.remove_vms_from_pool_result

    monkeypatch.setattr(pool_tools, "ProxmoxClient", _Client)


@pytest.mark.asyncio
async def test_proxmox_get_user_by_email_normalizes_to_proxmox_userid(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    user_result = {
        "ok": True,
        "message": "ok",
        "data": {"userid": "alice@example.com@pve", "email": "alice@example.com"},
    }
    state = _ClientState(
        get_pool_results={},
        get_user_result=user_result,
        get_effective_permissions_result={"ok": True, "message": "ok", "data": {}},
        add_vms_to_pool_result={"ok": True, "message": "ok", "data": None},
        remove_vms_from_pool_result={"ok": True, "message": "ok", "data": None},
    )
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await pool_tools.proxmox_get_user_by_email(
        session=_Session(),
        server_ref="pve1",
        email=" alice@example.com ",
    )

    assert result == user_result
    assert state.calls == [("get_user", "alice@example.com@pve")]


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_returns_wrapped_payloads(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    source_pool_result = {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_a", "members": [{"vmid": 1057}]}],
    }
    destination_pool_result = {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_b", "members": []}],
    }
    user_result = {
        "ok": True,
        "message": "ok",
        "data": {"userid": "l1@biznetgio.com@pve", "enable": 1},
    }
    permission_result = {
        "ok": True,
        "message": "ok",
        "data": {"/pool/pool_b": {"VM.Console": 1}},
    }
    state = _ClientState(
        get_pool_results={
            "pool_a": source_pool_result,
            "pool_b": destination_pool_result,
        },
        get_user_result=user_result,
        get_effective_permissions_result=permission_result,
        add_vms_to_pool_result={"ok": True, "message": "ok", "data": None},
        remove_vms_from_pool_result={"ok": True, "message": "ok", "data": None},
    )
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await pool_tools.proxmox_preflight_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool=" pool_a ",
        destination_pool="pool_b",
        vmids=[1057, 1058],
        email=" l1@biznetgio.com ",
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "server_id": str(server.id),
        "source_pool": source_pool_result,
        "destination_pool": destination_pool_result,
        "target_user": user_result,
        "destination_permission": permission_result,
        "requested_vmids": [1057, 1058],
        "normalized_userid": "l1@biznetgio.com@pve",
    }
    assert result["source_pool"] is source_pool_result
    assert result["destination_pool"] is destination_pool_result
    assert result["target_user"] is user_result
    assert result["destination_permission"] is permission_result
    assert state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_b")),
    ]


@pytest.mark.asyncio
async def test_proxmox_move_vms_between_pools_adds_before_removing_and_refetches(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    source_pool_before = {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_a", "members": [{"vmid": 1057}]}],
    }
    destination_pool_before = {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_b", "members": []}],
    }
    source_pool_after = {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_a", "members": []}],
    }
    destination_pool_after = {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_b", "members": [{"vmid": 1057}]}],
    }
    state = _ClientState(
        get_pool_results={
            "pool_a": source_pool_before,
            "pool_b": destination_pool_before,
        },
        get_user_result={
            "ok": True,
            "message": "ok",
            "data": {"userid": "l1@biznetgio.com@pve", "enable": 1},
        },
        get_effective_permissions_result={
            "ok": True,
            "message": "ok",
            "data": {"/pool/pool_b": {"VM.Console": 1}},
        },
        add_vms_to_pool_result={"ok": True, "message": "ok", "data": "UPID:ADD"},
        remove_vms_from_pool_result={
            "ok": True,
            "message": "ok",
            "data": "UPID:REMOVE",
        },
    )
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_pool(self, poolid: str) -> dict[str, object]:
            state.calls.append(("get_pool", poolid))
            if state.calls.count(("get_pool", poolid)) == 1:
                return state.get_pool_results[poolid]
            if poolid == "pool_a":
                return source_pool_after
            return destination_pool_after

        async def get_user(self, userid: str) -> dict[str, object]:
            state.calls.append(("get_user", userid))
            return state.get_user_result

        async def get_effective_permissions(
            self, userid: str, path: str
        ) -> dict[str, object]:
            state.calls.append(("get_effective_permissions", (userid, path)))
            return state.get_effective_permissions_result

        async def add_vms_to_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            state.calls.append(("add_vms_to_pool", (poolid, vmids)))
            return state.add_vms_to_pool_result

        async def remove_vms_from_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            state.calls.append(("remove_vms_from_pool", (poolid, vmids)))
            return state.remove_vms_from_pool_result

    monkeypatch.setattr(pool_tools, "ProxmoxClient", _Client)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        email="l1@biznetgio.com",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": str(server.id),
        "source_pool_before": source_pool_before,
        "destination_pool_before": destination_pool_before,
        "add_to_destination": {"ok": True, "message": "ok", "data": "UPID:ADD"},
        "remove_from_source": {
            "ok": True,
            "message": "ok",
            "data": "UPID:REMOVE",
        },
        "source_pool_after": source_pool_after,
        "destination_pool_after": destination_pool_after,
        "results": [{"vmid": 1057, "status": "changed"}],
        "verified": True,
    }

    assert state.calls.index(
        ("add_vms_to_pool", ("pool_b", [1057]))
    ) < state.calls.index(("remove_vms_from_pool", ("pool_a", [1057])))
    assert state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_b")),
        ("add_vms_to_pool", ("pool_b", [1057])),
        ("remove_vms_from_pool", ("pool_a", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
    ]


@pytest.mark.asyncio
async def test_proxmox_move_vms_between_pools_fails_closed_on_add_failure(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    state = _ClientState(
        get_pool_results={
            "pool_a": {"ok": True, "message": "ok", "data": []},
            "pool_b": {"ok": True, "message": "ok", "data": []},
        },
        get_user_result={
            "ok": True,
            "message": "ok",
            "data": {"userid": "l1@biznetgio.com@pve", "enable": 1},
        },
        get_effective_permissions_result={
            "ok": True,
            "message": "ok",
            "data": {"/pool/pool_b": {"VM.Console": 1}},
        },
        add_vms_to_pool_result={
            "ok": False,
            "error_code": "upstream_error",
            "message": "destination add failed",
        },
        remove_vms_from_pool_result={"ok": True, "message": "ok", "data": None},
    )
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )
    _install_client(monkeypatch, state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        email="l1@biznetgio.com",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "upstream_error",
        "message": "destination add failed",
    }
    assert state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_b")),
        ("add_vms_to_pool", ("pool_b", [1057])),
    ]
