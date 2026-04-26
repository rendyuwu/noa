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


@dataclass
class _ScriptedClientState:
    get_pool_results: dict[str, list[dict[str, object]]]
    get_user_result: dict[str, object]
    get_effective_permissions_result: dict[str, object]
    add_vms_to_pool_result: dict[str, object]
    remove_vms_from_pool_result: dict[str, object]
    calls: list[tuple[str, object]] = field(default_factory=list)
    get_pool_call_counts: dict[str, int] = field(default_factory=dict)


class _ScriptedPoolClient:
    def __init__(self, state: _ScriptedClientState, **kwargs: Any) -> None:
        _ = kwargs
        self._state = state

    async def get_pool(self, poolid: str) -> dict[str, object]:
        self._state.calls.append(("get_pool", poolid))
        call_count = self._state.get_pool_call_counts.get(poolid, 0)
        pool_results = self._state.get_pool_results[poolid]
        if call_count >= len(pool_results):
            raise AssertionError(
                f"No scripted get_pool result for {poolid} call {call_count + 1}"
            )
        self._state.get_pool_call_counts[poolid] = call_count + 1
        return pool_results[call_count]

    async def get_user(self, userid: str) -> dict[str, object]:
        self._state.calls.append(("get_user", userid))
        return self._state.get_user_result

    async def get_effective_permissions(
        self, userid: str, path: str
    ) -> dict[str, object]:
        self._state.calls.append(("get_effective_permissions", (userid, path)))
        return self._state.get_effective_permissions_result

    async def add_vms_to_pool(self, poolid: str, vmids: list[int]) -> dict[str, object]:
        self._state.calls.append(("add_vms_to_pool", (poolid, vmids)))
        return self._state.add_vms_to_pool_result

    async def remove_vms_from_pool(
        self, poolid: str, vmids: list[int]
    ) -> dict[str, object]:
        self._state.calls.append(("remove_vms_from_pool", (poolid, vmids)))
        return self._state.remove_vms_from_pool_result


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


def _pool_payload(poolid: str, members: list[dict[str, object]]) -> dict[str, object]:
    return {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": poolid, "members": members}],
    }


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

    from noa_api.proxmox.tools import _shared

    monkeypatch.setattr(_shared, "ProxmoxClient", _Client)
    monkeypatch.setattr(pool_tools, "ProxmoxClient", _Client)


def _install_scripted_client(monkeypatch, state: _ScriptedClientState) -> None:
    from noa_api.proxmox.tools import pool_tools
    from noa_api.proxmox.tools import _shared

    def _factory(**kwargs: Any) -> _ScriptedPoolClient:
        return _ScriptedPoolClient(state, **kwargs)

    monkeypatch.setattr(_shared, "ProxmoxClient", _factory)
    monkeypatch.setattr(
        pool_tools,
        "ProxmoxClient",
        _factory,
    )


def test_normalize_proxmox_userid_appends_pve_realm() -> None:
    from noa_api.proxmox.tools.pool_tools import _normalize_proxmox_userid

    assert _normalize_proxmox_userid("alice@example.com") == "alice@example.com@pve"
    assert _normalize_proxmox_userid("  alice@example.com  ") == "alice@example.com@pve"


