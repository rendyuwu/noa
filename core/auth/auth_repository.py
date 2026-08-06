"""Persistence behind the login flow (T8).

Ported from `noa-old` branch `MCP` (`core/auth/auth_service.py`'s
`SQLAuthRepository`, `core/auth/role_repository.py`, and
`storage/postgres/login_rate_limits.py`, C13), consolidated here because all three
share one session and commit as one unit of work: a failed login must not leave a
provisioned user behind without its rate-limit counter, and vice versa.

Both classes are addressed through Protocols (`AuthRepository`,
`LoginRateLimitRepository`) so `AuthService` and `LoginRateLimiter` are exercised by
route tests with no Postgres in the loop, while these implementations are covered
against a live scratch database.

`commit()` on the repository is deliberate. `noa-old` put the transaction boundary in
a FastAPI dependency, which then had to inspect the *exception type* to decide
whether to commit — a first login raises pending-approval (V7) yet its user row must
persist. Giving the service an explicit commit lets it write that rule where the rule
lives, and a test double counts the calls.

Role helpers live here rather than in a separate mixin because T9's RBAC engine reads
the same three operations, and duplicating them is exactly what V66 forbids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.login_rate_limiter import LoginRateLimitBucket
from core.db.models import LoginRateLimit, Role, User, UserRole


class AuthUserRecord(Protocol):
    """The slice of `users` the login flow reads and writes."""

    id: UUID
    email: str
    ldap_dn: str | None
    display_name: str | None
    is_active: bool
    last_login_at: datetime | None


class AuthRepository(Protocol):
    """User + role persistence for `AuthService`."""

    async def get_user_by_email(self, email: str) -> AuthUserRecord | None: ...

    async def get_user_by_id(self, user_id: UUID) -> AuthUserRecord | None: ...

    async def create_user(
        self,
        *,
        email: str,
        ldap_dn: str | None,
        display_name: str | None,
        is_active: bool,
    ) -> AuthUserRecord: ...

    async def update_user(
        self,
        user: AuthUserRecord,
        *,
        ldap_dn: str | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
        last_login_at: datetime | None = None,
    ) -> AuthUserRecord: ...

    async def ensure_role(self, name: str) -> str: ...

    async def assign_role(self, user_id: UUID, role_name: str) -> None: ...

    async def get_role_names(self, user_id: UUID) -> list[str]: ...

    async def commit(self) -> None: ...


class SQLAuthRepository:
    """`AuthRepository` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Users ---

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        """Primary-key read for the per-request `is_active` re-check (V6).

        By id, not email: the session cookie's `uid` claim is stable, while an LDAP
        address change would either 401 a live session or — if an address were ever
        reassigned — resolve to a different operator's row. `noa-old` looked up by the
        `sub` email; this does not.
        """
        return await self._session.get(User, user_id)

    async def create_user(
        self,
        *,
        email: str,
        ldap_dn: str | None,
        display_name: str | None,
        is_active: bool,
    ) -> User:
        user = User(
            email=email,
            ldap_dn=ldap_dn,
            display_name=display_name,
            is_active=is_active,
        )
        self._session.add(user)
        # Flush, not commit: `user.id` is server-generated and the caller needs it for
        # role assignment inside the same transaction.
        await self._session.flush()
        return user

    async def update_user(
        self,
        user: User,
        *,
        ldap_dn: str | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
        last_login_at: datetime | None = None,
    ) -> User:
        """Patch the fields passed; `None` means "leave alone", not "set NULL".

        Consequence worth knowing: an attribute that disappears from the directory
        cannot be cleared through here. Login only ever has fresher values to write,
        so a sentinel for "clear this" would be unused ceremony today; whoever needs
        it adds it with a caller.
        """
        if ldap_dn is not None:
            user.ldap_dn = ldap_dn
        if display_name is not None:
            user.display_name = display_name
        if is_active is not None:
            user.is_active = is_active
        if last_login_at is not None:
            user.last_login_at = last_login_at
        await self._session.flush()
        return user

    # --- Roles (T9 reads these too, V66) ---

    async def ensure_role(self, name: str) -> str:
        """Create the role if absent; return its name."""
        result = await self._session.execute(select(Role).where(Role.name == name))
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(name=name)
            self._session.add(role)
            await self._session.flush()
        return role.name

    async def assign_role(self, user_id: UUID, role_name: str) -> None:
        """Idempotent assignment. Unknown role name is a no-op, not an error."""
        result = await self._session.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if role is None:
            return

        existing = await self._session.execute(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id)
        )
        if existing.scalar_one_or_none() is None:
            self._session.add(UserRole(user_id=user_id, role_id=role.id))
            await self._session.flush()

    async def get_role_names(self, user_id: UUID) -> list[str]:
        """Role names, deduplicated and sorted so responses are stable."""
        result = await self._session.execute(
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def commit(self) -> None:
        await self._session.commit()


class SQLLoginRateLimitRepository:
    """`LoginRateLimitRepository` over one `AsyncSession` (V9)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_bucket(self, scope: str, scope_key: str) -> LoginRateLimitBucket | None:
        result = await self._session.execute(
            select(LoginRateLimit).where(
                LoginRateLimit.scope == scope,
                LoginRateLimit.scope_key == scope_key,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return LoginRateLimitBucket(
            attempt_count=record.attempt_count,
            window_started_at=record.window_started_at,
            blocked_until=record.blocked_until,
        )

    async def upsert_bucket(
        self,
        scope: str,
        scope_key: str,
        *,
        attempt_count: int,
        window_started_at: datetime,
        blocked_until: datetime | None,
    ) -> LoginRateLimitBucket:
        """`INSERT ... ON CONFLICT DO UPDATE`, so concurrent attempts cannot both
        insert the same bucket and trip the unique constraint.

        `RETURNING` gives back what the row now holds rather than what was sent, so a
        caller reads the committed state instead of its own optimistic guess.
        """
        statement = insert(LoginRateLimit).values(
            scope=scope,
            scope_key=scope_key,
            attempt_count=attempt_count,
            window_started_at=window_started_at,
            blocked_until=blocked_until,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_login_rate_limits_scope_key",
            set_={
                "attempt_count": statement.excluded.attempt_count,
                "window_started_at": statement.excluded.window_started_at,
                "blocked_until": statement.excluded.blocked_until,
            },
        ).returning(
            LoginRateLimit.attempt_count,
            LoginRateLimit.window_started_at,
            LoginRateLimit.blocked_until,
        )

        row = (await self._session.execute(statement)).one()
        return LoginRateLimitBucket(
            attempt_count=row.attempt_count,
            window_started_at=row.window_started_at,
            blocked_until=row.blocked_until,
        )

    async def clear_bucket(self, scope: str, scope_key: str) -> None:
        await self._session.execute(
            delete(LoginRateLimit).where(
                LoginRateLimit.scope == scope,
                LoginRateLimit.scope_key == scope_key,
            )
        )


__all__ = [
    "AuthRepository",
    "AuthUserRecord",
    "SQLAuthRepository",
    "SQLLoginRateLimitRepository",
]
