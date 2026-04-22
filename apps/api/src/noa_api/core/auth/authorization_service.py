from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncGenerator
from uuid import UUID

from noa_api.core.auth.authorization_errors import (
    InternalRoleError,
    InvalidRoleNameError,
    LastActiveAdminError,
    ReservedRoleError,
    SelfDeactivateAdminError,
    SelfDeleteAdminError,
    SelfRemoveAdminRoleError,
    UnknownRoleError,
    UnknownToolError,
)
from noa_api.core.auth.authorization_repository import SQLAuthorizationRepository
from noa_api.core.auth.authorization_types import (
    AuthorizationRepositoryProtocol,
    AuthorizationUser,
    DirectGrantsMigrationSummary,
)
from noa_api.core.tools.catalog import get_tool_catalog
from noa_api.storage.postgres.client import get_session_factory


class AuthorizationService:
    def __init__(self, *, repository: AuthorizationRepositoryProtocol) -> None:
        self._repository = repository
        self._known_tools = set(get_tool_catalog())

    @staticmethod
    def _validate_role_name(role_name: str) -> str:
        normalized = role_name.strip()
        if not normalized:
            raise InvalidRoleNameError("Role name cannot be empty")
        if len(normalized) > 100:
            raise InvalidRoleNameError("Role name is too long")
        if normalized.startswith("user:"):
            raise InvalidRoleNameError("Role name cannot start with user:")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
            raise InvalidRoleNameError("Role name contains invalid characters")
        return normalized

    async def get_allowed_tool_names(self, user: AuthorizationUser) -> set[str]:
        if not user.is_active:
            return set()
        if "admin" in user.roles:
            return set(self._known_tools)
        role_tools = await self._repository.get_role_tool_names(user.roles)
        return {name for name in role_tools if name in self._known_tools}

    async def authorize_tool_access(
        self, user: AuthorizationUser, tool_name: str
    ) -> bool:
        if not user.is_active:
            return False
        if "admin" in user.roles:
            return tool_name in self._known_tools
        role_tools = await self._repository.get_role_tool_names(user.roles)
        return tool_name in self._known_tools and tool_name in role_tools

    async def list_users(self) -> list[AuthorizationUser]:
        users = await self._repository.list_users()
        result: list[AuthorizationUser] = []
        for user in users:
            roles = await self._repository.get_role_names(user.id)
            direct_tools = await self._repository.get_user_allowlist_tools(user.id)
            effective_tools = sorted(
                await self.get_allowed_tool_names(
                    AuthorizationUser(
                        user_id=user.id,
                        email=user.email,
                        display_name=user.display_name,
                        is_active=user.is_active,
                        roles=roles,
                        tools=[],
                        direct_tools=[],
                    )
                )
            )
            result.append(
                AuthorizationUser(
                    user_id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    is_active=user.is_active,
                    roles=roles,
                    tools=effective_tools,
                    direct_tools=direct_tools,
                    created_at=user.created_at,
                    last_login_at=user.last_login_at,
                )
            )
        return result

    async def set_user_active(
        self,
        user_id: UUID,
        *,
        is_active: bool,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizationUser | None:
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            return None

        roles = await self._repository.get_role_names(user.id)
        is_admin_user = "admin" in roles
        if user.is_active and not is_active and is_admin_user:
            if actor_user_id is not None and actor_user_id == user_id:
                raise SelfDeactivateAdminError(
                    "Admins cannot disable their own account"
                )
            if await self._repository.count_active_admin_users() <= 1:
                raise LastActiveAdminError("Cannot disable the last active admin")

        user = await self._repository.update_user_active(user_id, is_active=is_active)
        if user is None:
            return None

        direct_tools = await self._repository.get_user_allowlist_tools(user.id)
        effective_tools = sorted(
            await self.get_allowed_tool_names(
                AuthorizationUser(
                    user_id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    is_active=user.is_active,
                    roles=roles,
                    tools=[],
                    direct_tools=[],
                )
            )
        )
        await self._repository.create_audit_log(
            event_type="admin_user_status_updated",
            actor_email=actor_email,
            tool_name=None,
            metadata={"target_user_id": str(user.id), "is_active": is_active},
        )
        return AuthorizationUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=roles,
            tools=effective_tools,
            direct_tools=direct_tools,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    async def list_tools(self) -> list[str]:
        return list(get_tool_catalog())

    async def delete_user(
        self,
        user_id: UUID,
        *,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizationUser | None:
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            return None

        roles = await self._repository.get_role_names(user.id)
        direct_tools = await self._repository.get_user_allowlist_tools(user.id)
        effective_tools = sorted(
            await self.get_allowed_tool_names(
                AuthorizationUser(
                    user_id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    is_active=user.is_active,
                    roles=roles,
                    tools=[],
                    direct_tools=[],
                )
            )
        )
        is_admin_user = "admin" in roles
        if actor_user_id is not None and actor_user_id == user_id:
            raise SelfDeleteAdminError("Admins cannot delete their own account")
        if user.is_active and is_admin_user:
            if await self._repository.count_active_admin_users() <= 1:
                raise LastActiveAdminError("Cannot delete the last active admin")

        deleted_user = await self._repository.delete_user(user_id)
        if deleted_user is None:
            return None

        await self._repository.create_audit_log(
            event_type="admin_user_deleted",
            actor_email=actor_email,
            tool_name=None,
            metadata={"target_user_id": str(user.id), "email": user.email},
        )
        return AuthorizationUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=roles,
            tools=effective_tools,
            direct_tools=direct_tools,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    async def migrate_legacy_direct_grants(
        self, *, actor_email: str | None = None
    ) -> DirectGrantsMigrationSummary:
        users = await self._repository.list_users()
        users_migrated = 0
        roles_created = 0
        roles_reused = 0
        internal_roles_deleted = 0
        tool_grant_count = 0
        created_roles: list[str] = []

        for user in users:
            direct_tools = await self._repository.get_user_allowlist_tools(user.id)
            if not direct_tools:
                continue

            joined = "\n".join(direct_tools)
            hash8 = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]
            role_name = self._validate_role_name(f"legacy_tools_{hash8}")

            if await self._repository.role_exists(role_name):
                roles_reused += 1
            else:
                roles_created += 1
                created_roles.append(role_name)

            await self._repository.ensure_role(role_name)
            await self._repository.replace_role_tool_permissions(
                role_name, direct_tools
            )
            await self._repository.assign_role(user.id, role_name)

            if await self._repository.remove_user_allowlist_role(user.id):
                internal_roles_deleted += 1

            users_migrated += 1
            tool_grant_count += len(direct_tools)

        summary: DirectGrantsMigrationSummary = {
            "users_migrated": users_migrated,
            "roles_created": roles_created,
            "roles_reused": roles_reused,
            "internal_roles_deleted": internal_roles_deleted,
            "tool_grant_count": tool_grant_count,
            "created_roles": created_roles,
        }
        await self._repository.create_audit_log(
            event_type="admin_migration_direct_grants_completed",
            actor_email=actor_email,
            tool_name=None,
            metadata=summary,
        )
        return summary

    async def list_roles(self) -> list[str]:
        return await self._repository.list_manageable_role_names()

    async def create_role(self, name: str, *, actor_email: str | None = None) -> str:
        role_name = self._validate_role_name(name)
        if role_name == "admin":
            raise ReservedRoleError("Role admin is reserved")
        created = await self._repository.create_role(role_name)
        await self._repository.create_audit_log(
            event_type="admin_role_created",
            actor_email=actor_email,
            tool_name=None,
            metadata={"role": created},
        )
        return created

    async def delete_role(self, name: str, *, actor_email: str | None = None) -> bool:
        role_name = self._validate_role_name(name)
        if role_name == "admin":
            raise ReservedRoleError("Role admin is reserved")
        deleted = await self._repository.delete_role(role_name)
        if deleted:
            await self._repository.create_audit_log(
                event_type="admin_role_deleted",
                actor_email=actor_email,
                tool_name=None,
                metadata={"role": role_name},
            )
        return deleted

    async def get_role_tools(self, name: str) -> list[str] | None:
        role_name = self._validate_role_name(name)
        if not await self._repository.role_exists(role_name):
            return None
        return await self._repository.get_role_tool_names_for_role(role_name)

    async def set_role_tools(
        self,
        name: str,
        tool_names: list[str],
        *,
        actor_email: str | None = None,
    ) -> list[str] | None:
        role_name = self._validate_role_name(name)
        if role_name == "admin":
            raise ReservedRoleError("Role admin is reserved")
        if not await self._repository.role_exists(role_name):
            return None

        normalized = sorted({name.strip() for name in tool_names if name.strip()})
        unknown = [name for name in normalized if name not in self._known_tools]
        if unknown:
            raise UnknownToolError(unknown)

        await self._repository.replace_role_tool_permissions(role_name, normalized)
        await self._repository.create_audit_log(
            event_type="admin_role_tools_updated",
            actor_email=actor_email,
            tool_name=None,
            metadata={"role": role_name, "tools": normalized},
        )
        return await self._repository.get_role_tool_names_for_role(role_name)

    async def set_user_roles(
        self,
        user_id: UUID,
        role_names: list[str],
        *,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizationUser | None:
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            return None

        normalized: list[str] = []
        for role_name in role_names:
            raw = role_name.strip()
            if raw.startswith("user:"):
                raise InternalRoleError("Cannot assign internal roles")
            candidate = self._validate_role_name(raw)
            normalized.append(candidate)
        normalized = sorted(dict.fromkeys(normalized))

        existing = await self._repository.list_existing_role_names(normalized)
        missing = sorted(set(normalized) - set(existing))
        if missing:
            raise UnknownRoleError(missing)

        current_roles = await self._repository.get_role_names(user.id)
        was_admin = "admin" in current_roles
        removing_admin = was_admin and "admin" not in normalized
        if removing_admin and actor_user_id is not None and actor_user_id == user_id:
            raise SelfRemoveAdminRoleError("Admins cannot remove their own admin role")
        if removing_admin and user.is_active:
            if await self._repository.count_active_admin_users() <= 1:
                raise LastActiveAdminError(
                    "Cannot remove admin from the last active admin"
                )

        await self._repository.replace_user_non_internal_roles(user.id, normalized)

        roles = await self._repository.get_role_names(user.id)
        direct_tools = await self._repository.get_user_allowlist_tools(user.id)
        effective_tools = sorted(
            await self.get_allowed_tool_names(
                AuthorizationUser(
                    user_id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    is_active=user.is_active,
                    roles=roles,
                    tools=[],
                    direct_tools=[],
                )
            )
        )
        await self._repository.create_audit_log(
            event_type="admin_user_roles_updated",
            actor_email=actor_email,
            tool_name=None,
            metadata={"target_user_id": str(user.id), "roles": roles},
        )
        return AuthorizationUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=roles,
            tools=effective_tools,
            direct_tools=direct_tools,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


async def get_authorization_service() -> AsyncGenerator[AuthorizationService, None]:
    async with get_session_factory()() as session:
        repository = SQLAuthorizationRepository(session)
        service = AuthorizationService(repository=repository)
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise
