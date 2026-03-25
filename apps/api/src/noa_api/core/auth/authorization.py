from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tools.catalog import get_tool_catalog
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.models import (
    AuditLog,
    Role,
    RoleToolPermission,
    User,
    UserRole,
)


class UnknownToolError(Exception):
    def __init__(self, unknown_tools: list[str]) -> None:
        self.unknown_tools = sorted(
            {name.strip() for name in unknown_tools if name.strip()}
        )
        super().__init__(f"Unknown tools: {', '.join(self.unknown_tools)}")


class LastActiveAdminError(Exception):
    pass


class SelfDeactivateAdminError(Exception):
    pass


class SelfDeleteAdminError(Exception):
    pass


class InvalidRoleNameError(Exception):
    pass


class ReservedRoleError(Exception):
    pass


class InternalRoleError(Exception):
    pass


class RoleNotFoundError(Exception):
    pass


class UnknownRoleError(Exception):
    def __init__(self, unknown_roles: list[str]) -> None:
        self.unknown_roles = sorted(
            {name.strip() for name in unknown_roles if name.strip()}
        )
        super().__init__(f"Unknown roles: {', '.join(self.unknown_roles)}")


class SelfRemoveAdminRoleError(Exception):
    pass


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

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None: ...


class SQLAuthorizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_role_tool_names(self, role_names: list[str]) -> list[str]:
        if not role_names:
            return []
        result = await self._session.execute(
            select(RoleToolPermission.tool_name)
            .join(Role, Role.id == RoleToolPermission.role_id)
            .where(Role.name.in_(role_names))
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def list_manageable_role_names(self) -> list[str]:
        result = await self._session.execute(
            select(Role.name).where(~Role.name.like("user:%")).order_by(Role.name.asc())
        )
        return [str(name) for name in result.scalars().all()]

    async def role_exists(self, role_name: str) -> bool:
        result = await self._session.execute(
            select(Role.id).where(Role.name == role_name)
        )
        return result.scalar_one_or_none() is not None

    async def create_role(self, role_name: str) -> str:
        return await self.ensure_role(role_name)

    async def delete_role(self, role_name: str) -> bool:
        result = await self._session.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if role is None:
            return False
        await self._session.delete(role)
        await self._session.flush()
        return True

    async def list_existing_role_names(self, role_names: list[str]) -> list[str]:
        normalized = [name.strip() for name in role_names if name.strip()]
        if not normalized:
            return []
        result = await self._session.execute(
            select(Role.name).where(Role.name.in_(normalized))
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def get_role_tool_names_for_role(self, role_name: str) -> list[str]:
        result = await self._session.execute(
            select(RoleToolPermission.tool_name)
            .join(Role, Role.id == RoleToolPermission.role_id)
            .where(Role.name == role_name)
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def replace_user_non_internal_roles(
        self, user_id: UUID, role_names: list[str]
    ) -> None:
        normalized = sorted({name.strip() for name in role_names if name.strip()})

        # Delete all non-internal roles (keep roles starting with user:).
        await self._session.execute(
            delete(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id.in_(select(Role.id).where(~Role.name.like("user:%"))),
            )
        )

        if not normalized:
            await self._session.flush()
            return

        roles_result = await self._session.execute(
            select(Role.id, Role.name).where(Role.name.in_(normalized))
        )
        roles_by_name = {str(name): role_id for role_id, name in roles_result.all()}

        existing_result = await self._session.execute(
            select(UserRole.role_id).where(UserRole.user_id == user_id)
        )
        existing_role_ids = {role_id for role_id in existing_result.scalars().all()}

        for role_name in normalized:
            role_id = roles_by_name.get(role_name)
            if role_id is None:
                continue
            if role_id in existing_role_ids:
                continue
            self._session.add(UserRole(user_id=user_id, role_id=role_id))
        await self._session.flush()

    async def list_users(self) -> list[User]:
        result = await self._session.execute(select(User).order_by(User.email.asc()))
        return list(result.scalars().all())

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def update_user_active(
        self, user_id: UUID, *, is_active: bool
    ) -> User | None:
        user = await self.get_user_by_id(user_id)
        if user is None:
            return None
        user.is_active = is_active
        await self._session.flush()
        return user

    async def count_active_admin_users(self) -> int:
        result = await self._session.execute(
            select(func.count(func.distinct(User.id)))
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active.is_(True), Role.name == "admin")
        )
        return int(result.scalar_one() or 0)

    async def delete_user(self, user_id: UUID) -> User | None:
        user = await self.get_user_by_id(user_id)
        if user is None:
            return None

        allowlist_role_name = f"user:{user_id}"
        role_result = await self._session.execute(
            select(Role).where(Role.name == allowlist_role_name)
        )
        allowlist_role = role_result.scalar_one_or_none()
        if allowlist_role is not None:
            await self._session.execute(
                delete(RoleToolPermission).where(
                    RoleToolPermission.role_id == allowlist_role.id
                )
            )
            await self._session.execute(
                delete(UserRole).where(UserRole.role_id == allowlist_role.id)
            )
            await self._session.execute(
                delete(Role).where(Role.id == allowlist_role.id)
            )

        await self._session.delete(user)
        await self._session.flush()
        return user

    async def get_role_names(self, user_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def ensure_role(self, name: str) -> str:
        result = await self._session.execute(select(Role).where(Role.name == name))
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(name=name)
            self._session.add(role)
            await self._session.flush()
        return role.name

    async def assign_role(self, user_id: UUID, role_name: str) -> None:
        role_result = await self._session.execute(
            select(Role).where(Role.name == role_name)
        )
        role = role_result.scalar_one_or_none()
        if role is None:
            return

        existing = await self._session.execute(
            select(UserRole).where(
                UserRole.user_id == user_id, UserRole.role_id == role.id
            )
        )
        if existing.scalar_one_or_none() is None:
            self._session.add(UserRole(user_id=user_id, role_id=role.id))
            await self._session.flush()

    async def replace_role_tool_permissions(
        self, role_name: str, tool_names: list[str]
    ) -> None:
        role_result = await self._session.execute(
            select(Role).where(Role.name == role_name)
        )
        role = role_result.scalar_one_or_none()
        if role is None:
            return

        await self._session.execute(
            delete(RoleToolPermission).where(RoleToolPermission.role_id == role.id)
        )
        for tool_name in sorted({name.strip() for name in tool_names if name.strip()}):
            self._session.add(RoleToolPermission(role_id=role.id, tool_name=tool_name))
        await self._session.flush()

    async def get_user_allowlist_tools(self, user_id: UUID) -> list[str]:
        role_name = f"user:{user_id}"
        result = await self._session.execute(
            select(RoleToolPermission.tool_name)
            .join(Role, Role.id == RoleToolPermission.role_id)
            .where(Role.name == role_name)
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            AuditLog(
                event_type=event_type,
                user_email=actor_email,
                tool_name=tool_name,
                meta_data=metadata,
            )
        )
        await self._session.flush()


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

    async def set_user_tools(
        self,
        user_id: UUID,
        tool_names: list[str],
        *,
        actor_email: str | None = None,
    ) -> AuthorizationUser | None:
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            return None

        normalized = sorted({name.strip() for name in tool_names if name.strip()})
        unknown = [name for name in normalized if name not in self._known_tools]
        if unknown:
            raise UnknownToolError(unknown)

        role_name = f"user:{user_id}"
        await self._repository.ensure_role(role_name)
        await self._repository.assign_role(user_id, role_name)
        await self._repository.replace_role_tool_permissions(role_name, normalized)

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
            event_type="admin_user_tools_updated",
            actor_email=actor_email,
            tool_name=None,
            metadata={"target_user_id": str(user.id), "tools": direct_tools},
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
