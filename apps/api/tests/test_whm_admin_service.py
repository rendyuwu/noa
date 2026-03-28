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
    api_username: str
    api_token: str
    verify_ssl: bool
    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_private_key_passphrase: str | None = None
    ssh_host_key_fingerprint: str | None = None


class _Repo:
    def __init__(self, server: _Server | None) -> None:
        self.server = server
        self.last_create_kwargs: dict[str, Any] | None = None
        self.last_update_kwargs: dict[str, Any] | None = None

    async def list_servers(self) -> list[_Server]:
        return [self.server] if self.server is not None else []

    async def get_by_id(self, *, server_id: UUID) -> _Server | None:
        if self.server is None or self.server.id != server_id:
            return None
        return self.server

    async def create(self, **kwargs: Any) -> _Server:
        self.last_create_kwargs = kwargs
        return self.server if self.server is not None else _Server(id=uuid4(), **kwargs)

    async def update(self, *, server_id: UUID, **kwargs: Any) -> _Server | None:
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
async def test_create_server_encrypts_whm_and_ssh_secrets(monkeypatch) -> None:
    from noa_api.api.whm_admin.service import WHMServerService

    repo = _Repo(None)
    service = WHMServerService(repo)

    monkeypatch.setattr(
        "noa_api.api.whm_admin.service.encrypt_text",
        lambda value: f"enc::{value}",
    )

    await service.create_server(
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="WHM_TOKEN",
        ssh_username="root",
        ssh_port=22,
        ssh_password="SSH_PASSWORD",
        ssh_private_key="PRIVATE_KEY",
        ssh_private_key_passphrase="PASSPHRASE",
        verify_ssl=True,
    )

    assert repo.last_create_kwargs is not None
    assert repo.last_create_kwargs["api_token"] == "enc::WHM_TOKEN"
    assert repo.last_create_kwargs["ssh_password"] == "enc::SSH_PASSWORD"
    assert repo.last_create_kwargs["ssh_private_key"] == "enc::PRIVATE_KEY"
    assert repo.last_create_kwargs["ssh_private_key_passphrase"] == "enc::PASSPHRASE"


@pytest.mark.asyncio
async def test_validate_server_updates_ssh_fingerprint_when_ssh_configured(
    monkeypatch,
) -> None:
    from noa_api.api.whm_admin.service import WHMServerService

    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="enc::TOKEN",
        verify_ssl=True,
        ssh_password="enc::SSH_PASSWORD",
    )
    repo = _Repo(server)
    service = WHMServerService(repo)

    class _Client:
        async def applist(self) -> dict[str, object]:
            return {"ok": True, "message": "ok"}

    monkeypatch.setattr(
        "noa_api.api.whm_admin.service.build_whm_client",
        lambda current_server: _Client(),
    )
    monkeypatch.setattr(
        "noa_api.api.whm_admin.service.has_ssh_credentials",
        lambda current_server: True,
    )
    monkeypatch.setattr(
        "noa_api.api.whm_admin.service.resolve_whm_ssh_config",
        lambda current_server, require_host_key_fingerprint: object(),
    )

    async def _fake_get_host_fingerprint(config) -> str:
        _ = config
        return "SHA256:new"

    async def _fake_ssh_exec(config, *, command: str):
        _ = (config, command)
        return None

    monkeypatch.setattr(
        "noa_api.api.whm_admin.service.ssh_get_host_fingerprint",
        _fake_get_host_fingerprint,
    )
    monkeypatch.setattr("noa_api.api.whm_admin.service.ssh_exec", _fake_ssh_exec)

    result = await service.validate_server(server_id=server.id)

    assert result.ok is True
    assert repo.last_update_kwargs is not None
    assert repo.last_update_kwargs["ssh_host_key_fingerprint"] == "SHA256:new"


@pytest.mark.asyncio
async def test_update_server_clears_ssh_fingerprint_when_base_url_changes(
    monkeypatch,
) -> None:
    from noa_api.api.whm_admin.service import WHMServerService

    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="enc::TOKEN",
        verify_ssl=True,
        ssh_password="enc::SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:old",
    )
    repo = _Repo(server)
    service = WHMServerService(repo)

    monkeypatch.setattr(
        "noa_api.api.whm_admin.service.encrypt_text",
        lambda value: f"enc::{value}",
    )

    await service.update_server(
        server_id=server.id,
        base_url="https://new-whm.example.com:2087",
    )

    assert repo.last_update_kwargs is not None
    assert repo.last_update_kwargs["clear_ssh_host_key_fingerprint"] is True


@pytest.mark.asyncio
async def test_update_server_forwards_clear_ssh_configuration(monkeypatch) -> None:
    from noa_api.api.whm_admin.service import WHMServerService

    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="enc::TOKEN",
        verify_ssl=True,
        ssh_password="enc::SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:old",
    )
    repo = _Repo(server)
    service = WHMServerService(repo)

    monkeypatch.setattr(
        "noa_api.api.whm_admin.service.encrypt_text",
        lambda value: f"enc::{value}",
    )

    await service.update_server(
        server_id=server.id,
        clear_ssh_configuration=True,
    )

    assert repo.last_update_kwargs is not None
    assert repo.last_update_kwargs["clear_ssh_configuration"] is True
    assert repo.last_update_kwargs["clear_ssh_host_key_fingerprint"] is True
