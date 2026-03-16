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
    api_username: str
    api_token: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "base_url": self.base_url,
            "api_username": self.api_username,
            "verify_ssl": self.verify_ssl,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


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


@pytest.mark.asyncio
async def test_whm_list_servers_excludes_api_token(monkeypatch) -> None:
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    servers = [
        _Server(
            id=uuid4(),
            name="web1",
            base_url="https://whm.example.com:2087",
            api_username="root",
            api_token="SECRET",
            verify_ssl=True,
            created_at=now,
            updated_at=now,
        )
    ]
    repo = _Repo(servers)
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    result = await read_tools.whm_list_servers(session=_Session())

    assert result["ok"] is True
    assert result["servers"][0]["name"] == "web1"
    assert "api_token" not in result["servers"][0]
    assert "SECRET" not in str(result)


@pytest.mark.asyncio
async def test_whm_list_accounts_ambiguous_server_ref_returns_choices(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    a = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://a.example.com:2087",
        api_username="root",
        api_token="A",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    b = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://b.example.com:2087",
        api_username="root",
        api_token="B",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([a, b])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    result = await read_tools.whm_list_accounts(session=_Session(), server_ref="web1")

    assert result["ok"] is False
    assert result["error_code"] == "host_ambiguous"
    assert len(result["choices"]) >= 2


@pytest.mark.asyncio
async def test_whm_validate_server_propagates_client_errors(monkeypatch) -> None:
    from noa_api.whm.tools import read_tools

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
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def applist(self) -> dict[str, object]:
            return {
                "ok": False,
                "error_code": "auth_failed",
                "message": "WHM authentication failed",
            }

    monkeypatch.setattr(read_tools, "WHMClient", _Client)

    result = await read_tools.whm_validate_server(session=_Session(), server_ref="web1")

    assert result["ok"] is False
    assert result["error_code"] == "auth_failed"


@pytest.mark.asyncio
async def test_whm_search_accounts_filters_results(monkeypatch) -> None:
    from noa_api.whm.tools import read_tools

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
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def list_accounts(self) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "accounts": [
                    {"user": "alice", "domain": "alice.example.com"},
                    {"user": "bob", "domain": "bob.example.com"},
                ],
            }

    monkeypatch.setattr(read_tools, "WHMClient", _Client)

    result = await read_tools.whm_search_accounts(
        session=_Session(),
        server_ref="web1",
        query="ali",
    )

    assert result["ok"] is True
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["user"] == "alice"


@pytest.mark.asyncio
async def test_whm_search_accounts_rejects_non_positive_limit() -> None:
    from noa_api.whm.tools import read_tools

    result = await read_tools.whm_search_accounts(
        session=_Session(),
        server_ref="web1",
        query="alice",
        limit=0,
    )

    assert result == {
        "ok": False,
        "error_code": "limit_invalid",
        "message": "Limit must be a positive integer",
    }


@pytest.mark.asyncio
async def test_whm_preflight_csf_entries_parses_verdict(monkeypatch) -> None:
    from noa_api.whm.tools import preflight_tools

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
    monkeypatch.setattr(preflight_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def csf_grep(self, *, target: str) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "html": f"<html><body><pre>Found {target} in /etc/csf/csf.deny</pre></body></html>",
            }

    monkeypatch.setattr(preflight_tools, "WHMClient", _Client)

    result = await preflight_tools.whm_preflight_csf_entries(
        session=_Session(),
        server_ref="web1",
        target="1.2.3.4",
    )

    assert result["ok"] is True
    assert result["verdict"] == "blocked"
    assert len(result["matches"]) <= 20


@pytest.mark.asyncio
async def test_whm_preflight_account_finds_account(monkeypatch) -> None:
    from noa_api.whm.tools import preflight_tools

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
    monkeypatch.setattr(preflight_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def list_accounts(self) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "accounts": [
                    {
                        "user": "alice",
                        "domain": "alice.example.com",
                        "suspended": 1,
                        "email": "alice@example.com",
                    },
                ],
            }

    monkeypatch.setattr(preflight_tools, "WHMClient", _Client)

    result = await preflight_tools.whm_preflight_account(
        session=_Session(),
        server_ref="web1",
        username="alice",
    )

    assert result["ok"] is True
    assert result["account"]["user"] == "alice"


async def test_whm_read_and_preflight_tools_are_registered() -> None:
    from noa_api.core.tools.registry import get_tool_definition

    assert get_tool_definition("whm_list_servers") is not None
    assert get_tool_definition("whm_validate_server") is not None
    assert get_tool_definition("whm_list_accounts") is not None
    assert get_tool_definition("whm_search_accounts") is not None
    assert get_tool_definition("whm_preflight_account") is not None
    assert get_tool_definition("whm_preflight_csf_entries") is not None
