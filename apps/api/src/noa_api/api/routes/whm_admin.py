from __future__ import annotations

from datetime import datetime
import logging
from typing import Any
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from noa_api.api.admin.guards import _require_admin
from noa_api.api.error_codes import (
    WHM_SERVER_NAME_EXISTS,
    WHM_SERVER_NOT_FOUND,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.api.routes.server_validation import (
    normalize_https_base_url,
    validate_server_name,
)
from noa_api.api.whm_admin.schemas import ValidateWHMServerResponse
from noa_api.api.whm_admin.service import (
    WHMServerNameExistsError,
    WHMServerNotFoundError,
    WHMServerService,
    get_whm_server_service,
)
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.logging_context import log_context
from noa_api.api.route_telemetry import safe_metric, safe_trace, status_family
from noa_api.core.telemetry import TelemetryEvent

router = APIRouter(prefix="/admin/whm/servers", tags=["admin"])

logger = logging.getLogger(__name__)
WHM_OUTCOMES_TOTAL = "whm.outcomes.total"
_WHM_API_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


class WHMServerResponse(BaseModel):
    id: str
    name: str
    base_url: str
    api_username: str
    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_host_key_fingerprint: str | None = None
    has_ssh_password: bool = False
    has_ssh_private_key: bool = False
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
    ssh_username: str | None = Field(default=None, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_password: str | None = Field(default=None)
    ssh_private_key: str | None = Field(default=None)
    ssh_private_key_passphrase: str | None = Field(default=None)
    verify_ssl: bool = True

    @field_validator(
        "name",
        "base_url",
        "api_username",
        "api_token",
        "ssh_username",
        "ssh_password",
        "ssh_private_key",
        "ssh_private_key_passphrase",
        mode="before",
    )
    @classmethod
    def _normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return validate_server_name(value, label="WHM")

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return normalize_https_base_url(value, label="WHM")

    @field_validator("api_username")
    @classmethod
    def _validate_api_username(cls, value: str) -> str:
        return _validate_api_username(value)


class CreateWHMServerResponse(BaseModel):
    server: WHMServerResponse


class UpdateWHMServerRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=500)
    api_username: str | None = Field(default=None, max_length=255)
    api_token: str | None = Field(default=None, min_length=1)
    ssh_username: str | None = Field(default=None, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_password: str | None = Field(default=None)
    ssh_private_key: str | None = Field(default=None)
    ssh_private_key_passphrase: str | None = Field(default=None)
    clear_ssh_configuration: bool = False
    clear_ssh_username: bool = False
    clear_ssh_port: bool = False
    clear_ssh_password: bool = False
    clear_ssh_private_key: bool = False
    clear_ssh_private_key_passphrase: bool = False
    clear_ssh_host_key_fingerprint: bool = False
    verify_ssl: bool | None = None

    @field_validator(
        "name",
        "base_url",
        "api_username",
        "api_token",
        "ssh_username",
        "ssh_password",
        "ssh_private_key",
        "ssh_private_key_passphrase",
        mode="before",
    )
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

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_server_name(value, label="WHM")

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_https_base_url(value, label="WHM")

    @field_validator("api_username")
    @classmethod
    def _validate_api_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_api_username(value)


class UpdateWHMServerResponse(BaseModel):
    server: WHMServerResponse


class DeleteWHMServerResponse(BaseModel):
    ok: bool


def _validate_api_username(value: str) -> str:
    if not _WHM_API_USERNAME_RE.fullmatch(value):
        raise ValueError("String should be a valid WHM API username")
    return value


def _to_server_response(server: Any) -> WHMServerResponse:
    safe = server.to_safe_dict()
    return WHMServerResponse.model_validate(safe)


def _record_whm_outcome(
    request: Request,
    *,
    event_name: str,
    status_code: int,
    trace_attributes: dict[str, str | int | bool],
    error_code: str | None = None,
    metric_attributes: dict[str, str | bool] | None = None,
) -> None:
    event_attributes = dict(trace_attributes)
    if error_code is not None:
        event_attributes["error_code"] = error_code
        event_attributes["status_code"] = status_code

    safe_trace(
        request,
        TelemetryEvent(name=event_name, attributes=event_attributes),
    )

    bounded_metric_attributes: dict[str, str | bool] = {
        "event_name": event_name,
        "status_family": status_family(status_code),
    }
    if error_code is not None:
        bounded_metric_attributes["error_code"] = error_code
    if metric_attributes is not None:
        bounded_metric_attributes.update(metric_attributes)

    safe_metric(
        request,
        TelemetryEvent(name=WHM_OUTCOMES_TOTAL, attributes=bounded_metric_attributes),
        value=1,
    )


@router.get("", response_model=WHMServerListResponse)
async def list_whm_servers(
    request: Request,
    admin_user: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> WHMServerListResponse:
    servers = await whm_server_service.list_servers()
    with log_context(user_id=str(admin_user.user_id)):
        logger.info("whm_servers_list_succeeded", extra={"server_count": len(servers)})
    _record_whm_outcome(
        request,
        event_name="whm_servers_list_succeeded",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "server_count": len(servers),
            "user_id": str(admin_user.user_id),
        },
    )
    return WHMServerListResponse(servers=[_to_server_response(s) for s in servers])


@router.post("", response_model=CreateWHMServerResponse)
async def create_whm_server(
    request: Request,
    payload: CreateWHMServerRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> CreateWHMServerResponse | Response:
    with log_context(server_name=payload.name, user_id=str(admin_user.user_id)):
        try:
            server = await whm_server_service.create_server(
                name=payload.name,
                base_url=payload.base_url,
                api_username=payload.api_username,
                api_token=payload.api_token,
                ssh_username=payload.ssh_username,
                ssh_port=payload.ssh_port,
                ssh_password=payload.ssh_password,
                ssh_private_key=payload.ssh_private_key,
                ssh_private_key_passphrase=payload.ssh_private_key_passphrase,
                verify_ssl=payload.verify_ssl,
            )
        except WHMServerNameExistsError as exc:
            logger.info("whm_server_name_conflict")
            _record_whm_outcome(
                request,
                event_name="whm_server_name_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "server_name": payload.name,
                    "user_id": str(admin_user.user_id),
                },
                error_code=WHM_SERVER_NAME_EXISTS,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="WHM server name already exists",
                error_code=WHM_SERVER_NAME_EXISTS,
            ) from exc
        logger.info("whm_server_created")
    _record_whm_outcome(
        request,
        event_name="whm_server_created",
        status_code=status.HTTP_201_CREATED,
        trace_attributes={
            "server_name": payload.name,
            "user_id": str(admin_user.user_id),
        },
    )
    response = CreateWHMServerResponse(server=_to_server_response(server))
    return JSONResponse(
        content=jsonable_encoder(response.model_dump()),
        status_code=status.HTTP_201_CREATED,
    )


@router.patch("/{server_id}", response_model=UpdateWHMServerResponse)
async def update_whm_server(
    request: Request,
    server_id: UUID,
    payload: UpdateWHMServerRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> UpdateWHMServerResponse:
    with log_context(
        server_id=str(server_id),
        server_name=payload.name,
        user_id=str(admin_user.user_id),
    ):
        try:
            server = await whm_server_service.update_server(
                server_id=server_id,
                name=payload.name,
                base_url=payload.base_url,
                api_username=payload.api_username,
                api_token=payload.api_token,
                ssh_username=payload.ssh_username,
                ssh_port=payload.ssh_port,
                ssh_password=payload.ssh_password,
                ssh_private_key=payload.ssh_private_key,
                ssh_private_key_passphrase=payload.ssh_private_key_passphrase,
                clear_ssh_configuration=payload.clear_ssh_configuration,
                clear_ssh_username=payload.clear_ssh_username,
                clear_ssh_port=payload.clear_ssh_port,
                clear_ssh_password=payload.clear_ssh_password,
                clear_ssh_private_key=payload.clear_ssh_private_key,
                clear_ssh_private_key_passphrase=payload.clear_ssh_private_key_passphrase,
                clear_ssh_host_key_fingerprint=payload.clear_ssh_host_key_fingerprint,
                verify_ssl=payload.verify_ssl,
            )
        except WHMServerNameExistsError as exc:
            logger.info("whm_server_name_conflict")
            _record_whm_outcome(
                request,
                event_name="whm_server_name_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=WHM_SERVER_NAME_EXISTS,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="WHM server name already exists",
                error_code=WHM_SERVER_NAME_EXISTS,
            ) from exc
        if server is None:
            logger.info("whm_server_not_found")
            _record_whm_outcome(
                request,
                event_name="whm_server_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=WHM_SERVER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="WHM server not found",
                error_code=WHM_SERVER_NOT_FOUND,
            )
        logger.info("whm_server_updated")
        _record_whm_outcome(
            request,
            event_name="whm_server_updated",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "server_id": str(server_id),
                "user_id": str(admin_user.user_id),
            },
        )
    return UpdateWHMServerResponse(server=_to_server_response(server))


@router.delete("/{server_id}", response_model=DeleteWHMServerResponse)
async def delete_whm_server(
    request: Request,
    server_id: UUID,
    admin_user: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> DeleteWHMServerResponse:
    with log_context(server_id=str(server_id), user_id=str(admin_user.user_id)):
        deleted = await whm_server_service.delete_server(server_id=server_id)
        if not deleted:
            logger.info("whm_server_not_found")
            _record_whm_outcome(
                request,
                event_name="whm_server_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=WHM_SERVER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="WHM server not found",
                error_code=WHM_SERVER_NOT_FOUND,
            )
        logger.info("whm_server_deleted")
        _record_whm_outcome(
            request,
            event_name="whm_server_deleted",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "server_id": str(server_id),
                "user_id": str(admin_user.user_id),
            },
        )
    return DeleteWHMServerResponse(ok=True)


@router.post("/{server_id}/validate", response_model=ValidateWHMServerResponse)
async def validate_whm_server(
    request: Request,
    server_id: UUID,
    admin_user: AuthorizationUser = Depends(_require_admin),
    whm_server_service: WHMServerService = Depends(get_whm_server_service),
) -> ValidateWHMServerResponse:
    with log_context(server_id=str(server_id), user_id=str(admin_user.user_id)):
        try:
            result = await whm_server_service.validate_server(server_id=server_id)
        except WHMServerNotFoundError as exc:
            logger.info("whm_server_not_found")
            _record_whm_outcome(
                request,
                event_name="whm_server_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=WHM_SERVER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="WHM server not found",
                error_code=WHM_SERVER_NOT_FOUND,
            ) from exc
        logger.info(
            "whm_server_validated",
            extra={
                "validation_ok": result.ok,
                "validation_error_code": result.error_code,
            },
        )
        metric_attributes: dict[str, str | bool] = {"validation_ok": result.ok}
        _record_whm_outcome(
            request,
            event_name="whm_server_validated",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "server_id": str(server_id),
                "user_id": str(admin_user.user_id),
                "validation_error_code": result.error_code or "",
                "validation_ok": result.ok,
            },
            metric_attributes=metric_attributes,
        )
        return result
