from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest


@dataclass
class _Server:
    id: UUID
    name: str
    base_url: str
    api_username: str
    api_token: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime


class _Repo:
    def __init__(self, servers: list[_Server]) -> None:
        self._servers = servers

    async def list_servers(self) -> list[_Server]:
        return self._servers

    async def get_by_id(self, *, server_id: UUID) -> _Server | None:
        for s in self._servers:
            if s.id == server_id:
                return s
        return None


@dataclass
class _Session:
    pass


@dataclass
class _CSFState:
    blocked: set[str] = field(default_factory=set)
    allowlisted: set[str] = field(default_factory=set)
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)


def _grep_html(*, target: str, blocked: bool, allowlisted: bool) -> str:
    if blocked:
        return (
            f"<html><body><pre>Found {target} in /etc/csf/csf.deny</pre></body></html>"
        )
    if allowlisted:
        return (
            f"<html><body><pre>Found {target} in /etc/csf/csf.allow</pre></body></html>"
        )
    return f"<html><body><pre>No matches for {target}</pre></body></html>"


@pytest.mark.asyncio
async def test_whm_csf_unblock_changes_when_blocked(monkeypatch) -> None:
    from noa_api.core.tools.whm import csf_change_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(blocked={"1.2.3.4"})

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def csf_grep(self, *, target: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "html": _grep_html(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
            }

        async def csf_request_action(
            self, *, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            state.calls.append((action, params))
            target = params.get("target")
            if action == "unblock" and isinstance(target, str):
                state.blocked.discard(target)
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(csf_change_tools, "WHMClient", _Client)

    result = await csf_change_tools.whm_csf_unblock(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        reason="customer blocked",
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "changed"
    assert state.calls and state.calls[0][0] == "unblock"


@pytest.mark.asyncio
async def test_whm_csf_unblock_is_noop_when_not_blocked(monkeypatch) -> None:
    from noa_api.core.tools.whm import csf_change_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(blocked=set())

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def csf_grep(self, *, target: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "html": _grep_html(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
            }

        async def csf_request_action(
            self, *, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            state.calls.append((action, params))
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(csf_change_tools, "WHMClient", _Client)

    result = await csf_change_tools.whm_csf_unblock(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        reason="customer blocked",
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "no-op"
    assert state.calls == []


@pytest.mark.asyncio
async def test_whm_csf_allowlist_add_ttl_rejects_cidr_and_ipv6_and_converts_minutes(
    monkeypatch,
) -> None:
    from noa_api.core.tools.whm import csf_change_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState()

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def csf_grep(self, *, target: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "html": _grep_html(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
            }

        async def csf_request_action(
            self, *, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            state.calls.append((action, params))
            target = params.get("target")
            if action == "allow_ttl" and isinstance(target, str):
                state.allowlisted.add(target)
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(csf_change_tools, "WHMClient", _Client)

    result = await csf_change_tools.whm_csf_allowlist_add_ttl(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.0/24", "2001:db8::1", "1.2.3.4"],
        duration_minutes=30,
        reason="temporary allow",
    )

    assert result["ok"] is False
    by_target = {r["target"]: r for r in result["results"]}
    assert by_target["1.2.3.0/24"]["error_code"] == "invalid_target"
    assert by_target["2001:db8::1"]["error_code"] == "invalid_target"
    assert by_target["1.2.3.4"]["status"] in {"changed", "no-op"}

    allow_calls = [call for call in state.calls if call[0] == "allow_ttl"]
    assert len(allow_calls) == 1
    assert allow_calls[0][1]["timeout"] == 30
    assert allow_calls[0][1]["dur"] == "m"


@pytest.mark.asyncio
async def test_whm_csf_allowlist_remove_is_noop_when_not_allowlisted(
    monkeypatch,
) -> None:
    from noa_api.core.tools.whm import csf_change_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(allowlisted=set())

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def csf_grep(self, *, target: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "html": _grep_html(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
            }

        async def csf_request_action(
            self, *, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            state.calls.append((action, params))
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(csf_change_tools, "WHMClient", _Client)

    result = await csf_change_tools.whm_csf_allowlist_remove(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        reason="cleanup",
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "no-op"
    assert state.calls == []


@pytest.mark.asyncio
async def test_whm_csf_denylist_add_ttl_converts_minutes(monkeypatch) -> None:
    from noa_api.core.tools.whm import csf_change_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(blocked=set())

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def csf_grep(self, *, target: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "html": _grep_html(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
            }

        async def csf_request_action(
            self, *, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            state.calls.append((action, params))
            target = params.get("target")
            if action == "deny_ttl" and isinstance(target, str):
                state.blocked.add(target)
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(csf_change_tools, "WHMClient", _Client)

    result = await csf_change_tools.whm_csf_denylist_add_ttl(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        duration_minutes=45,
        reason="customer abuse",
    )

    assert result["ok"] is True
    deny_calls = [call for call in state.calls if call[0] == "deny_ttl"]
    assert len(deny_calls) == 1
    assert deny_calls[0][1]["timeout"] == 45
    assert deny_calls[0][1]["dur"] == "m"


async def test_whm_csf_change_tools_are_registered() -> None:
    from noa_api.core.tools.registry import get_tool_definition

    assert get_tool_definition("whm_csf_unblock") is not None
    assert get_tool_definition("whm_csf_allowlist_remove") is not None
    assert get_tool_definition("whm_csf_allowlist_add_ttl") is not None
    assert get_tool_definition("whm_csf_denylist_add_ttl") is not None
