from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import (
    ADMIN_ACCESS_REQUIRED,
    PROXMOX_SERVER_NAME_EXISTS,
    PROXMOX_SERVER_NOT_FOUND,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.api.proxmox_admin.schemas import ValidateProxmoxServerResponse
from noa_api.api.proxmox_admin.service import (
    ProxmoxServerNameExistsError,
    ProxmoxServerNotFoundError,
    ProxmoxServerService,
    get_proxmox_server_service,
)
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.logging_context import log_context
from noa_api.core.telemetry import TelemetryEvent, get_telemetry_recorder

router = APIRouter(prefix="/admin/proxmox/servers", tags=["admin"])

logger = logging.getLogger(__name__)
PROXMOX_OUTCOMES_TOTAL = "proxmox.outcomes.total"
_PROXMOX_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


class ProxmoxServerResponse(BaseModel):
    id: str
    name: str
    base_url: str
    api_token_id: str
    has_api_token_secret: bool = False
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime


class ProxmoxServerListResponse(BaseModel):
    servers: list[ProxmoxServerResponse]


class CreateProxmoxServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=1, max_length=500)
    api_token_id: str = Field(min_length=1, max_length=255)
    api_token_secret: str = Field(min_length=1)
    verify_ssl: bool = False

    @field_validator(
        "name",
        "base_url",
        "api_token_id",
        "api_token_secret",
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
        return _validate_server_name(value)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return _normalize_proxmox_base_url(value)


class CreateProxmoxServerResponse(BaseModel):
    server: ProxmoxServerResponse


class UpdateProxmoxServerRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=500)
    api_token_id: str | None = Field(default=None, max_length=255)
    api_token_secret: str | None = Field(default=None, min_length=1)
    verify_ssl: bool | None = None

    @field_validator(
        "name",
        "base_url",
        "api_token_id",
        "api_token_secret",
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
        return _validate_server_name(value)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_proxmox_base_url(value)


class UpdateProxmoxServerResponse(BaseModel):
    server: ProxmoxServerResponse


class DeleteProxmoxServerResponse(BaseModel):
    ok: bool


def _validate_server_name(value: str) -> str:
    if not _PROXMOX_SERVER_NAME_RE.fullmatch(value):
        raise ValueError("String should be a valid Proxmox server name")
    return value


def _normalize_proxmox_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise ValueError("String should be a valid HTTPS Proxmox base URL")
    if not parsed.hostname:
        raise ValueError("String should be a valid HTTPS Proxmox base URL")
    if parsed.username or parsed.password:
        raise ValueError("String should be a valid HTTPS Proxmox base URL")
    if parsed.path not in {"", "/"}:
        raise ValueError("String should be a valid HTTPS Proxmox base URL")
    if parsed.query or parsed.fragment:
        raise ValueError("String should be a valid HTTPS Proxmox base URL")

    hostname = parsed.hostname
    assert hostname is not None
    try:
        port_value = parsed.port
    except ValueError as exc:
        raise ValueError("String should be a valid HTTPS Proxmox base URL") from exc
    port = f":{port_value}" if port_value is not None else ""
    return f"https://{hostname}{port}"


def _to_server_response(server: Any) -> ProxmoxServerResponse:
    safe = server.to_safe_dict()
    return ProxmoxServerResponse.model_validate(safe)


def _status_family(status_code: int) -> str:
    return f"{status_code // 100}xx"


def _safe_trace(request: Request, event: TelemetryEvent) -> None:
    try:
        get_telemetry_recorder(request.app).trace(event)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "trace",
                "telemetry_event": event.name,
            },
        )


def _safe_metric(
    request: Request, event: TelemetryEvent, *, value: int | float
) -> None:
    try:
        get_telemetry_recorder(request.app).metric(event, value=value)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "metric",
                "telemetry_event": event.name,
            },
        )


def _record_proxmox_outcome(
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

    _safe_trace(
        request,
        TelemetryEvent(name=event_name, attributes=event_attributes),
    )

    bounded_metric_attributes: dict[str, str | bool] = {
        "event_name": event_name,
        "status_family": _status_family(status_code),
    }
    if error_code is not None:
        bounded_metric_attributes["error_code"] = error_code
    if metric_attributes is not None:
        bounded_metric_attributes.update(metric_attributes)

    _safe_metric(
        request,
        TelemetryEvent(
            name=PROXMOX_OUTCOMES_TOTAL,
            attributes=bounded_metric_attributes,
        ),
        value=1,
    )


async def _require_admin(
    request: Request,
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active or "admin" not in current_user.roles:
        logger.info(
            "proxmox_admin_access_denied",
            extra={
                "is_active": current_user.is_active,
                "roles": current_user.roles,
                "user_id": str(current_user.user_id),
            },
        )
        _record_proxmox_outcome(
            request,
            event_name="proxmox_admin_access_denied",
            status_code=status.HTTP_403_FORBIDDEN,
            trace_attributes={
                "is_active": current_user.is_active,
                "user_id": str(current_user.user_id),
            },
            error_code=ADMIN_ACCESS_REQUIRED,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
            error_code=ADMIN_ACCESS_REQUIRED,
        )
    return current_user


@router.get("", response_model=ProxmoxServerListResponse)
async def list_proxmox_servers(
    request: Request,
    admin_user: AuthorizationUser = Depends(_require_admin),
    proxmox_server_service: ProxmoxServerService = Depends(get_proxmox_server_service),
) -> ProxmoxServerListResponse:
    servers = await proxmox_server_service.list_servers()
    with log_context(user_id=str(admin_user.user_id)):
        logger.info(
            "proxmox_servers_list_succeeded", extra={"server_count": len(servers)}
        )
    _record_proxmox_outcome(
        request,
        event_name="proxmox_servers_list_succeeded",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "server_count": len(servers),
            "user_id": str(admin_user.user_id),
        },
    )
    return ProxmoxServerListResponse(
        servers=[_to_server_response(server) for server in servers]
    )


@router.post("", response_model=CreateProxmoxServerResponse)
async def create_proxmox_server(
    request: Request,
    payload: CreateProxmoxServerRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    proxmox_server_service: ProxmoxServerService = Depends(get_proxmox_server_service),
) -> CreateProxmoxServerResponse | Response:
    with log_context(server_name=payload.name, user_id=str(admin_user.user_id)):
        try:
            server = await proxmox_server_service.create_server(
                name=payload.name,
                base_url=payload.base_url,
                api_token_id=payload.api_token_id,
                api_token_secret=payload.api_token_secret,
                verify_ssl=payload.verify_ssl,
            )
        except ProxmoxServerNameExistsError as exc:
            logger.info("proxmox_server_name_conflict")
            _record_proxmox_outcome(
                request,
                event_name="proxmox_server_name_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "server_name": payload.name,
                    "user_id": str(admin_user.user_id),
                },
                error_code=PROXMOX_SERVER_NAME_EXISTS,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Proxmox server name already exists",
                error_code=PROXMOX_SERVER_NAME_EXISTS,
            ) from exc
        logger.info("proxmox_server_created")
    _record_proxmox_outcome(
        request,
        event_name="proxmox_server_created",
        status_code=status.HTTP_201_CREATED,
        trace_attributes={
            "server_name": payload.name,
            "user_id": str(admin_user.user_id),
        },
    )
    response = CreateProxmoxServerResponse(server=_to_server_response(server))
    return JSONResponse(
        content=jsonable_encoder(response.model_dump()),
        status_code=status.HTTP_201_CREATED,
    )


