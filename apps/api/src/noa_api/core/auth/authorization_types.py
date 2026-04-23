from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, TypedDict
from uuid import UUID

from noa_api.storage.postgres.models import User


@dataclass
class AuthorizationUser:
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]
    tools: list[str]
    direct_tools: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class AuthorizationRepositoryProtocol(Protocol):
    async def get_role_tool_names(self, role_names: list[str]) -> list[str]: ...

    async def list_manageable_role_names(self) -> list[str]: ...

    async def role_exists(self, role_name: str) -> bool: ...

    async def create_role(self, role_name: str) -> str: ...

    async def delete_role(self, role_name: str) -> bool: ...

    async def list_existing_role_names(self, role_names: list[str]) -> list[str]: ...

    async def get_role_tool_names_for_role(self, role_name: str) -> list[str]: ...

    async def replace_user_non_internal_roles(
        self, user_id: UUID, role_names: list[str]
    ) -> None: ...

    async def list_users(self) -> list[User]: ...

    async def get_user_by_id(self, user_id: UUID) -> User | None: ...

    async def update_user_active(
        self, user_id: UUID, *, is_active: bool
    ) -> User | None: ...

    async def count_active_admin_users(self) -> int: ...

    async def delete_user(self, user_id: UUID) -> User | None: ...

    async def get_role_names(self, user_id: UUID) -> list[str]: ...

    async def ensure_role(self, name: str) -> str: ...

    async def assign_role(self, user_id: UUID, role_name: str) -> None: ...

    async def replace_role_tool_permissions(
        self, role_name: str, tool_names: list[str]
    ) -> None: ...

    async def get_user_allowlist_tools(self, user_id: UUID) -> list[str]: ...

    async def remove_user_allowlist_role(self, user_id: UUID) -> bool: ...

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None: ...


class DirectGrantsMigrationSummary(TypedDict):
    users_migrated: int
    roles_created: int
    roles_reused: int
    internal_roles_deleted: int
    tool_grant_count: int
    created_roles: list[str]
