from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import (
    ADMIN_ACCESS_REQUIRED,
    WHM_SERVER_NAME_EXISTS,
    WHM_SERVER_NOT_FOUND,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.logging_context import log_context
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.whm_servers import (
    SQLWHMServerRepository,
    WHMServerRepositoryProtocol,
)

router = APIRouter(prefix="/admin/whm/servers", tags=["admin"])

logger = logging.getLogger(__name__)


class WHMServerResponse(BaseModel):
    id: str
    name: str
    base_url: str
    api_username: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime


class WHMServerListResponse(BaseModel):
    servers: list[WHMServerResponse]


class CreateWHMServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=1, max_length=500)
    api_username: str = Field(min_length=1, max_length=255)
    api_token: str = Field(min_length=1)
    verify_ssl: bool = True

    @field_validator("name", "base_url", "api_username", "api_token", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class CreateWHMServerResponse(BaseModel):
    server: WHMServerResponse


class UpdateWHMServerRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=500)
    api_username: str | None = Field(default=None, max_length=255)
    api_token: str | None = Field(default=None, min_length=1)
    verify_ssl: bool | None = None

    @field_validator("name", "base_url", "api_username", "api_token", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized == "":
                return None
            return normalized
        return value


class UpdateWHMServerResponse(BaseModel):
    server: WHMServerResponse


class DeleteWHMServerResponse(BaseModel):
    ok: bool


class ValidateWHMServerResponse(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str


class WHMServerServiceError(Exception):
    pass


class WHMServerNameExistsError(WHMServerServiceError):
    pass


class WHMServerNotFoundError(WHMServerServiceError):
    pass


def _to_server_response(server: Any) -> WHMServerResponse:
    safe = server.to_safe_dict()
    return WHMServerResponse.model_validate(safe)


async def _require_admin(
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active or "admin" not in current_user.roles:
        logger.info(
            "whm_admin_access_denied",
            extra={
                "is_active": current_user.is_active,
                "roles": current_user.roles,
                "user_id": str(current_user.user_id),
            },
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
            error_code=ADMIN_ACCESS_REQUIRED,
        )
    return current_user


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

        # Local import keeps this module usable in tests without the integration present yet.
        from noa_api.integrations.whm.client import WHMClient

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


@router.get("", response_model=WHMServerListResponse)
async def list_whm_servers(
    _: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> WHMServerListResponse:
    servers = await whm_server_service.list_servers()
    return WHMServerListResponse(servers=[_to_server_response(s) for s in servers])


@router.post("", response_model=CreateWHMServerResponse)
async def create_whm_server(
    payload: CreateWHMServerRequest,
    _: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> CreateWHMServerResponse | Response:
    with log_context(server_name=payload.name):
        try:
            server = await whm_server_service.create_server(
                name=payload.name,
                base_url=payload.base_url,
                api_username=payload.api_username,
                api_token=payload.api_token,
                verify_ssl=payload.verify_ssl,
            )
        except WHMServerNameExistsError as exc:
            logger.info("whm_server_name_conflict")
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="WHM server name already exists",
                error_code=WHM_SERVER_NAME_EXISTS,
            ) from exc
    response = CreateWHMServerResponse(server=_to_server_response(server))
    return JSONResponse(
        content=jsonable_encoder(response.model_dump()),
        status_code=status.HTTP_201_CREATED,
    )


@router.patch("/{server_id}", response_model=UpdateWHMServerResponse)
async def update_whm_server(
    server_id: UUID,
    payload: UpdateWHMServerRequest,
    _: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> UpdateWHMServerResponse:
    with log_context(server_id=str(server_id), server_name=payload.name):
        try:
            server = await whm_server_service.update_server(
                server_id=server_id,
                name=payload.name,
                base_url=payload.base_url,
                api_username=payload.api_username,
                api_token=payload.api_token,
                verify_ssl=payload.verify_ssl,
            )
        except WHMServerNameExistsError as exc:
            logger.info("whm_server_name_conflict")
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="WHM server name already exists",
                error_code=WHM_SERVER_NAME_EXISTS,
            ) from exc
        if server is None:
            logger.info("whm_server_not_found")
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="WHM server not found",
                error_code=WHM_SERVER_NOT_FOUND,
            )
    return UpdateWHMServerResponse(server=_to_server_response(server))


@router.delete("/{server_id}", response_model=DeleteWHMServerResponse)
async def delete_whm_server(
    server_id: UUID,
    _: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> DeleteWHMServerResponse:
    with log_context(server_id=str(server_id)):
        deleted = await whm_server_service.delete_server(server_id=server_id)
        if not deleted:
            logger.info("whm_server_not_found")
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="WHM server not found",
                error_code=WHM_SERVER_NOT_FOUND,
            )
    return DeleteWHMServerResponse(ok=True)


@router.post("/{server_id}/validate", response_model=ValidateWHMServerResponse)
async def validate_whm_server(
    server_id: UUID,
    _: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> ValidateWHMServerResponse:
    with log_context(server_id=str(server_id)):
        try:
            return await whm_server_service.validate_server(server_id=server_id)
        except WHMServerNotFoundError as exc:
            logger.info("whm_server_not_found")
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="WHM server not found",
                error_code=WHM_SERVER_NOT_FOUND,
            ) from exc
