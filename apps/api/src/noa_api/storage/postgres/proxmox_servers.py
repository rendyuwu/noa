from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.models import ProxmoxServer


class ProxmoxServerRepositoryProtocol(Protocol):
    async def list_servers(self) -> list[ProxmoxServer]: ...

    async def get_by_id(self, *, server_id: UUID) -> ProxmoxServer | None: ...

    async def get_by_name(self, *, name: str) -> list[ProxmoxServer]: ...

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        api_token_id: str,
        api_token_secret: str,
        verify_ssl: bool,
    ) -> ProxmoxServer: ...

    async def update(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_token_id: str | None = None,
        api_token_secret: str | None = None,
        verify_ssl: bool | None = None,
    ) -> ProxmoxServer | None: ...

    async def delete(self, *, server_id: UUID) -> bool: ...


class SQLProxmoxServerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_servers(self) -> list[ProxmoxServer]:
        result = await self._session.execute(
            select(ProxmoxServer).order_by(ProxmoxServer.name.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, *, server_id: UUID) -> ProxmoxServer | None:
        result = await self._session.execute(
            select(ProxmoxServer).where(ProxmoxServer.id == server_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, *, name: str) -> list[ProxmoxServer]:
        result = await self._session.execute(
            select(ProxmoxServer).where(func.lower(ProxmoxServer.name) == name.lower())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        api_token_id: str,
        api_token_secret: str,
        verify_ssl: bool,
    ) -> ProxmoxServer:
        server = ProxmoxServer(
            name=name,
            base_url=base_url,
            api_token_id=api_token_id,
            api_token_secret=api_token_secret,
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
        api_token_id: str | None = None,
        api_token_secret: str | None = None,
        verify_ssl: bool | None = None,
    ) -> ProxmoxServer | None:
        server = await self.get_by_id(server_id=server_id)
        if server is None:
            return None

        if name is not None:
            server.name = name
        if base_url is not None:
            server.base_url = base_url
        if api_token_id is not None:
            server.api_token_id = api_token_id
        if api_token_secret is not None:
            server.api_token_secret = api_token_secret
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
