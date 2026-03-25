from __future__ import annotations

import logging

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from noa_api.api.error_codes import (
    INVALID_TOKEN,
    MISSING_BEARER_TOKEN,
    USER_PENDING_APPROVAL,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.auth_service import AuthService, AuthUser
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.auth.deps import get_auth_service, get_jwt_service
from noa_api.core.auth.errors import AuthInvalidCredentialsError
from noa_api.core.auth.jwt_service import JWTService
from noa_api.core.logging_context import log_context
from noa_api.core.telemetry import TelemetryEvent, get_telemetry_recorder

_bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)
AUTH_OUTCOMES_TOTAL = "auth.outcomes.total"


def _invalid_token_error() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
        error_code=INVALID_TOKEN,
    )


def _log_current_user_rejected(
    *, status_code: int, error_code: str, failure_stage: str
) -> None:
    logger.info(
        "auth_current_user_rejected",
        extra={
            "status_code": status_code,
            "error_code": error_code,
            "failure_stage": failure_stage,
        },
    )


def _log_current_user_resolved(user: AuthorizationUser) -> None:
    with log_context(user_id=str(user.user_id), user_email=user.email):
        logger.info(
            "auth_current_user_resolved",
            extra={
                "is_active": user.is_active,
                "roles": user.roles,
            },
        )


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


def _record_current_user_resolved_telemetry(
    request: Request, user: AuthorizationUser
) -> None:
    _safe_trace(
        request,
        TelemetryEvent(
            name="auth_current_user_resolved",
            attributes={
                "user_id": str(user.user_id),
                "user_email": user.email,
            },
        ),
    )


def _record_current_user_rejected_telemetry(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    failure_stage: str,
    user: AuthorizationUser | None = None,
) -> None:
    trace_attributes: dict[str, str | int] = {
        "status_code": status_code,
        "error_code": error_code,
        "failure_stage": failure_stage,
    }
    if user is not None:
        trace_attributes["user_id"] = str(user.user_id)
        trace_attributes["user_email"] = user.email

    _safe_trace(
        request,
        TelemetryEvent(
            name="auth_current_user_rejected",
            attributes=trace_attributes,
        ),
    )
    _safe_metric(
        request,
        TelemetryEvent(
            name=AUTH_OUTCOMES_TOTAL,
            attributes={
                "event_name": "auth_current_user_rejected",
                "status_code": status_code,
                "error_code": error_code,
                "failure_stage": failure_stage,
            },
        ),
        value=1,
    )


def _to_authorization_user(user: AuthUser) -> AuthorizationUser:
    return AuthorizationUser(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=user.roles,
        tools=[],
        direct_tools=[],
    )


async def require_auth_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    jwt_service: JWTService = Depends(get_jwt_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUser:
    if credentials is None:
        _log_current_user_rejected(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=MISSING_BEARER_TOKEN,
            failure_stage="credentials",
        )
        _record_current_user_rejected_telemetry(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=MISSING_BEARER_TOKEN,
            failure_stage="credentials",
        )
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            error_code=MISSING_BEARER_TOKEN,
        )

    try:
        payload = jwt_service.decode_token(credentials.credentials)
    except AuthInvalidCredentialsError as exc:
        _log_current_user_rejected(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=INVALID_TOKEN,
            failure_stage="jwt_decode",
        )
        _record_current_user_rejected_telemetry(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=INVALID_TOKEN,
            failure_stage="jwt_decode",
        )
        raise _invalid_token_error() from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        _log_current_user_rejected(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=INVALID_TOKEN,
            failure_stage="jwt_subject",
        )
        _record_current_user_rejected_telemetry(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=INVALID_TOKEN,
            failure_stage="jwt_subject",
        )
        raise _invalid_token_error()

    try:
        user = await auth_service.get_user_by_email(email=subject)
    except AuthInvalidCredentialsError as exc:
        _log_current_user_rejected(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=INVALID_TOKEN,
            failure_stage="user_lookup",
        )
        _record_current_user_rejected_telemetry(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=INVALID_TOKEN,
            failure_stage="user_lookup",
        )
        raise _invalid_token_error() from exc

    return user


async def get_current_auth_user(
    request: Request,
    user: AuthUser = Depends(require_auth_user),
) -> AuthorizationUser:
    current_user = _to_authorization_user(user)
    _log_current_user_resolved(current_user)
    _record_current_user_resolved_telemetry(request, current_user)
    return current_user


async def get_active_current_auth_user(
    request: Request,
    user: AuthUser = Depends(require_auth_user),
) -> AuthorizationUser:
    current_user = _to_authorization_user(user)
    if not current_user.is_active:
        with log_context(
            user_id=str(current_user.user_id), user_email=current_user.email
        ):
            _log_current_user_rejected(
                status_code=status.HTTP_403_FORBIDDEN,
                error_code=USER_PENDING_APPROVAL,
                failure_stage="inactive_user",
            )
        _record_current_user_rejected_telemetry(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            error_code=USER_PENDING_APPROVAL,
            failure_stage="inactive_user",
            user=current_user,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User pending approval",
            error_code=USER_PENDING_APPROVAL,
        )

    _log_current_user_resolved(current_user)
    _record_current_user_resolved_telemetry(request, current_user)
    return current_user
