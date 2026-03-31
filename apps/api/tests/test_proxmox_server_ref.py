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

    async def get_by_id(self, *, server_id: UUID) -> _Server | None:
        for server in self._servers:
            if server.id == server_id:
                return server
        return None


@pytest.mark.asyncio
async def test_resolve_proxmox_server_ref_by_host() -> None:
    from noa_api.proxmox.server_ref import resolve_proxmox_server_ref

    server = _Server(
        id=uuid4(),
        name="pve1",
        base_url="https://proxmox.example.com:8006",
    )

    result = await resolve_proxmox_server_ref(
        "proxmox.example.com", repo=_Repo([server])
    )

    assert result.ok is True
    assert result.server_id == server.id


@pytest.mark.asyncio
async def test_resolve_proxmox_server_ref_ambiguous_name_returns_choices() -> None:
    from noa_api.proxmox.server_ref import resolve_proxmox_server_ref

    a = _Server(id=uuid4(), name="pve1", base_url="https://a.example.com:8006")
    b = _Server(id=uuid4(), name="pve1", base_url="https://b.example.com:8006")

    result = await resolve_proxmox_server_ref("pve1", repo=_Repo([a, b]))

    assert result.ok is False
    assert result.error_code == "host_ambiguous"
    assert len(result.choices) == 2
