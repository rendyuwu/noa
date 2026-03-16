from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from noa_api.api.whm_admin.schemas import ValidateWHMServerResponse
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.whm_servers import (
    SQLWHMServerRepository,
    WHMServerRepositoryProtocol,
)


class WHMServerServiceError(Exception):
    pass


class WHMServerNameExistsError(WHMServerServiceError):
    pass


class WHMServerNotFoundError(WHMServerServiceError):
    pass


class WHMServerService:
    def __init__(self, repository: WHMServerRepositoryProtocol) -> None:
        self._repository = repository

    async def list_servers(self):
        return await self._repository.list_servers()

    async def get_server(self, *, server_id: UUID):
        return await self._repository.get_by_id(server_id=server_id)

    async def create_server(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
    ):
        try:
            return await self._repository.create(
                name=name,
                base_url=base_url,
                api_username=api_username,
                api_token=api_token,
                verify_ssl=verify_ssl,
            )
        except IntegrityError as exc:
            raise WHMServerNameExistsError from exc

    async def update_server(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ):
        try:
            return await self._repository.update(
                server_id=server_id,
                name=name,
                base_url=base_url,
                api_username=api_username,
                api_token=api_token,
                verify_ssl=verify_ssl,
            )
        except IntegrityError as exc:
            raise WHMServerNameExistsError from exc

    async def delete_server(self, *, server_id: UUID) -> bool:
        return await self._repository.delete(server_id=server_id)

    async def validate_server(self, *, server_id: UUID) -> ValidateWHMServerResponse:
        server = await self.get_server(server_id=server_id)
        if server is None:
            raise WHMServerNotFoundError

        from noa_api.whm.integrations.client import WHMClient

        client = WHMClient(
            base_url=server.base_url,
            api_username=server.api_username,
            api_token=server.api_token,
            verify_ssl=server.verify_ssl,
        )
        result = await client.applist()
        if result.get("ok") is True:
            return ValidateWHMServerResponse(ok=True, message="ok")
        return ValidateWHMServerResponse(
            ok=False,
            error_code=str(result.get("error_code") or "unknown"),
            message=str(result.get("message") or "WHM validation failed"),
        )


async def get_whm_server_service() -> AsyncGenerator[WHMServerService, None]:
    async with get_session_factory()() as session:
        service = WHMServerService(SQLWHMServerRepository(session))
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise
