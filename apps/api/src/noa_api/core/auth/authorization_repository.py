from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.models import (
    AuditLog,
    Role,
    RoleToolPermission,
    User,
    UserRole,
)


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

        await self.remove_user_allowlist_role(user_id)

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

    async def remove_user_allowlist_role(self, user_id: UUID) -> bool:
        role_name = f"user:{user_id}"
        result = await self._session.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if role is None:
            return False
        await self._session.delete(role)
        await self._session.flush()
        return True

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
