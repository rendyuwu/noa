import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import (
    ADMIN_ACCESS_REQUIRED,
    ADMIN_ROLE_NOT_FOUND,
    ADMIN_USER_NOT_FOUND,
    DIRECT_TOOL_GRANTS_DISABLED,
    INTERNAL_ROLE_FORBIDDEN,
    INVALID_ROLE_NAME,
    LAST_ACTIVE_ADMIN,
    RESERVED_ROLE,
    SELF_DELETE_ADMIN,
    SELF_DEACTIVATE_ADMIN,
    SELF_REMOVE_ADMIN_ROLE,
    UNKNOWN_TOOLS,
    UNKNOWN_ROLES,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    InternalRoleError,
    LastActiveAdminError,
    InvalidRoleNameError,
    ReservedRoleError,
    SelfDeleteAdminError,
    SelfDeactivateAdminError,
    SelfRemoveAdminRoleError,
    UnknownToolError,
    UnknownRoleError,
    get_authorization_service,
)
from noa_api.api.route_telemetry import record_route_outcome
from noa_api.core.logging_context import log_context

router = APIRouter(prefix="/admin", tags=["admin"])

logger = logging.getLogger(__name__)
ADMIN_OUTCOMES_TOTAL = "admin.outcomes.total"


class AdminUserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    roles: list[str]
    tools: list[str]
    direct_tools: list[str]


class AdminUsersResponse(BaseModel):
    users: list[AdminUserResponse]


class UpdateUserRequest(BaseModel):
    is_active: bool


class UpdateUserResponse(BaseModel):
    user: AdminUserResponse


class DeleteUserResponse(BaseModel):
    ok: bool


class AdminToolsResponse(BaseModel):
    tools: list[str]


class SetUserToolsRequest(BaseModel):
    tools: list[str]


class DirectGrantsMigrationResponse(BaseModel):
    users_migrated: int
    roles_created: int
    roles_reused: int
    internal_roles_deleted: int
    tool_grant_count: int
    created_roles: list[str] = Field(default_factory=list)


def _to_user_response(user: AuthorizationUser) -> AdminUserResponse:
    assert user.created_at is not None
    return AdminUserResponse(
        id=str(user.user_id),
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        roles=[role for role in user.roles if not role.startswith("user:")],
        tools=user.tools,
        direct_tools=user.direct_tools,
    )


class AdminRolesResponse(BaseModel):
    roles: list[str]


class CreateRoleRequest(BaseModel):
    name: str


class AdminRoleResponse(BaseModel):
    name: str


class DeleteRoleResponse(BaseModel):
    ok: bool


class SetRoleToolsRequest(BaseModel):
    tools: list[str]


class RoleToolsResponse(BaseModel):
    tools: list[str]


class SetUserRolesRequest(BaseModel):
    roles: list[str]


def _record_admin_outcome(
    request: Request,
    *,
    event_name: str,
    status_code: int,
    trace_attributes: dict[str, str | int | bool],
    error_code: str | None = None,
) -> None:
    record_route_outcome(
        request,
        metric_name=ADMIN_OUTCOMES_TOTAL,
        event_name=event_name,
        status_code=status_code,
        trace_attributes=trace_attributes,
        error_code=error_code,
    )


