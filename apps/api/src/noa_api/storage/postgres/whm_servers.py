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
        verify_ssl: bool,
    ) -> WHMServer:
        server = WHMServer(
            name=name,
            base_url=base_url,
            api_username=api_username,
            api_token=api_token,
            verify_ssl=verify_ssl,
        )
        self._session.add(server)
        await self._session.flush()
        return server

    async def update(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ) -> WHMServer | None:
        server = await self.get_by_id(server_id=server_id)
        if server is None:
            return None

        if name is not None:
            server.name = name
        if base_url is not None:
            server.base_url = base_url
        if api_username is not None:
            server.api_username = api_username
        if api_token is not None:
            server.api_token = api_token
        if verify_ssl is not None:
            server.verify_ssl = verify_ssl

        await self._session.flush()
        return server

    async def delete(self, *, server_id: UUID) -> bool:
        server = await self.get_by_id(server_id=server_id)
        if server is None:
            return False
        await self._session.delete(server)
        await self._session.flush()
        return True
