"""SQL behind the RBAC engine (T9, V10, V11, V13).

Ported from `noa-old` branch `MCP` (`core/auth/authorization_repository.py` +
`role_repository.py`, C13). Two departures:

1. **No direct-grant methods.** `get_user_allowlist_tools` and
   `remove_user_allowlist_role` read and wrote the `user:<uuid>` pseudo-role that carried
   per-user grants. V75 disables direct grants (410, T65), so those paths are gone. What
   survives is the *preservation* rule: `replace_user_assignable_roles` never touches a
   `user:`-prefixed assignment, because internal roles are NOA's own bookkeeping and an
   admin replacing a user's roles must not clear them (V13, V75).
2. **The three shared role operations are delegated, not copied.** `ensure_role` and
   `get_role_names` already exist on `SQLAuthRepository` (T8 put them there precisely so
   T9 could read them, V66). `noa-old` used a mixin; composition keeps T8's class
   untouched and reads the same.

Every method takes the caller's `AsyncSession` and flushes rather than commits. The
transaction boundary belongs to the request (see `noa_api.api.deps`), so a role rename and
its audit event land together or not at all.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.auth_repository import SQLAuthRepository
from core.db.models import (
    ADMIN_ROLE_NAME,
    INTERNAL_ROLE_PREFIX,
    McpToken,
    Role,
    RoleToolPermission,
    User,
    UserRole,
)

# `LIKE 'user:%'` — the pattern for "is an internal role" (V13, V75).
_INTERNAL_ROLE_PATTERN = f"{INTERNAL_ROLE_PREFIX}%"


class SQLAuthorizationRepository:
    """`AuthorizationRepository` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Composition, not duplication (V66): T8 owns these three queries.
        self._auth_repository = SQLAuthRepository(session)

    # --- Reads on the permission path ---

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        """The `users` row, read for this call.

        V6 and V11 both rest on this: `is_active` and role membership are read from the
        database on every permission question, so disabling an operator takes effect on
        their live session's next request rather than at cookie expiry.
        """
        return await self._auth_repository.get_user_by_id(user_id)

    async def get_role_names(self, user_id: UUID) -> list[str]:
        """Role names held by `user_id`, sorted and deduplicated."""
        return await self._auth_repository.get_role_names(user_id)

    async def get_role_tool_names(self, role_names: list[str]) -> list[str]:
        """Union of tool grants across `role_names`.

        Empty input short-circuits: `IN ()` is not valid SQL everywhere and SQLAlchemy
        would emit a warning-worthy always-false clause for a question with a known
        answer.
        """
        if not role_names:
            return []

        result = await self._session.execute(
            select(RoleToolPermission.tool_name)
            .join(Role, Role.id == RoleToolPermission.role_id)
            .where(Role.name.in_(role_names))
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def list_users(self) -> list[User]:
        """Every user, ordered by email so the admin list is stable across requests."""
        result = await self._session.execute(select(User).order_by(User.email.asc()))
        return list(result.scalars().all())

    # --- Roles and grants ---

    async def list_assignable_role_names(self) -> list[str]:
        """Roles an admin may assign: everything except internal `user:` roles (V13)."""
        result = await self._session.execute(
            select(Role.name)
            .where(~Role.name.like(_INTERNAL_ROLE_PATTERN))
            .order_by(Role.name.asc())
        )
        return [str(name) for name in result.scalars().all()]

    async def role_exists(self, role_name: str) -> bool:
        result = await self._session.execute(select(Role.id).where(Role.name == role_name))
        return result.scalar_one_or_none() is not None

    async def ensure_role(self, role_name: str) -> str:
        """Create the role if absent; return its name."""
        return await self._auth_repository.ensure_role(role_name)

    async def delete_role(self, role_name: str) -> bool:
        """Delete the role. False when it did not exist.

        Its grants and assignments go with it through the `ON DELETE CASCADE` on
        `role_tool_permissions.role_id` and `user_roles.role_id` (T4), so no orphan grant
        can later resolve for a role nobody holds.
        """
        result = await self._session.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if role is None:
            return False

        await self._session.delete(role)
        await self._session.flush()
        return True

    async def list_existing_role_names(self, role_names: list[str]) -> list[str]:
        """Which of `role_names` exist. Lets the caller name the missing ones."""
        normalized = [name.strip() for name in role_names if name.strip()]
        if not normalized:
            return []

        result = await self._session.execute(select(Role.name).where(Role.name.in_(normalized)))
        return sorted({str(name) for name in result.scalars().all()})

    async def get_role_tool_names_for_role(self, role_name: str) -> list[str]:
        """Tool grants held by one role, sorted."""
        result = await self._session.execute(
            select(RoleToolPermission.tool_name)
            .join(Role, Role.id == RoleToolPermission.role_id)
            .where(Role.name == role_name)
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def replace_role_tool_permissions(self, role_name: str, tool_names: list[str]) -> None:
        """Set the role's grants to exactly `tool_names`.

        Replace rather than diff: the admin UI sends the full desired set, and a diff
        would have to guess whether an absent name means "leave it" or "revoke it".
        Missing role is a no-op here — `AuthorizationService` has already raised
        `RoleNotFoundError`, and duplicating that check would put the 404 decision in two
        places.
        """
        role_result = await self._session.execute(select(Role).where(Role.name == role_name))
        role = role_result.scalar_one_or_none()
        if role is None:
            return

        await self._session.execute(
            delete(RoleToolPermission).where(RoleToolPermission.role_id == role.id)
        )
        for tool_name in sorted({name.strip() for name in tool_names if name.strip()}):
            self._session.add(RoleToolPermission(role_id=role.id, tool_name=tool_name))
        await self._session.flush()

    async def replace_user_assignable_roles(self, user_id: UUID, role_names: list[str]) -> None:
        """Set the user's assignable roles to exactly `role_names`, keeping internal ones.

        The delete is scoped by a subquery on the role table rather than by the names
        passed in, so a `user:` assignment survives replacement even when the caller sends
        an empty list (V13, V75).

        Names that do not resolve to a role are skipped, not inserted as dangling
        assignments; `AuthorizationService` rejects them up front with `UnknownRoleError`,
        so reaching that branch means the role was deleted mid-request.
        """
        normalized = sorted({name.strip() for name in role_names if name.strip()})

        await self._session.execute(
            delete(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id.in_(
                    select(Role.id).where(~Role.name.like(_INTERNAL_ROLE_PATTERN))
                ),
            )
        )

        if not normalized:
            await self._session.flush()
            return

        roles_result = await self._session.execute(
            select(Role.id, Role.name).where(Role.name.in_(normalized))
        )
        role_ids_by_name = {str(name): role_id for role_id, name in roles_result.all()}

        existing_result = await self._session.execute(
            select(UserRole.role_id).where(UserRole.user_id == user_id)
        )
        already_assigned = set(existing_result.scalars().all())

        for role_name in normalized:
            role_id = role_ids_by_name.get(role_name)
            if role_id is None or role_id in already_assigned:
                continue
            self._session.add(UserRole(user_id=user_id, role_id=role_id))
        await self._session.flush()

    # --- User administration ---

    async def update_user_active(self, user_id: UUID, *, is_active: bool) -> User | None:
        """Flip `users.is_active`. None when the row is gone."""
        user = await self.get_user_by_id(user_id)
        if user is None:
            return None

        user.is_active = is_active
        await self._session.flush()
        return user

    async def count_active_admin_users(self) -> int:
        """How many active users hold `admin` (V12).

        `DISTINCT` on the user id: the join multiplies rows per matching role, and without
        it a user holding `admin` twice — impossible today, cheap to be wrong about
        tomorrow — would count as two and defeat the last-admin guard.
        """
        result = await self._session.execute(
            select(func.count(func.distinct(User.id)))
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active.is_(True), Role.name == ADMIN_ROLE_NAME)
        )
        return int(result.scalar_one() or 0)

    async def delete_user(self, user_id: UUID) -> bool:
        """Delete the user. False when the row was already gone.

        Role assignments and MCP tokens cascade from the foreign keys (T4). The session
        cookie does not: V6 records that a session JWT cannot be revoked before `exp`,
        which is why `AuthService.resolve_session_user` treats a missing row as
        `session_invalid` on the next request.
        """
        user = await self.get_user_by_id(user_id)
        if user is None:
            return False

        await self._session.delete(user)
        await self._session.flush()
        return True

    async def delete_mcp_tokens_for_user(self, user_id: UUID) -> int:
        """Revoke every MCP token this operator holds; return how many (V4, T11).

        Called when an admin disables the account. `is_active=False` already zeroes their
        permissions (V11) and the verify path already refuses them (V1), so this is not
        what stops them acting — it is what makes the credential itself dead, so a token
        pasted into a LibreChat config cannot come back to life the day someone re-enables
        the row for an unrelated reason.

        A plain `DELETE`, matching T10: revocation is the row's absence (V2). Flushed, not
        committed — the disable, this revoke and the audit event share the request's
        transaction, so a failure part-way leaves none of the three.
        """
        result = await self._session.execute(delete(McpToken).where(McpToken.user_id == user_id))
        await self._session.flush()
        return int(result.rowcount or 0)


__all__ = ["SQLAuthorizationRepository"]