@router.patch("/{server_id}", response_model=UpdateProxmoxServerResponse)
async def update_proxmox_server(
    request: Request,
    server_id: UUID,
    payload: UpdateProxmoxServerRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    proxmox_server_service: ProxmoxServerService = Depends(get_proxmox_server_service),
) -> UpdateProxmoxServerResponse:
    with log_context(
        server_id=str(server_id),
        server_name=payload.name,
        user_id=str(admin_user.user_id),
    ):
        try:
            server = await proxmox_server_service.update_server(
                server_id=server_id,
                name=payload.name,
                base_url=payload.base_url,
                api_token_id=payload.api_token_id,
                api_token_secret=payload.api_token_secret,
                verify_ssl=payload.verify_ssl,
            )
        except ProxmoxServerNameExistsError as exc:
            logger.info("proxmox_server_name_conflict")
            _record_proxmox_outcome(
                request,
                event_name="proxmox_server_name_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=PROXMOX_SERVER_NAME_EXISTS,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Proxmox server name already exists",
                error_code=PROXMOX_SERVER_NAME_EXISTS,
            ) from exc
        if server is None:
            logger.info("proxmox_server_not_found")
            _record_proxmox_outcome(
                request,
                event_name="proxmox_server_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=PROXMOX_SERVER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proxmox server not found",
                error_code=PROXMOX_SERVER_NOT_FOUND,
            )
        logger.info("proxmox_server_updated")
        _record_proxmox_outcome(
            request,
            event_name="proxmox_server_updated",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "server_id": str(server_id),
                "user_id": str(admin_user.user_id),
            },
        )
    return UpdateProxmoxServerResponse(server=_to_server_response(server))


@router.delete("/{server_id}", response_model=DeleteProxmoxServerResponse)
async def delete_proxmox_server(
    request: Request,
    server_id: UUID,
    admin_user: AuthorizationUser = Depends(_require_admin),
    proxmox_server_service: ProxmoxServerService = Depends(get_proxmox_server_service),
) -> DeleteProxmoxServerResponse:
    with log_context(server_id=str(server_id), user_id=str(admin_user.user_id)):
        deleted = await proxmox_server_service.delete_server(server_id=server_id)
        if not deleted:
            logger.info("proxmox_server_not_found")
            _record_proxmox_outcome(
                request,
                event_name="proxmox_server_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=PROXMOX_SERVER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proxmox server not found",
                error_code=PROXMOX_SERVER_NOT_FOUND,
            )
        logger.info("proxmox_server_deleted")
        _record_proxmox_outcome(
            request,
            event_name="proxmox_server_deleted",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "server_id": str(server_id),
                "user_id": str(admin_user.user_id),
            },
        )
    return DeleteProxmoxServerResponse(ok=True)


@router.post("/{server_id}/validate", response_model=ValidateProxmoxServerResponse)
async def validate_proxmox_server(
    request: Request,
    server_id: UUID,
    admin_user: AuthorizationUser = Depends(_require_admin),
    proxmox_server_service: ProxmoxServerService = Depends(get_proxmox_server_service),
) -> ValidateProxmoxServerResponse:
    with log_context(server_id=str(server_id), user_id=str(admin_user.user_id)):
        try:
            result = await proxmox_server_service.validate_server(server_id=server_id)
        except ProxmoxServerNotFoundError as exc:
            logger.info("proxmox_server_not_found")
            _record_proxmox_outcome(
                request,
                event_name="proxmox_server_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "server_id": str(server_id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=PROXMOX_SERVER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proxmox server not found",
                error_code=PROXMOX_SERVER_NOT_FOUND,
            ) from exc
        logger.info(
            "proxmox_server_validated",
            extra={
                "validation_ok": result.ok,
                "validation_error_code": result.error_code,
            },
        )
        metric_attributes: dict[str, str | bool] = {"validation_ok": result.ok}
        _record_proxmox_outcome(
            request,
            event_name="proxmox_server_validated",
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
