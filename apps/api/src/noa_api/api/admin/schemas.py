from datetime import datetime

from pydantic import BaseModel, Field

from noa_api.core.auth.authorization import AuthorizationUser


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
