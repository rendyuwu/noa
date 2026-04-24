import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from noa_api.api.error_codes import (
    ADMIN_USER_NOT_FOUND,
    DIRECT_TOOL_GRANTS_DISABLED,
    INTERNAL_ROLE_FORBIDDEN,
    INVALID_ROLE_NAME,
    LAST_ACTIVE_ADMIN,
    SELF_DEACTIVATE_ADMIN,
    SELF_DELETE,
    SELF_DELETE_ADMIN,
    SELF_REMOVE_ADMIN_ROLE,
    UNKNOWN_ROLES,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    InternalRoleError,
    InvalidRoleNameError,
    LastActiveAdminError,
    SelfDeactivateAdminError,
    SelfDeleteAdminError,
    SelfDeleteError,
    SelfRemoveAdminRoleError,
    UnknownRoleError,
    get_authorization_service,
)
from noa_api.core.logging_context import log_context

from .guards import _record_admin_outcome, _require_admin
from .schemas import (
    AdminToolsResponse,
    AdminUsersResponse,
    DeleteUserResponse,
    DirectGrantsMigrationResponse,
    SetUserRolesRequest,
    SetUserToolsRequest,
    UpdateUserRequest,
    UpdateUserResponse,
    _to_user_response,
)

user_router = APIRouter()

logger = logging.getLogger(__name__)


@user_router.get("/users", response_model=AdminUsersResponse)
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


@user_router.patch("/users/{id}", response_model=UpdateUserResponse)
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


@user_router.delete("/users/{id}", response_model=DeleteUserResponse)
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
        except SelfDeleteError as exc:
            logger.info("self_delete_conflict")
            _record_admin_outcome(
                request,
                event_name="self_delete_conflict",
                status_code=status.HTTP_409_CONFLICT,
                trace_attributes={
                    "target_user_id": str(id),
                    "user_id": str(admin_user.user_id),
                },
                error_code=SELF_DELETE,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                error_code=SELF_DELETE,
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


@user_router.get("/tools", response_model=AdminToolsResponse)
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


@user_router.put("/users/{id}/tools", response_model=UpdateUserResponse)
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


@user_router.post(
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


@user_router.put("/users/{id}/roles", response_model=UpdateUserResponse)
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
