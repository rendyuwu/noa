from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.models import WHMServer


class WHMServerRepositoryProtocol(Protocol):
    async def list_servers(self) -> list[WHMServer]: ...

    async def get_by_id(self, *, server_id: UUID) -> WHMServer | None: ...

    async def get_by_name(self, *, name: str) -> list[WHMServer]: ...

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        ssh_username: str | None = None,
        ssh_port: int | None = None,
        ssh_password: str | None = None,
        ssh_private_key: str | None = None,
        ssh_private_key_passphrase: str | None = None,
        ssh_host_key_fingerprint: str | None = None,
        verify_ssl: bool,
    ) -> WHMServer: ...

    async def update(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        ssh_username: str | None = None,
        ssh_port: int | None = None,
        ssh_password: str | None = None,
        ssh_private_key: str | None = None,
        ssh_private_key_passphrase: str | None = None,
        ssh_host_key_fingerprint: str | None = None,
        clear_ssh_configuration: bool = False,
        clear_ssh_username: bool = False,
        clear_ssh_port: bool = False,
        clear_ssh_password: bool = False,
        clear_ssh_private_key: bool = False,
        clear_ssh_private_key_passphrase: bool = False,
        clear_ssh_host_key_fingerprint: bool = False,
        verify_ssl: bool | None = None,
    ) -> WHMServer | None: ...

    async def delete(self, *, server_id: UUID) -> bool: ...


class SQLWHMServerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_servers(self) -> list[WHMServer]:
        result = await self._session.execute(
            select(WHMServer).order_by(WHMServer.name.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, *, server_id: UUID) -> WHMServer | None:
        result = await self._session.execute(
            select(WHMServer).where(WHMServer.id == server_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, *, name: str) -> list[WHMServer]:
        result = await self._session.execute(
            select(WHMServer).where(func.lower(WHMServer.name) == name.lower())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        ssh_username: str | None = None,
        ssh_port: int | None = None,
        ssh_password: str | None = None,
        ssh_private_key: str | None = None,
        ssh_private_key_passphrase: str | None = None,
        ssh_host_key_fingerprint: str | None = None,
        verify_ssl: bool,
    ) -> WHMServer:
        server = WHMServer(
            name=name,
            base_url=base_url,
            api_username=api_username,
            api_token=api_token,
            ssh_username=ssh_username,
            ssh_port=ssh_port,
            ssh_password=ssh_password,
            ssh_private_key=ssh_private_key,
            ssh_private_key_passphrase=ssh_private_key_passphrase,
            ssh_host_key_fingerprint=ssh_host_key_fingerprint,
            verify_ssl=verify_ssl,
        )
        self._session.add(server)
        await self._session.flush()
        await self._session.refresh(server)
        return server

    async def update(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        ssh_username: str | None = None,
        ssh_port: int | None = None,
        ssh_password: str | None = None,
        ssh_private_key: str | None = None,
        ssh_private_key_passphrase: str | None = None,
        ssh_host_key_fingerprint: str | None = None,
        clear_ssh_configuration: bool = False,
        clear_ssh_username: bool = False,
        clear_ssh_port: bool = False,
        clear_ssh_password: bool = False,
        clear_ssh_private_key: bool = False,
        clear_ssh_private_key_passphrase: bool = False,
        clear_ssh_host_key_fingerprint: bool = False,
        verify_ssl: bool | None = None,
    ) -> WHMServer | None:
        server = await self.get_by_id(server_id=server_id)
        if server is None:
            return None

        if clear_ssh_configuration:
            server.ssh_username = None
            server.ssh_port = None
            server.ssh_password = None
            server.ssh_private_key = None
            server.ssh_private_key_passphrase = None
            server.ssh_host_key_fingerprint = None

        if clear_ssh_username:
            server.ssh_username = None
        if clear_ssh_port:
            server.ssh_port = None
        if clear_ssh_password:
            server.ssh_password = None
        if clear_ssh_private_key:
            server.ssh_private_key = None
        if clear_ssh_private_key_passphrase:
            server.ssh_private_key_passphrase = None
        if clear_ssh_host_key_fingerprint:
            server.ssh_host_key_fingerprint = None

        if name is not None:
            server.name = name
        if base_url is not None:
            server.base_url = base_url
        if api_username is not None:
            server.api_username = api_username
        if api_token is not None:
            server.api_token = api_token
        if ssh_username is not None:
            server.ssh_username = ssh_username
        if ssh_port is not None:
            server.ssh_port = ssh_port
        if ssh_password is not None:
            server.ssh_password = ssh_password
        if ssh_private_key is not None:
            server.ssh_private_key = ssh_private_key
        if ssh_private_key_passphrase is not None:
            server.ssh_private_key_passphrase = ssh_private_key_passphrase
        if ssh_host_key_fingerprint is not None:
            server.ssh_host_key_fingerprint = ssh_host_key_fingerprint
        if verify_ssl is not None:
            server.verify_ssl = verify_ssl

        await self._session.flush()
        await self._session.refresh(server)
        return server

    async def delete(self, *, server_id: UUID) -> bool:
        server = await self.get_by_id(server_id=server_id)
        if server is None:
            return False
        await self._session.delete(server)
        await self._session.flush()
        return True