def test_normalize_proxmox_userid_does_not_double_append_pve() -> None:
    from noa_api.proxmox.tools.pool_tools import _normalize_proxmox_userid

    assert _normalize_proxmox_userid("alice@example.com@pve") == "alice@example.com@pve"
    assert _normalize_proxmox_userid("alice@pve") == "alice@pve"


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
        "data": [{"poolid": "pool_a", "members": [{"vmid": 1057}, {"vmid": 1058}]}],
    }
    destination_pool_result = {
        "ok": True,
        "message": "ok",
        "data": [{"poolid": "pool_b", "members": []}],
    }
    old_user_result = {
        "ok": True,
        "message": "ok",
        "data": {"userid": "l1@biznetgio.com@pve", "enable": 1},
    }
    new_user_result = {
        "ok": True,
        "message": "ok",
        "data": {"userid": "l2@biznetgio.com@pve", "enable": 1},
    }
    source_permission_result = {
        "ok": True,
        "message": "ok",
        "data": {"/pool/pool_a": {"VM.Allocate": 1}},
    }
    destination_permission_result = {
        "ok": True,
        "message": "ok",
        "data": {"/pool/pool_b": {"VM.Allocate": 1}},
    }

    calls: list[tuple[str, object]] = []

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_pool(self, poolid: str) -> dict[str, object]:
            calls.append(("get_pool", poolid))
            return {"pool_a": source_pool_result, "pool_b": destination_pool_result}[
                poolid
            ]

        async def get_user(self, userid: str) -> dict[str, object]:
            calls.append(("get_user", userid))
            return {
                "l1@biznetgio.com@pve": old_user_result,
                "l2@biznetgio.com@pve": new_user_result,
            }[userid]

        async def get_effective_permissions(
            self, userid: str, path: str
        ) -> dict[str, object]:
            calls.append(("get_effective_permissions", (userid, path)))
            return {
                ("l1@biznetgio.com@pve", "/pool/pool_a"): source_permission_result,
                ("l2@biznetgio.com@pve", "/pool/pool_b"): destination_permission_result,
            }[(userid, path)]

        async def add_vms_to_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            calls.append(("add_vms_to_pool", (poolid, vmids)))
            return {"ok": True, "message": "ok", "data": None}

        async def remove_vms_from_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            calls.append(("remove_vms_from_pool", (poolid, vmids)))
            return {"ok": True, "message": "ok", "data": None}

    from noa_api.proxmox.tools import _shared

    monkeypatch.setattr(_shared, "ProxmoxClient", _Client)
    monkeypatch.setattr(pool_tools, "ProxmoxClient", _Client)
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )

    result = await pool_tools.proxmox_preflight_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool=" pool_a ",
        destination_pool="pool_b",
        vmids=[1057, 1058],
        old_email=" l1@biznetgio.com ",
        new_email="l2@biznetgio.com",
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "server_id": str(server.id),
        "source_pool": source_pool_result,
        "destination_pool": destination_pool_result,
        "old_user": old_user_result,
        "new_user": new_user_result,
        "source_permission": source_permission_result,
        "destination_permission": destination_permission_result,
        "requested_vmids": [1057, 1058],
        "normalized_old_userid": "l1@biznetgio.com@pve",
        "normalized_new_userid": "l2@biznetgio.com@pve",
    }
    assert result["source_pool"] is source_pool_result
    assert result["destination_pool"] is destination_pool_result
    assert result["old_user"] is old_user_result
    assert result["new_user"] is new_user_result
    assert result["source_permission"] is source_permission_result
    assert result["destination_permission"] is destination_permission_result
    assert calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
    ]


