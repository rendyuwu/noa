import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import (
    ADMIN_ACCESS_REQUIRED,
    ADMIN_USER_NOT_FOUND,
    LAST_ACTIVE_ADMIN,
    SELF_DEACTIVATE_ADMIN,
    UNKNOWN_TOOLS,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    LastActiveAdminError,
    SelfDeactivateAdminError,
    UnknownToolError,
    get_authorization_service,
)
from noa_api.core.logging_context import log_context

router = APIRouter(prefix="/admin", tags=["admin"])

logger = logging.getLogger(__name__)


class AdminUserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
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
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        roles=user.roles,
        tools=user.tools,
    )


async def _require_admin(
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active or "admin" not in current_user.roles:
        logger.info(
            "admin_access_denied",
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


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminUsersResponse:
    users = await authorization_service.list_users()
    with log_context(user_id=str(admin_user.user_id), user_email=admin_user.email):
        logger.info("admin_users_list_succeeded", extra={"user_count": len(users)})
    return AdminUsersResponse(users=[_to_user_response(user) for user in users])


@router.patch("/users/{id}", response_model=UpdateUserResponse)
async def update_user_active(
    id: UUID,
    payload: UpdateUserRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> UpdateUserResponse:
    with log_context(target_user_id=str(id), user_id=str(admin_user.user_id)):
        try:
            user = await authorization_service.set_user_active(
                id,
                is_active=payload.is_active,
                actor_email=admin_user.email,
                actor_user_id=admin_user.user_id,
            )
        except LastActiveAdminError as exc:
            logger.info("admin_last_active_admin_conflict")
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=LAST_ACTIVE_ADMIN,
            ) from exc
        except SelfDeactivateAdminError as exc:
            logger.info("admin_self_deactivate_conflict")
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=SELF_DEACTIVATE_ADMIN,
            ) from exc
        if user is None:
            logger.info("admin_user_not_found")
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
                error_code=ADMIN_USER_NOT_FOUND,
            )
        logger.info("admin_user_status_updated", extra={"is_active": user.is_active})
    return UpdateUserResponse(user=_to_user_response(user))


@router.get("/tools", response_model=AdminToolsResponse)
async def list_tools(
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminToolsResponse:
    tools = await authorization_service.list_tools()
    with log_context(user_id=str(admin_user.user_id), user_email=admin_user.email):
        logger.info("admin_tools_list_succeeded", extra={"tool_count": len(tools)})
    return AdminToolsResponse(tools=tools)


@router.put("/users/{id}/tools", response_model=UpdateUserResponse)
async def set_user_tools(
    id: UUID,
    payload: SetUserToolsRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> UpdateUserResponse:
    with log_context(target_user_id=str(id), user_id=str(admin_user.user_id)):
        try:
            user = await authorization_service.set_user_tools(
                id, payload.tools, actor_email=admin_user.email
            )
        except UnknownToolError as exc:
            logger.info(
                "admin_unknown_tools",
                extra={"requested_tools": payload.tools},
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=UNKNOWN_TOOLS,
            ) from exc
        if user is None:
            logger.info("admin_user_not_found")
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
                error_code=ADMIN_USER_NOT_FOUND,
            )
        logger.info(
            "admin_user_tools_updated",
            extra={"assigned_tool_count": len(user.tools)},
        )
    return UpdateUserResponse(user=_to_user_response(user))
