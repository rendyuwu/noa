import logging

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from noa_api.api.auth_dependencies import get_active_current_auth_user
from noa_api.api.error_codes import (
    AUTHENTICATION_SERVICE_UNAVAILABLE,
    INVALID_CREDENTIALS,
    USER_PENDING_APPROVAL,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.auth_service import AuthResult, AuthService
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.auth.deps import get_auth_service
from noa_api.core.auth.errors import (
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
)
from noa_api.core.logging_context import log_context

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginUserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: LoginUserResponse


class MeResponse(BaseModel):
    user: LoginUserResponse


def _to_login_response(result: AuthResult) -> LoginResponse:
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        user=LoginUserResponse(
            id=str(result.user_id),
            email=result.email,
            display_name=result.display_name,
            is_active=result.is_active,
            roles=result.roles,
        ),
    )


def _to_me_response(user: AuthorizationUser) -> MeResponse:
    return MeResponse(
        user=LoginUserResponse(
            id=str(user.user_id),
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=user.roles,
        )
    )


def _log_login_rejected(
    *, user_email: str, status_code: int, error_code: str, failure_stage: str
) -> None:
    with log_context(user_email=user_email):
        logger.info(
            "auth_login_rejected",
            extra={
                "status_code": status_code,
                "error_code": error_code,
                "failure_stage": failure_stage,
            },
        )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest, auth_service: AuthService = Depends(get_auth_service)
) -> LoginResponse:
    try:
        result = await auth_service.authenticate(
            email=payload.email, password=payload.password
        )
    except AuthInvalidCredentialsError as exc:
        _log_login_rejected(
            user_email=payload.email,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=INVALID_CREDENTIALS,
            failure_stage="invalid_credentials",
        )
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            error_code=INVALID_CREDENTIALS,
        ) from exc
    except AuthPendingApprovalError as exc:
        _log_login_rejected(
            user_email=payload.email,
            status_code=status.HTTP_403_FORBIDDEN,
            error_code=USER_PENDING_APPROVAL,
            failure_stage="pending_approval",
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User pending approval",
            error_code=USER_PENDING_APPROVAL,
        ) from exc
    except AuthError as exc:
        _log_login_rejected(
            user_email=payload.email,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code=AUTHENTICATION_SERVICE_UNAVAILABLE,
            failure_stage="auth_service",
        )
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
            error_code=AUTHENTICATION_SERVICE_UNAVAILABLE,
        ) from exc

    with log_context(user_id=str(result.user_id), user_email=result.email):
        logger.info(
            "auth_login_succeeded",
            extra={
                "is_active": result.is_active,
                "roles": result.roles,
            },
        )

    return _to_login_response(result)


@router.get("/me", response_model=MeResponse)
async def me(
    current_user: AuthorizationUser = Depends(get_active_current_auth_user),
) -> MeResponse:
    with log_context(user_id=str(current_user.user_id), user_email=current_user.email):
        logger.info(
            "auth_me_succeeded",
            extra={
                "is_active": current_user.is_active,
                "roles": current_user.roles,
            },
        )

    return _to_me_response(current_user)
