from __future__ import annotations

from dataclasses import dataclass
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


class _Repo:
    def __init__(self) -> None:
        self.last_create_kwargs: dict[str, Any] | None = None

    async def list_servers(self) -> list[_Server]:
        return []

    async def get_by_id(self, *, server_id: UUID) -> _Server | None:
        _ = server_id
        return None

    async def create(self, **kwargs: Any) -> _Server:
        self.last_create_kwargs = kwargs
        return _Server(id=uuid4(), **kwargs)


@pytest.mark.asyncio
async def test_create_server_encrypts_proxmox_api_token_secret(monkeypatch) -> None:
    from noa_api.api.proxmox_admin.service import ProxmoxServerService

    repo = _Repo()
    service = ProxmoxServerService(repo)

    monkeypatch.setattr(
        "noa_api.api.proxmox_admin.service.encrypt_text",
        lambda value: f"enc::{value}",
    )

    await service.create_server(
        name="pve1",
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="SECRET",
        verify_ssl=True,
    )

    assert repo.last_create_kwargs is not None
    assert repo.last_create_kwargs["api_token_secret"] == "enc::SECRET"
