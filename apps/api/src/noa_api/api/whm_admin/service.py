from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from noa_api.api.whm_admin.schemas import ValidateWHMServerResponse
from noa_api.core.remote_exec.ssh import (
    SSHExecutionError,
    ssh_exec,
    ssh_get_host_fingerprint,
)
from noa_api.core.secrets.crypto import encrypt_text
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.whm_servers import (
    SQLWHMServerRepository,
    WHMServerRepositoryProtocol,
)
from noa_api.whm.integrations.ssh import (
    build_whm_client,
    has_ssh_credentials,
    resolve_whm_ssh_config,
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
        ssh_username: str | None = None,
        ssh_port: int | None = None,
        ssh_password: str | None = None,
        ssh_private_key: str | None = None,
        ssh_private_key_passphrase: str | None = None,
        verify_ssl: bool,
    ):
        try:
            return await self._repository.create(
                name=name,
                base_url=base_url,
                api_username=api_username,
                api_token=encrypt_text(api_token),
                ssh_username=ssh_username,
                ssh_port=ssh_port,
                ssh_password=(encrypt_text(ssh_password) if ssh_password else None),
                ssh_private_key=(
                    encrypt_text(ssh_private_key) if ssh_private_key else None
                ),
                ssh_private_key_passphrase=(
                    encrypt_text(ssh_private_key_passphrase)
                    if ssh_private_key_passphrase
                    else None
                ),
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
    ):
        current_server = await self._repository.get_by_id(server_id=server_id)
        if current_server is None:
            return None

        should_clear_host_key_fingerprint = clear_ssh_host_key_fingerprint
        if base_url is not None and base_url != current_server.base_url:
            should_clear_host_key_fingerprint = True
        if ssh_port is not None and ssh_port != current_server.ssh_port:
            should_clear_host_key_fingerprint = True
        if clear_ssh_configuration:
            should_clear_host_key_fingerprint = True

        try:
            return await self._repository.update(
                server_id=server_id,
                name=name,
                base_url=base_url,
                api_username=api_username,
                api_token=(encrypt_text(api_token) if api_token is not None else None),
                ssh_username=ssh_username,
                ssh_port=ssh_port,
                ssh_password=(
                    encrypt_text(ssh_password) if ssh_password is not None else None
                ),
                ssh_private_key=(
                    encrypt_text(ssh_private_key)
                    if ssh_private_key is not None
                    else None
                ),
                ssh_private_key_passphrase=(
                    encrypt_text(ssh_private_key_passphrase)
                    if ssh_private_key_passphrase is not None
                    else None
                ),
                ssh_host_key_fingerprint=ssh_host_key_fingerprint,
                clear_ssh_configuration=clear_ssh_configuration,
                clear_ssh_username=clear_ssh_username,
                clear_ssh_port=clear_ssh_port,
                clear_ssh_password=clear_ssh_password,
                clear_ssh_private_key=clear_ssh_private_key,
                clear_ssh_private_key_passphrase=clear_ssh_private_key_passphrase,
                clear_ssh_host_key_fingerprint=should_clear_host_key_fingerprint,
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

        client = build_whm_client(server)
        result = await client.applist()
        if result.get("ok") is not True:
            return ValidateWHMServerResponse(
                ok=False,
                error_code=str(result.get("error_code") or "unknown"),
                message=str(result.get("message") or "WHM validation failed"),
            )

        if not has_ssh_credentials(server):
            return ValidateWHMServerResponse(ok=True, message="ok")

        try:
            bootstrap_config = resolve_whm_ssh_config(
                server,
                require_host_key_fingerprint=False,
            )
            fingerprint = await ssh_get_host_fingerprint(bootstrap_config)
            updated = await self.update_server(
                server_id=server_id,
                ssh_host_key_fingerprint=fingerprint,
            )
            validated_server = updated or server
            ssh_config = resolve_whm_ssh_config(
                validated_server,
                require_host_key_fingerprint=True,
            )
            await ssh_exec(ssh_config, command="true")
        except SSHExecutionError as exc:
            return ValidateWHMServerResponse(
                ok=False,
                error_code=exc.code,
                message=exc.message,
            )

        return ValidateWHMServerResponse(ok=True, message="ok")


async def get_whm_server_service() -> AsyncGenerator[WHMServerService, None]:
    async with get_session_factory()() as session:
        service = WHMServerService(SQLWHMServerRepository(session))
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise
