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


@dataclass
class _FullServer:
    id: UUID
    name: str
    base_url: str
    api_token_id: str
    api_token_secret: str
    verify_ssl: bool


class _FullRepo:
    def __init__(self, server: _FullServer | None) -> None:
        self.server = server
        self.last_create_kwargs: dict[str, Any] | None = None
        self.last_update_kwargs: dict[str, Any] | None = None

    async def list_servers(self) -> list[_FullServer]:
        return [self.server] if self.server is not None else []

    async def get_by_id(self, *, server_id: UUID) -> _FullServer | None:
        if self.server is None or self.server.id != server_id:
            return None
        return self.server

    async def create(self, **kwargs: Any) -> _FullServer:
        self.last_create_kwargs = kwargs
        return (
            self.server
            if self.server is not None
            else _FullServer(id=uuid4(), **kwargs)
        )

    async def update(self, *, server_id: UUID, **kwargs: Any) -> _FullServer | None:
        self.last_update_kwargs = {"server_id": server_id, **kwargs}
        if self.server is None or self.server.id != server_id:
            return None
        for key, value in kwargs.items():
            if value is not None:
                setattr(self.server, key, value)
        return self.server

    async def delete(self, *, server_id: UUID) -> bool:
        return self.server is not None and self.server.id == server_id


@pytest.mark.asyncio
async def test_update_server_encrypts_proxmox_api_token_secret(monkeypatch) -> None:
    from noa_api.api.proxmox_admin.service import ProxmoxServerService

    server = _FullServer(
        id=uuid4(),
        name="pve1",
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="enc::OLD_SECRET",
        verify_ssl=True,
    )
    repo = _FullRepo(server)
    service = ProxmoxServerService(repo)

    monkeypatch.setattr(
        "noa_api.api.proxmox_admin.service.encrypt_text",
        lambda value: f"enc::{value}",
    )

    await service.update_server(
        server_id=server.id,
        api_token_secret="NEW_SECRET",
    )

    assert repo.last_update_kwargs is not None
    assert repo.last_update_kwargs["api_token_secret"] == "enc::NEW_SECRET"


@pytest.mark.asyncio
async def test_validate_server_decrypts_api_token_secret(monkeypatch) -> None:
    from noa_api.api.proxmox_admin.service import ProxmoxServerService

    server = _FullServer(
        id=uuid4(),
        name="pve1",
        base_url="https://proxmox.example.com:8006",
        api_token_id="root@pam!token",
        api_token_secret="enc::ENCRYPTED_SECRET",
        verify_ssl=True,
    )
    repo = _FullRepo(server)
    service = ProxmoxServerService(repo)

    monkeypatch.setattr(
        "noa_api.api.proxmox_admin.service.maybe_decrypt_text",
        lambda value: value.replace("enc::", ""),
    )

    captured_secret: list[str] = []

    class _FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            api_token_id: str,
            api_token_secret: str,
            verify_ssl: bool,
        ):
            captured_secret.append(api_token_secret)

        async def get_version(self) -> dict[str, object]:
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(
        "noa_api.api.proxmox_admin.service.ProxmoxClient",
        _FakeClient,
    )

    result = await service.validate_server(server_id=server.id)

    assert result.ok is True
    assert captured_secret == ["ENCRYPTED_SECRET"]
