from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from noa_api.api.proxmox_admin.schemas import ValidateProxmoxServerResponse
from noa_api.core.secrets.crypto import encrypt_text, maybe_decrypt_text
from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.proxmox_servers import (
    ProxmoxServerRepositoryProtocol,
    SQLProxmoxServerRepository,
)


class ProxmoxServerServiceError(Exception):
    pass


class ProxmoxServerNameExistsError(ProxmoxServerServiceError):
    pass


class ProxmoxServerNotFoundError(ProxmoxServerServiceError):
    pass


class ProxmoxServerService:
    def __init__(self, repository: ProxmoxServerRepositoryProtocol) -> None:
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
        api_token_id: str,
        api_token_secret: str,
        verify_ssl: bool,
    ):
        try:
            return await self._repository.create(
                name=name,
                base_url=base_url,
                api_token_id=api_token_id,
                api_token_secret=encrypt_text(api_token_secret),
                verify_ssl=verify_ssl,
            )
        except IntegrityError as exc:
            raise ProxmoxServerNameExistsError from exc

    async def update_server(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_token_id: str | None = None,
        api_token_secret: str | None = None,
        verify_ssl: bool | None = None,
    ):
        try:
            return await self._repository.update(
                server_id=server_id,
                name=name,
                base_url=base_url,
                api_token_id=api_token_id,
                api_token_secret=(
                    encrypt_text(api_token_secret)
                    if api_token_secret is not None
                    else None
                ),
                verify_ssl=verify_ssl,
            )
        except IntegrityError as exc:
            raise ProxmoxServerNameExistsError from exc

    async def delete_server(self, *, server_id: UUID) -> bool:
        return await self._repository.delete(server_id=server_id)

    async def validate_server(
        self, *, server_id: UUID
    ) -> ValidateProxmoxServerResponse:
        server = await self.get_server(server_id=server_id)
        if server is None:
            raise ProxmoxServerNotFoundError

        client = ProxmoxClient(
            base_url=server.base_url,
            api_token_id=server.api_token_id,
            api_token_secret=maybe_decrypt_text(server.api_token_secret),
            verify_ssl=server.verify_ssl,
        )
        result = await client.get_version()
        if result.get("ok") is not True:
            return ValidateProxmoxServerResponse(
                ok=False,
                error_code=str(result.get("error_code") or "unknown"),
                message=str(result.get("message") or "Proxmox validation failed"),
            )

        return ValidateProxmoxServerResponse(
            ok=True,
            message=str(result.get("message") or "ok"),
        )


async def get_proxmox_server_service() -> AsyncGenerator[ProxmoxServerService, None]:
    async with get_session_factory()() as session:
        service = ProxmoxServerService(SQLProxmoxServerRepository(session))
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise
