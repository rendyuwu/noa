from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest


@dataclass
class _Server:
    id: UUID
    name: str
    base_url: str


class _Repo:
    def __init__(self, servers: list[_Server]) -> None:
        self._servers = servers

    async def list_servers(self) -> list[_Server]:
        return self._servers

    async def get_by_id(self, server_id: UUID):
        for s in self._servers:
            if s.id == server_id:
                return s
        return None


class _RepoKwOnly:
    def __init__(self, server: _Server) -> None:
        self._server = server

    async def list_servers(self) -> list[_Server]:
        return [self._server]

    async def get_by_id(self, *, server_id: UUID):
        if self._server.id == server_id:
            return self._server
        return None


@pytest.mark.asyncio
async def test_resolve_by_uuid() -> None:
    from noa_api.core.whm.server_ref import resolve_whm_server_ref

    target = _Server(id=uuid4(), name="web1", base_url="https://whm.example.com:2087")
    repo = _Repo([target])
    result = await resolve_whm_server_ref(str(target.id), repo=repo)
    assert result.ok is True
    assert result.server_id == target.id


@pytest.mark.asyncio
async def test_resolve_by_uuid_supports_keyword_only_repo_methods() -> None:
    from noa_api.core.whm.server_ref import resolve_whm_server_ref

    target = _Server(id=uuid4(), name="web1", base_url="https://whm.example.com:2087")
    repo = _RepoKwOnly(target)
    result = await resolve_whm_server_ref(str(target.id), repo=repo)
    assert result.ok is True
    assert result.server_id == target.id


@pytest.mark.asyncio
async def test_resolve_ambiguous_by_name_returns_choices() -> None:
    from noa_api.core.whm.server_ref import resolve_whm_server_ref

    a = _Server(id=uuid4(), name="web1", base_url="https://a.example.com:2087")
    b = _Server(id=uuid4(), name="web1", base_url="https://b.example.com:2087")
    repo = _Repo([a, b])
    result = await resolve_whm_server_ref("web1", repo=repo)
    assert result.ok is False
    assert result.error_code == "host_ambiguous"
    assert len(result.choices) >= 2