async def _require_admin(
    request: Request,
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
        _record_admin_outcome(
            request,
            event_name="admin_access_denied",
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


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    request: Request,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminUsersResponse:
    users = await authorization_service.list_users()
    with log_context(user_id=str(admin_user.user_id), user_email=admin_user.email):
        logger.info("admin_users_list_succeeded", extra={"user_count": len(users)})
    _record_admin_outcome(
        request,
        event_name="admin_users_list_succeeded",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "user_count": len(users),
            "user_email": admin_user.email,
            "user_id": str(admin_user.user_id),
        },
    )
    return AdminUsersResponse(users=[_to_user_response(user) for user in users])


@router.patch("/users/{id}", response_model=UpdateUserResponse)
async def update_user_active(
    request: Request,
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
            _record_admin_outcome(
                request,
                event_name="admin_last_active_admin_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=LAST_ACTIVE_ADMIN,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=LAST_ACTIVE_ADMIN,
            ) from exc
        except SelfDeactivateAdminError as exc:
            logger.info("admin_self_deactivate_conflict")
            _record_admin_outcome(
                request,
                event_name="admin_self_deactivate_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=SELF_DEACTIVATE_ADMIN,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=SELF_DEACTIVATE_ADMIN,
            ) from exc
        if user is None:
            logger.info("admin_user_not_found")
            _record_admin_outcome(
                request,
                event_name="admin_user_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=ADMIN_USER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
                error_code=ADMIN_USER_NOT_FOUND,
            )
        logger.info("admin_user_status_updated", extra={"is_active": user.is_active})
        _record_admin_outcome(
            request,
            event_name="admin_user_status_updated",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "is_active": user.is_active,
                "target_user_id": str(id),
                "user_id": str(admin_user.user_id),
            },
        )
    return UpdateUserResponse(user=_to_user_response(user))


@router.delete("/users/{id}", response_model=DeleteUserResponse)
async def delete_user(
    request: Request,
    id: UUID,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> DeleteUserResponse:
    with log_context(target_user_id=str(id), user_id=str(admin_user.user_id)):
        try:
            deleted_user = await authorization_service.delete_user(
                id,
                actor_email=admin_user.email,
                actor_user_id=admin_user.user_id,
            )
        except LastActiveAdminError as exc:
            logger.info("admin_last_active_admin_conflict")
            _record_admin_outcome(
                request,
                event_name="admin_last_active_admin_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=LAST_ACTIVE_ADMIN,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=LAST_ACTIVE_ADMIN,
            ) from exc
        except SelfDeleteAdminError as exc:
            logger.info("admin_self_delete_conflict")
            _record_admin_outcome(
                request,
                event_name="admin_self_delete_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=SELF_DELETE_ADMIN,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=SELF_DELETE_ADMIN,
            ) from exc
        if deleted_user is None:
            logger.info("admin_user_not_found")
            _record_admin_outcome(
                request,
                event_name="admin_user_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=ADMIN_USER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
                error_code=ADMIN_USER_NOT_FOUND,
            )
        logger.info(
            "admin_user_deleted", extra={"deleted_user_email": deleted_user.email}
        )
        _record_admin_outcome(
            request,
            event_name="admin_user_deleted",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "target_user_id": str(id),
                "user_id": str(admin_user.user_id),
            },
        )
    return DeleteUserResponse(ok=True)


@router.get("/tools", response_model=AdminToolsResponse)
async def list_tools(
    request: Request,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminToolsResponse:
    tools = await authorization_service.list_tools()
    with log_context(user_id=str(admin_user.user_id), user_email=admin_user.email):
        logger.info("admin_tools_list_succeeded", extra={"tool_count": len(tools)})
    _record_admin_outcome(
        request,
        event_name="admin_tools_list_succeeded",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "tool_count": len(tools),
            "user_email": admin_user.email,
            "user_id": str(admin_user.user_id),
        },
    )
    return AdminToolsResponse(tools=tools)


@router.put("/users/{id}/tools", response_model=UpdateUserResponse)
async def set_user_tools(
    request: Request,
    id: UUID,
    payload: SetUserToolsRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> UpdateUserResponse:
    _ = payload
    _ = authorization_service
    with log_context(target_user_id=str(id), user_id=str(admin_user.user_id)):
        logger.info("admin_direct_tool_grants_disabled")
        _record_admin_outcome(
            request,
            event_name="admin_direct_tool_grants_disabled",
            status_code=status.HTTP_410_GONE,
            trace_attributes={
                "target_user_id": str(id),
                "user_id": str(admin_user.user_id),
            },
            error_code=DIRECT_TOOL_GRANTS_DISABLED,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Direct tool grants are disabled",
            error_code=DIRECT_TOOL_GRANTS_DISABLED,
        )


@router.post(
    "/migrations/direct-grants",
    response_model=DirectGrantsMigrationResponse,
)
async def migrate_direct_grants(
    request: Request,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> DirectGrantsMigrationResponse:
    with log_context(user_id=str(admin_user.user_id), user_email=admin_user.email):
        summary = await authorization_service.migrate_legacy_direct_grants(
            actor_email=admin_user.email
        )
        logger.info("admin_migration_direct_grants_completed", extra=summary)
        _record_admin_outcome(
            request,
            event_name="admin_migration_direct_grants_completed",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "user_id": str(admin_user.user_id),
                "users_migrated": summary["users_migrated"],
                "roles_created": summary["roles_created"],
                "roles_reused": summary["roles_reused"],
                "internal_roles_deleted": summary["internal_roles_deleted"],
            },
        )
    return DirectGrantsMigrationResponse(**summary)


@router.get("/roles", response_model=AdminRolesResponse)
async def list_roles(
    request: Request,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminRolesResponse:
    roles = await authorization_service.list_roles()
    with log_context(user_id=str(admin_user.user_id), user_email=admin_user.email):
        logger.info("admin_roles_list_succeeded", extra={"role_count": len(roles)})
    _record_admin_outcome(
        request,
        event_name="admin_roles_list_succeeded",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "role_count": len(roles),
            "user_email": admin_user.email,
            "user_id": str(admin_user.user_id),
        },
    )
    return AdminRolesResponse(roles=roles)


@router.post("/roles", response_model=AdminRoleResponse)
async def create_role(
    request: Request,
    payload: CreateRoleRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AdminRoleResponse:
    with log_context(user_id=str(admin_user.user_id)):
        try:
            role_name = await authorization_service.create_role(
                payload.name, actor_email=admin_user.email
            )
        except InvalidRoleNameError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_name_invalid",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=INVALID_ROLE_NAME,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=INVALID_ROLE_NAME,
            ) from exc
        except ReservedRoleError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_reserved",
                status_code=status.HTTP_403_FORBIDDEN,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=RESERVED_ROLE,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
                error_code=RESERVED_ROLE,
            ) from exc

    logger.info("admin_role_created", extra={"role": role_name})
    _record_admin_outcome(
        request,
        event_name="admin_role_created",
        status_code=status.HTTP_200_OK,
        trace_attributes={"role": role_name, "user_id": str(admin_user.user_id)},
    )
    return AdminRoleResponse(name=role_name)


@router.delete("/roles/{name}", response_model=DeleteRoleResponse)
async def delete_role(
    request: Request,
    name: str,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> DeleteRoleResponse:
    with log_context(user_id=str(admin_user.user_id), role=name):
        try:
            ok = await authorization_service.delete_role(
                name, actor_email=admin_user.email
            )
        except InvalidRoleNameError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_name_invalid",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=INVALID_ROLE_NAME,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=INVALID_ROLE_NAME,
            ) from exc
        except ReservedRoleError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_reserved",
                status_code=status.HTTP_403_FORBIDDEN,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=RESERVED_ROLE,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
                error_code=RESERVED_ROLE,
            ) from exc

    if not ok:
        _record_admin_outcome(
            request,
            event_name="admin_role_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
            trace_attributes={"user_id": str(admin_user.user_id), "role": name},
            error_code=ADMIN_ROLE_NOT_FOUND,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
            error_code=ADMIN_ROLE_NOT_FOUND,
        )
    _record_admin_outcome(
        request,
        event_name="admin_role_deleted",
        status_code=status.HTTP_200_OK,
        trace_attributes={"user_id": str(admin_user.user_id), "role": name},
    )
    return DeleteRoleResponse(ok=True)


