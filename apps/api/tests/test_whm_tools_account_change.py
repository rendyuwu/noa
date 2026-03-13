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
class _AccountState:
    suspended: bool
    email: str
    suspend_called: int = 0
    unsuspend_called: int = 0
    change_email_called: int = 0


@pytest.mark.asyncio
async def test_whm_suspend_account_is_noop_when_already_suspended(monkeypatch) -> None:
    from noa_api.core.tools.whm import account_change_tools

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
        account_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _AccountState(suspended=True, email="alice@example.com")

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
                        "suspended": 1 if state.suspended else 0,
                        "email": state.email,
                    }
                ],
            }

        async def suspend_account(
            self, *, username: str, reason: str
        ) -> dict[str, object]:
            state.suspend_called += 1
            state.suspended = True
            _ = (username, reason)
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(account_change_tools, "WHMClient", _Client)

    result = await account_change_tools.whm_suspend_account(
        session=_Session(),
        server_ref="web1",
        username="alice",
        reason="requested by customer",
    )

    assert result["ok"] is True
    assert result["status"] == "no-op"
    assert state.suspend_called == 0


@pytest.mark.asyncio
async def test_whm_unsuspend_account_is_noop_when_not_suspended(monkeypatch) -> None:
    from noa_api.core.tools.whm import account_change_tools

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
        account_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _AccountState(suspended=False, email="alice@example.com")

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
                        "suspended": 1 if state.suspended else 0,
                        "email": state.email,
                    }
                ],
            }

        async def unsuspend_account(self, *, username: str) -> dict[str, object]:
            state.unsuspend_called += 1
            state.suspended = False
            _ = username
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(account_change_tools, "WHMClient", _Client)

    result = await account_change_tools.whm_unsuspend_account(
        session=_Session(),
        server_ref="web1",
        username="alice",
        reason="requested by customer",
    )

    assert result["ok"] is True
    assert result["status"] == "no-op"
    assert state.unsuspend_called == 0


@pytest.mark.asyncio
async def test_whm_change_contact_email_is_noop_when_already_matches(
    monkeypatch,
) -> None:
    from noa_api.core.tools.whm import account_change_tools

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
        account_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _AccountState(suspended=False, email="alice@example.com")

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
                        "suspended": 1 if state.suspended else 0,
                        "email": state.email,
                    }
                ],
            }

        async def change_contact_email(
            self, *, username: str, email: str
        ) -> dict[str, object]:
            state.change_email_called += 1
            state.email = email
            _ = username
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(account_change_tools, "WHMClient", _Client)

    result = await account_change_tools.whm_change_contact_email(
        session=_Session(),
        server_ref="web1",
        username="alice",
        new_email="alice@example.com",
        reason="requested by customer",
    )

    assert result["ok"] is True
    assert result["status"] == "no-op"
    assert state.change_email_called == 0


async def test_whm_account_change_tools_are_registered() -> None:
    from noa_api.core.tools.registry import get_tool_definition

    assert get_tool_definition("whm_suspend_account") is not None
    assert get_tool_definition("whm_unsuspend_account") is not None
    assert get_tool_definition("whm_change_contact_email") is not None
