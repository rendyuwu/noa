import logging

from fastapi import APIRouter, Depends, Request, status

from noa_api.api.error_codes import (
    ADMIN_ROLE_NOT_FOUND,
    INVALID_ROLE_NAME,
    RESERVED_ROLE,
    UNKNOWN_TOOLS,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    InvalidRoleNameError,
    ReservedRoleError,
    UnknownToolError,
    get_authorization_service,
)
from noa_api.core.logging_context import log_context

from .guards import _record_admin_outcome, _require_admin
from .schemas import (
    AdminRoleResponse,
    AdminRolesResponse,
    CreateRoleRequest,
    DeleteRoleResponse,
    RoleToolsResponse,
    SetRoleToolsRequest,
)

role_router = APIRouter()

logger = logging.getLogger(__name__)


@role_router.get("/roles", response_model=AdminRolesResponse)
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


@role_router.post("/roles", response_model=AdminRoleResponse)
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


@role_router.delete("/roles/{name}", response_model=DeleteRoleResponse)
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


@role_router.get("/roles/{name}/tools", response_model=RoleToolsResponse)
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


@role_router.put("/roles/{name}/tools", response_model=RoleToolsResponse)
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
