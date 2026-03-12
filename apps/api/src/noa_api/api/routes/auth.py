from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from noa_api.core.auth.auth_service import AuthResult, AuthService, AuthUser
from noa_api.core.auth.deps import get_auth_service, get_jwt_service
from noa_api.core.auth.errors import (
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
)
from noa_api.core.auth.jwt_service import JWTService

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer_scheme = HTTPBearer(auto_error=False)


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


def _to_me_response(user: AuthUser) -> MeResponse:
    return MeResponse(
        user=LoginUserResponse(
            id=str(user.user_id),
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=user.roles,
        )
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        ) from exc
    except AuthPendingApprovalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User pending approval"
        ) from exc
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc

    return _to_login_response(result)


@router.get("/me", response_model=MeResponse)
async def me(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    jwt_service: JWTService = Depends(get_jwt_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> MeResponse:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )

    try:
        payload = jwt_service.decode_token(credentials.credentials)
    except AuthInvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    try:
        user = await auth_service.get_user_by_email(email=subject)
    except AuthInvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User pending approval"
        )

    return _to_me_response(user)