@pytest.mark.asyncio
async def test_proxmox_move_vms_between_pools_fails_when_source_pool_changed_before_mutation(
    monkeypatch,
) -> None:
    """TOCTOU: if a VMID disappears from the source pool between preflight and
    mutation, the move must fail with ``source_pool_changed``."""
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )

    # Scripted pool results:
    #   pool_a call 1 (preflight): vmid 1057 present
    #   pool_a call 2 (TOCTOU re-verify): vmid 1057 GONE
    scripted_state = _ScriptedClientState(
        get_pool_results={
            "pool_a": [
                _pool_payload(
                    "pool_a",
                    [
                        {
                            "vmid": 1057,
                            "name": "vm1",
                            "node": "pve1",
                            "status": "running",
                        }
                    ],
                ),
                _pool_payload("pool_a", []),  # TOCTOU: vmid gone
            ],
            "pool_b": [
                _pool_payload("pool_b", []),
            ],
        },
        get_user_result={
            "ok": True,
            "message": "ok",
            "data": {"email": "alice@example.com"},
        },
        get_effective_permissions_result={
            "ok": True,
            "message": "ok",
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
        },
        add_vms_to_pool_result={"ok": True, "message": "ok", "data": None},
        remove_vms_from_pool_result={"ok": True, "message": "ok", "data": None},
    )

    _install_scripted_client(monkeypatch, scripted_state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="alice@example.com",
        new_email="bob@example.com",
        reason="Ticket #123",
    )

    assert result["ok"] is False
    assert result["error_code"] == "source_pool_changed"
    # Mutation (add_vms_to_pool) should NOT have been called
    assert not any(call[0] == "add_vms_to_pool" for call in scripted_state.calls)
    # Verify both users were looked up and correct permission paths checked
    assert ("get_user", "alice@example.com@pve") in scripted_state.calls
    assert ("get_user", "bob@example.com@pve") in scripted_state.calls
    assert (
        "get_effective_permissions",
        ("alice@example.com@pve", "/pool/pool_a"),
    ) in scripted_state.calls
    assert (
        "get_effective_permissions",
        ("bob@example.com@pve", "/pool/pool_b"),
    ) in scripted_state.calls


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_rejects_same_source_and_destination(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    state = _ClientState(
        get_pool_results={},
        get_user_result={"ok": True, "message": "ok", "data": {}},
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

    result = await pool_tools.proxmox_preflight_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_a",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
    )

    assert result == {
        "ok": False,
        "error_code": "invalid_request",
        "message": "Source and destination pools must be different",
    }
    assert state.calls == []


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_rejects_empty_vmids(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    state = _ClientState(
        get_pool_results={},
        get_user_result={"ok": True, "message": "ok", "data": {}},
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

    result = await pool_tools.proxmox_preflight_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
    )

    assert result == {
        "ok": False,
        "error_code": "invalid_request",
        "message": "At least one VMID is required",
    }
    assert state.calls == []


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_rejects_empty_source_permission(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    permissions: dict[tuple[str, str], dict[str, object]] = {
        ("l1@biznetgio.com@pve", "/pool/pool_a"): {
            "ok": True,
            "message": "ok",
            "data": {},
        },
        ("l2@biznetgio.com@pve", "/pool/pool_b"): {
            "ok": True,
            "message": "ok",
            "data": {"/pool/pool_b": {"VM.Allocate": 1}},
        },
    }

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_pool(self, poolid: str) -> dict[str, object]:
            return _pool_payload(
                poolid, [{"vmid": 1057}] if poolid == "pool_a" else []
            )

        async def get_user(self, userid: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "data": {"userid": userid, "enable": 1},
            }

        async def get_effective_permissions(
            self, userid: str, path: str
        ) -> dict[str, object]:
            return permissions[(userid, path)]

        async def add_vms_to_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            return {"ok": True, "message": "ok", "data": None}

        async def remove_vms_from_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            return {"ok": True, "message": "ok", "data": None}

    from noa_api.proxmox.tools import _shared

    monkeypatch.setattr(_shared, "ProxmoxClient", _Client)
    monkeypatch.setattr(pool_tools, "ProxmoxClient", _Client)
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )

    result = await pool_tools.proxmox_preflight_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
    )

    assert result == {
        "ok": False,
        "error_code": "permission_required",
        "message": "Old email does not have permissions on the source pool",
    }


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_rejects_empty_destination_permission(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    permissions: dict[tuple[str, str], dict[str, object]] = {
        ("l1@biznetgio.com@pve", "/pool/pool_a"): {
            "ok": True,
            "message": "ok",
            "data": {"/pool/pool_a": {"VM.Allocate": 1}},
        },
        ("l2@biznetgio.com@pve", "/pool/pool_b"): {
            "ok": True,
            "message": "ok",
            "data": {},
        },
    }

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        async def get_pool(self, poolid: str) -> dict[str, object]:
            return _pool_payload(
                poolid, [{"vmid": 1057}] if poolid == "pool_a" else []
            )

        async def get_user(self, userid: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "data": {"userid": userid, "enable": 1},
            }

        async def get_effective_permissions(
            self, userid: str, path: str
        ) -> dict[str, object]:
            return permissions[(userid, path)]

        async def add_vms_to_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            return {"ok": True, "message": "ok", "data": None}

        async def remove_vms_from_pool(
            self, poolid: str, vmids: list[int]
        ) -> dict[str, object]:
            return {"ok": True, "message": "ok", "data": None}

    from noa_api.proxmox.tools import _shared

    monkeypatch.setattr(_shared, "ProxmoxClient", _Client)
    monkeypatch.setattr(pool_tools, "ProxmoxClient", _Client)
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )

    result = await pool_tools.proxmox_preflight_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
    )

    assert result == {
        "ok": False,
        "error_code": "permission_required",
        "message": "New email does not have permissions on the destination pool",
    }


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_rejects_same_old_and_new_email(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    state = _ClientState(
        get_pool_results={},
        get_user_result={"ok": True, "message": "ok", "data": {}},
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

    result = await pool_tools.proxmox_preflight_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l1@biznetgio.com",
    )

    assert result == {
        "ok": False,
        "error_code": "invalid_request",
        "message": "Old email and new email must be different for a PIC change",
    }
    assert state.calls == []


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_rejects_missing_source_vmids(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    state = _ClientState(
        get_pool_results={
            "pool_a": _pool_payload("pool_a", [{"vmid": 1057}]),
            "pool_b": _pool_payload("pool_b", []),
        },
        get_user_result={
            "ok": True,
            "message": "ok",
            "data": {"userid": "l1@biznetgio.com@pve", "enable": 1},
        },
        get_effective_permissions_result={
            "ok": True,
            "message": "ok",
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
        },
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
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057, 1058],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
    )

    assert result == {
        "ok": False,
        "error_code": "vmid_not_in_source_pool",
        "message": "One or more requested VMIDs were not found in the source pool",
    }


@pytest.mark.asyncio
async def test_proxmox_preflight_move_vms_between_pools_rejects_malformed_source_pool_payload(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    state = _ClientState(
        get_pool_results={
            "pool_a": {"ok": True, "message": "ok", "data": {"poolid": "pool_a"}},
            "pool_b": _pool_payload("pool_b", []),
        },
        get_user_result={
            "ok": True,
            "message": "ok",
            "data": {"userid": "l1@biznetgio.com@pve", "enable": 1},
        },
        get_effective_permissions_result={
            "ok": True,
            "message": "ok",
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
        },
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
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
    )

    assert result == {
        "ok": False,
        "error_code": "invalid_response",
        "message": "Proxmox returned an unexpected pool payload",
    }


@pytest.mark.asyncio
async def test_proxmox_move_vms_between_pools_fails_when_postflight_does_not_confirm_move(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    source_pool_before = _pool_payload("pool_a", [{"vmid": 1057}])
    destination_pool_before = _pool_payload("pool_b", [])
    source_pool_after = _pool_payload("pool_a", [{"vmid": 1057}])
    destination_pool_after = _pool_payload("pool_b", [])
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
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
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

    scripted_state = _ScriptedClientState(
        get_pool_results={
            "pool_a": [source_pool_before, source_pool_before, source_pool_after],
            "pool_b": [destination_pool_before, destination_pool_after],
        },
        get_user_result=state.get_user_result,
        get_effective_permissions_result=state.get_effective_permissions_result,
        add_vms_to_pool_result=state.add_vms_to_pool_result,
        remove_vms_from_pool_result=state.remove_vms_from_pool_result,
    )
    _install_scripted_client(monkeypatch, scripted_state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "postflight_failed",
        "message": "Proxmox pool move verification did not confirm the requested VMIDs",
    }
    assert scripted_state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
        ("get_pool", "pool_a"),
        ("add_vms_to_pool", ("pool_b", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_pool_after_add", "destination_pool_after_add"),
    [
        (
            {"ok": True, "message": "ok", "data": {"poolid": "pool_a"}},
            _pool_payload("pool_b", [{"vmid": 1057}]),
        ),
        (
            _pool_payload("pool_a", []),
            {"ok": True, "message": "ok", "data": {"poolid": "pool_b"}},
        ),
    ],
)
async def test_proxmox_move_vms_between_pools_rejects_malformed_refetch_payload_after_add_step(
    monkeypatch,
    source_pool_after_add: dict[str, object],
    destination_pool_after_add: dict[str, object],
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    source_pool_before = _pool_payload("pool_a", [{"vmid": 1057}])
    state = _ScriptedClientState(
        get_pool_results={
            "pool_a": [
                source_pool_before,
                source_pool_before,
                source_pool_after_add,
            ],
            "pool_b": [
                _pool_payload("pool_b", []),
                destination_pool_after_add,
            ],
        },
        get_user_result={
            "ok": True,
            "message": "ok",
            "data": {"userid": "l1@biznetgio.com@pve", "enable": 1},
        },
        get_effective_permissions_result={
            "ok": True,
            "message": "ok",
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
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
    _install_scripted_client(monkeypatch, state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "invalid_response",
        "message": "Proxmox returned an unexpected pool payload",
    }
    assert state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
        ("get_pool", "pool_a"),
        ("add_vms_to_pool", ("pool_b", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
    ]


@pytest.mark.asyncio
async def test_proxmox_move_vms_between_pools_skips_remove_when_add_already_moves_vmids(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    source_pool_before = _pool_payload("pool_a", [{"vmid": 1057}])
    destination_pool_before = _pool_payload("pool_b", [])
    source_pool_after_add = _pool_payload("pool_a", [])
    destination_pool_after_add = _pool_payload("pool_b", [{"vmid": 1057}])
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
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
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

    scripted_state = _ScriptedClientState(
        get_pool_results={
            "pool_a": [source_pool_before, source_pool_before, source_pool_after_add],
            "pool_b": [destination_pool_before, destination_pool_after_add],
        },
        get_user_result=state.get_user_result,
        get_effective_permissions_result=state.get_effective_permissions_result,
        add_vms_to_pool_result=state.add_vms_to_pool_result,
        remove_vms_from_pool_result=state.remove_vms_from_pool_result,
    )
    _install_scripted_client(monkeypatch, scripted_state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
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
        "remove_from_source": None,
        "source_pool_after": source_pool_after_add,
        "destination_pool_after": destination_pool_after_add,
        "results": [{"vmid": 1057, "status": "changed"}],
        "verified": True,
    }
    assert scripted_state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
        ("get_pool", "pool_a"),
        ("add_vms_to_pool", ("pool_b", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
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
    source_pool_after_add = {
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
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
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

    scripted_state = _ScriptedClientState(
        get_pool_results={
            "pool_a": [
                source_pool_before,
                source_pool_before,
                source_pool_after_add,
                source_pool_after,
            ],
            "pool_b": [
                destination_pool_before,
                destination_pool_after,
                destination_pool_after,
            ],
        },
        get_user_result=state.get_user_result,
        get_effective_permissions_result=state.get_effective_permissions_result,
        add_vms_to_pool_result=state.add_vms_to_pool_result,
        remove_vms_from_pool_result=state.remove_vms_from_pool_result,
    )
    _install_scripted_client(monkeypatch, scripted_state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
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

    assert scripted_state.calls.index(
        ("add_vms_to_pool", ("pool_b", [1057]))
    ) < scripted_state.calls.index(("remove_vms_from_pool", ("pool_a", [1057])))
    assert scripted_state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
        ("get_pool", "pool_a"),
        ("add_vms_to_pool", ("pool_b", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
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
            "pool_a": _pool_payload("pool_a", [{"vmid": 1057}]),
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
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
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
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
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
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
        ("get_pool", "pool_a"),
        ("add_vms_to_pool", ("pool_b", [1057])),
    ]


@pytest.mark.asyncio
async def test_proxmox_move_vms_between_pools_fails_when_remove_step_fails(
    monkeypatch,
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    source_pool_before = _pool_payload("pool_a", [{"vmid": 1057}])
    destination_pool_before = _pool_payload("pool_b", [])
    source_pool_after_add = _pool_payload("pool_a", [{"vmid": 1057}])
    destination_pool_after_add = _pool_payload("pool_b", [{"vmid": 1057}])
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
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
        },
        add_vms_to_pool_result={"ok": True, "message": "ok", "data": "UPID:ADD"},
        remove_vms_from_pool_result={
            "ok": False,
            "error_code": "upstream_error",
            "message": "source remove failed",
        },
    )
    monkeypatch.setattr(
        pool_tools,
        "SQLProxmoxServerRepository",
        lambda session: _Repo([server]),
    )

    scripted_state = _ScriptedClientState(
        get_pool_results={
            "pool_a": [source_pool_before, source_pool_before, source_pool_after_add],
            "pool_b": [destination_pool_before, destination_pool_after_add],
        },
        get_user_result=state.get_user_result,
        get_effective_permissions_result=state.get_effective_permissions_result,
        add_vms_to_pool_result=state.add_vms_to_pool_result,
        remove_vms_from_pool_result=state.remove_vms_from_pool_result,
    )
    _install_scripted_client(monkeypatch, scripted_state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "upstream_error",
        "message": "source remove failed",
    }
    assert scripted_state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
        ("get_pool", "pool_a"),
        ("add_vms_to_pool", ("pool_b", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("remove_vms_from_pool", ("pool_a", [1057])),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_pool_after_move", "destination_pool_after_move"),
    [
        (
            {"ok": True, "message": "ok", "data": {"poolid": "pool_a"}},
            _pool_payload("pool_b", [{"vmid": 1057}]),
        ),
        (
            _pool_payload("pool_a", []),
            {"ok": True, "message": "ok", "data": {"poolid": "pool_b"}},
        ),
    ],
)
async def test_proxmox_move_vms_between_pools_rejects_malformed_refetch_payload_after_move_path(
    monkeypatch,
    source_pool_after_move: dict[str, object],
    destination_pool_after_move: dict[str, object],
) -> None:
    from noa_api.proxmox.tools import pool_tools

    server = _server()
    source_pool_before = _pool_payload("pool_a", [{"vmid": 1057}])
    destination_pool_before = _pool_payload("pool_b", [])
    source_pool_after_add = _pool_payload("pool_a", [{"vmid": 1057}])
    destination_pool_after_add = _pool_payload("pool_b", [{"vmid": 1057}])
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
            "data": {
                "/pool/pool_a": {"VM.Allocate": 1},
                "/pool/pool_b": {"VM.Allocate": 1},
            },
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

    scripted_state = _ScriptedClientState(
        get_pool_results={
            "pool_a": [
                source_pool_before,
                source_pool_before,
                source_pool_after_add,
                source_pool_after_move,
            ],
            "pool_b": [
                destination_pool_before,
                destination_pool_after_add,
                destination_pool_after_move,
            ],
        },
        get_user_result=state.get_user_result,
        get_effective_permissions_result=state.get_effective_permissions_result,
        add_vms_to_pool_result=state.add_vms_to_pool_result,
        remove_vms_from_pool_result=state.remove_vms_from_pool_result,
    )
    _install_scripted_client(monkeypatch, scripted_state)

    result = await pool_tools.proxmox_move_vms_between_pools(
        session=_Session(),
        server_ref="pve1",
        source_pool="pool_a",
        destination_pool="pool_b",
        vmids=[1057],
        old_email="l1@biznetgio.com",
        new_email="l2@biznetgio.com",
        reason="Ticket #1661262",
    )

    assert result == {
        "ok": False,
        "error_code": "invalid_response",
        "message": "Proxmox returned an unexpected pool payload",
    }
    assert scripted_state.calls == [
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("get_user", "l1@biznetgio.com@pve"),
        ("get_user", "l2@biznetgio.com@pve"),
        ("get_effective_permissions", ("l1@biznetgio.com@pve", "/pool/pool_a")),
        ("get_effective_permissions", ("l2@biznetgio.com@pve", "/pool/pool_b")),
        ("get_pool", "pool_a"),
        ("add_vms_to_pool", ("pool_b", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
        ("remove_vms_from_pool", ("pool_a", [1057])),
        ("get_pool", "pool_a"),
        ("get_pool", "pool_b"),
    ]