@router.get("/roles/{name}/tools", response_model=RoleToolsResponse)
async def get_role_tools(
    request: Request,
    name: str,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> RoleToolsResponse:
    with log_context(user_id=str(admin_user.user_id), role=name):
        try:
            tools = await authorization_service.get_role_tools(name)
        except InvalidRoleNameError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_name_invalid",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=INVALID_ROLE_NAME,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=INVALID_ROLE_NAME,
            ) from exc
    if tools is None:
        _record_admin_outcome(
            request,
            event_name="admin_role_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
            trace_attributes={"user_id": str(admin_user.user_id), "role": name},
            error_code=ADMIN_ROLE_NOT_FOUND,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
            error_code=ADMIN_ROLE_NOT_FOUND,
        )
    _record_admin_outcome(
        request,
        event_name="admin_role_tools_get_succeeded",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "user_id": str(admin_user.user_id),
            "role": name,
            "tool_count": len(tools),
        },
    )
    return RoleToolsResponse(tools=tools)


@router.put("/roles/{name}/tools", response_model=RoleToolsResponse)
async def set_role_tools(
    request: Request,
    name: str,
    payload: SetRoleToolsRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> RoleToolsResponse:
    with log_context(user_id=str(admin_user.user_id), role=name):
        try:
            tools = await authorization_service.set_role_tools(
                name, payload.tools, actor_email=admin_user.email
            )
        except InvalidRoleNameError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_name_invalid",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=INVALID_ROLE_NAME,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=INVALID_ROLE_NAME,
            ) from exc
        except ReservedRoleError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_reserved",
                status_code=status.HTTP_403_FORBIDDEN,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=RESERVED_ROLE,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
                error_code=RESERVED_ROLE,
            ) from exc
        except UnknownToolError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_unknown_tools",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id), "role": name},
                error_code=UNKNOWN_TOOLS,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=UNKNOWN_TOOLS,
            ) from exc

    if tools is None:
        _record_admin_outcome(
            request,
            event_name="admin_role_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
            trace_attributes={"user_id": str(admin_user.user_id), "role": name},
            error_code=ADMIN_ROLE_NOT_FOUND,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
            error_code=ADMIN_ROLE_NOT_FOUND,
        )
    _record_admin_outcome(
        request,
        event_name="admin_role_tools_updated",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "user_id": str(admin_user.user_id),
            "role": name,
            "tool_count": len(tools),
        },
    )
    return RoleToolsResponse(tools=tools)


