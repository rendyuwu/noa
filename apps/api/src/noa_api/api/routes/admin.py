from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    LastActiveAdminError,
    SelfDeactivateAdminError,
    UnknownToolError,
    get_authorization_service,
    get_current_auth_user,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]
    tools: list[str]


class AdminUsersResponse(BaseModel):
    users: list[AdminUserResponse]


class UpdateUserRequest(BaseModel):
    is_active: bool


class UpdateUserResponse(BaseModel):
    user: AdminUserResponse


class AdminToolsResponse(BaseModel):
    tools: list[str]


class SetUserToolsRequest(BaseModel):
    tools: list[str]


def _to_user_response(user: AuthorizationUser) -> AdminUserResponse:
    return AdminUserResponse(
        id=str(user.user_id),
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=user.roles,
        tools=user.tools,
    )


async def _require_admin(current_user: AuthorizationUser = Depends(get_current_auth_user)) -> AuthorizationUser:
    if not current_user.is_active or "admin" not in current_user.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    _: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminUsersResponse:
    users = await authorization_service.list_users()
    return AdminUsersResponse(users=[_to_user_response(user) for user in users])


@router.patch("/users/{id}", response_model=UpdateUserResponse)
async def update_user_active(
    id: UUID,
    payload: UpdateUserRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> UpdateUserResponse:
    try:
        user = await authorization_service.set_user_active(
            id,
            is_active=payload.is_active,
            actor_email=admin_user.email,
            actor_user_id=admin_user.user_id,
        )
    except (LastActiveAdminError, SelfDeactivateAdminError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UpdateUserResponse(user=_to_user_response(user))


@router.get("/tools", response_model=AdminToolsResponse)
async def list_tools(
    _: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminToolsResponse:
    tools = await authorization_service.list_tools()
    return AdminToolsResponse(tools=tools)


@router.put("/users/{id}/tools", response_model=UpdateUserResponse)
async def set_user_tools(
    id: UUID,
    payload: SetUserToolsRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> UpdateUserResponse:
    try:
        user = await authorization_service.set_user_tools(id, payload.tools, actor_email=admin_user.email)
    except UnknownToolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UpdateUserResponse(user=_to_user_response(user))
