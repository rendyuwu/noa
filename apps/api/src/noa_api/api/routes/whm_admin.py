from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from noa_api.core.auth.authorization import AuthorizationUser, get_current_auth_user
from noa_api.storage.postgres.client import create_engine, create_session_factory
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository

router = APIRouter(prefix="/admin/whm/servers", tags=["admin"])
_engine = create_engine()
_session_factory = create_session_factory(_engine)


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


def _to_server_response(server: Any) -> WHMServerResponse:
    safe = server.to_safe_dict()
    return WHMServerResponse.model_validate(safe)


async def _require_admin(
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active or "admin" not in current_user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return current_user


class WHMServerService:
    def __init__(self, repository: SQLWHMServerRepository) -> None:
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="WHM server name already exists",
            ) from exc

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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="WHM server name already exists",
            ) from exc

    async def delete_server(self, *, server_id: UUID) -> bool:
        return await self._repository.delete(server_id=server_id)

    async def validate_server(self, *, server_id: UUID) -> ValidateWHMServerResponse:
        server = await self.get_server(server_id=server_id)
        if server is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="WHM server not found"
            )

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
    async with _session_factory() as session:
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
    server = await whm_server_service.create_server(
        name=payload.name,
        base_url=payload.base_url,
        api_username=payload.api_username,
        api_token=payload.api_token,
        verify_ssl=payload.verify_ssl,
    )
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
    server = await whm_server_service.update_server(
        server_id=server_id,
        name=payload.name,
        base_url=payload.base_url,
        api_username=payload.api_username,
        api_token=payload.api_token,
        verify_ssl=payload.verify_ssl,
    )
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="WHM server not found"
        )
    return UpdateWHMServerResponse(server=_to_server_response(server))


@router.delete("/{server_id}", response_model=DeleteWHMServerResponse)
async def delete_whm_server(
    server_id: UUID,
    _: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> DeleteWHMServerResponse:
    deleted = await whm_server_service.delete_server(server_id=server_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="WHM server not found"
        )
    return DeleteWHMServerResponse(ok=True)


@router.post("/{server_id}/validate", response_model=ValidateWHMServerResponse)
async def validate_whm_server(
    server_id: UUID,
    _: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> ValidateWHMServerResponse:
    return await whm_server_service.validate_server(server_id=server_id)
