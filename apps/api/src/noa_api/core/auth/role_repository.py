from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.models import Role, UserRole


class RoleRepositoryMixin:
    """Shared role operations for SQL repositories that hold an ``_session``."""

    _session: AsyncSession

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

    async def get_role_names(self, user_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        return sorted({str(name) for name in result.scalars().all()})
