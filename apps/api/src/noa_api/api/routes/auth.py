from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from noa_api.core.auth.auth_service import AuthResult, AuthService
from noa_api.core.auth.deps import get_auth_service
from noa_api.core.auth.errors import AuthInvalidCredentialsError, AuthPendingApprovalError

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, auth_service: AuthService = Depends(get_auth_service)) -> LoginResponse:
    try:
        result = await auth_service.authenticate(email=payload.email, password=payload.password)
    except AuthInvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials") from exc
    except AuthPendingApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User pending approval") from exc

    return _to_login_response(result)