@router.put("/users/{id}/roles", response_model=UpdateUserResponse)
async def set_user_roles(
    request: Request,
    id: UUID,
    payload: SetUserRolesRequest,
    admin_user: AuthorizationUser = Depends(_require_admin),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> UpdateUserResponse:
    with log_context(target_user_id=str(id), user_id=str(admin_user.user_id)):
        try:
            user = await authorization_service.set_user_roles(
                id,
                payload.roles,
                actor_email=admin_user.email,
                actor_user_id=admin_user.user_id,
            )
        except InvalidRoleNameError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_role_name_invalid",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=INVALID_ROLE_NAME,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=INVALID_ROLE_NAME,
            ) from exc
        except InternalRoleError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_internal_role_forbidden",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=INTERNAL_ROLE_FORBIDDEN,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=INTERNAL_ROLE_FORBIDDEN,
            ) from exc
        except UnknownRoleError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_unknown_roles",
                status_code=status.HTTP_400_BAD_REQUEST,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=UNKNOWN_ROLES,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                error_code=UNKNOWN_ROLES,
            ) from exc
        except SelfRemoveAdminRoleError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_self_remove_admin_role_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={"user_id": str(admin_user.user_id)},
                error_code=SELF_REMOVE_ADMIN_ROLE,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=SELF_REMOVE_ADMIN_ROLE,
            ) from exc
        except LastActiveAdminError as exc:
            _record_admin_outcome(
                request,
                event_name="admin_last_active_admin_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=LAST_ACTIVE_ADMIN,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=LAST_ACTIVE_ADMIN,
            ) from exc

        if user is None:
            _record_admin_outcome(
                request,
                event_name="admin_user_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=ADMIN_USER_NOT_FOUND,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
                error_code=ADMIN_USER_NOT_FOUND,
            )

    _record_admin_outcome(
        request,
        event_name="admin_user_roles_updated",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "target_user_id": str(id),
            "user_id": str(admin_user.user_id),
            "role_count": len(user.roles),
        },
    )
    return UpdateUserResponse(user=_to_user_response(user))
