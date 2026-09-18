"""SQL behind MCP token management and verification.

New work — `noa-old` has no `mcp_tokens` table to port from.

Two classes, one table. They are separate because their transaction discipline is opposite,
not because the SQL is unrelated:

- `SQLMcpTokenRepository` — the admin CRUD path. Like the authorization repository,
  every method takes the caller's `AsyncSession` and flushes
  rather than commits, so a mint and its audit event land together or not at all. `commit()`
  is the boundary itself: `noa_api.api.deps.get_db_session` never commits, so without
  it a mint would return a plaintext over a transaction that rolls back at teardown and the
  operator would hold a credential authenticating nothing — the commit-last rule, one table over.
- `SQLMcpIdentityRepository` — the MCP request path. It runs outside FastAPI's
  dependency graph, inside `verify_token`, where there is no request transaction to join.
  It therefore owns its session and exposes `commit()`, which `McpIdentityResolver` calls
  at the two points a write must become durable.

Keeping both here rather than in two modules is deliberate: every statement that
touches `mcp_tokens` is in one file, so a column added later cannot be handled on one path
and forgotten on the other.

Rows are converted to dataclasses here rather than returned as ORM objects, following
`SQLLoginRateLimitRepository` returning `LoginRateLimitBucket`. Two reasons, and the
second is the security one: the callers never hold an object carrying `token_hash`, and
the in-memory doubles in the tests cannot accidentally expose a field the SQL path would
have hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.mcp_token_service import McpTokenView
from core.db.models import McpToken, User


class SQLMcpTokenRepository:
    """`McpTokenRepository` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_exists(self, user_id: UUID) -> bool:
        """Whether the `users` row is there.

        Selecting the id rather than the row: the caller only needs the answer, and
        loading the whole user would put a second, staler copy of `is_active` in play
        during a mint that deliberately does not consult it.
        """
        result = await self._session.execute(select(User.id).where(User.id == user_id))
        return result.scalar_one_or_none() is not None

    async def insert(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        token_prefix: str,
        label: str | None,
        expires_at: datetime | None,
    ) -> McpTokenView:
        """Write one token row and return its view.

        Flushed, not committed, so `created_at` (a column default, never the caller's) and the
        server-generated id are readable in the same transaction the caller is building.

        No retry around the `uq_mcp_tokens_token_hash` collision: with 256 bits of entropy
        behind the digest, a duplicate means the CSPRNG is broken, and a retry loop would
        quietly paper over that.
        """
        record = McpToken(
            user_id=user_id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            label=label,
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return _to_view(record)

    async def list_for_user(self, user_id: UUID) -> list[McpTokenView]:
        """This user's tokens, newest first.

        Tie-broken on `id` because `created_at` is not declared unique, and an unstable order
        makes a paginated admin list drop or repeat a row. Two tokens minted in one request no
        longer collide the way they once did — the column default is the application clock, read
        once per row, so they differ by microseconds — which makes this tie-break
        belt-and-braces rather than load-bearing. It stays: "distinct to the microsecond" is a
        property of the clock's resolution, not a guarantee worth resting pagination on.
        """
        result = await self._session.execute(
            select(McpToken)
            .where(McpToken.user_id == user_id)
            .order_by(McpToken.created_at.desc(), McpToken.id.desc())
        )
        return [_to_view(record) for record in result.scalars().all()]

    async def delete_for_user(self, user_id: UUID, token_id: UUID) -> bool:
        """Delete one token owned by `user_id`. False when there was nothing to delete.

        Both ids are in the WHERE clause, so the ownership check cannot be skipped by a
        caller and a foreign id is indistinguishable from an absent one.
        """
        result = await self._session.execute(
            delete(McpToken).where(McpToken.id == token_id, McpToken.user_id == user_id)
        )
        await self._session.flush()
        return bool(result.rowcount)

    async def commit(self) -> None:
        """Make this request's token writes durable.

        Delegated to the session rather than to `SQLMcpIdentityRepository.commit` below, even
        though both classes wrap a session and both commit: they are handed *different*
        sessions on purpose — this one joins the request's transaction, that one owns the
        verify path's. Routing one through the other would tie two transactions that must stay
        independent.
        """
        await self._session.commit()


@dataclass(frozen=True)
class McpAuthenticationRecord:
    """One `mcp_tokens ⋈ users` row, as the verify path reads it.

    Frozen and hash-free: `McpIdentityResolver` decides from these fields and writes
    through the repository, so a mutable row it could edit in place would be a second,
    divergent source of truth for `is_active`.
    """

    token_id: UUID
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    librechat_user_id: str | None
    expires_at: datetime | None
    last_ldap_check_at: datetime | None


class SQLMcpIdentityRepository:
    """`McpIdentityRepository` over one `AsyncSession` owned by the verify path."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash(self, token_hash: str) -> McpAuthenticationRecord | None:
        """The token row joined to its user, in one statement.

        Joined rather than two lookups: `is_active` must describe the same instant the
        token was found, or a disable landing between the reads would authenticate against
        a stale row. `token_hash` is the unique lookup key, so this is an index hit
        and at most one row.
        """
        result = await self._session.execute(
            select(
                McpToken.id,
                McpToken.user_id,
                User.email,
                User.display_name,
                User.is_active,
                McpToken.librechat_user_id,
                McpToken.expires_at,
                McpToken.last_ldap_check_at,
            )
            .join(User, User.id == McpToken.user_id)
            .where(McpToken.token_hash == token_hash)
        )
        row = result.one_or_none()
        if row is None:
            return None

        return McpAuthenticationRecord(
            token_id=row.id,
            user_id=row.user_id,
            email=row.email,
            display_name=row.display_name,
            is_active=row.is_active,
            librechat_user_id=row.librechat_user_id,
            expires_at=row.expires_at,
            last_ldap_check_at=row.last_ldap_check_at,
        )

    async def bind_librechat_user(self, token_id: UUID, librechat_user_id: str) -> str:
        """Pin an unbound token to `librechat_user_id`; return what the row now holds.

        `WHERE … AND librechat_user_id IS NULL` makes this a compare-and-set, so two first
        calls racing cannot both bind. The loser updates nothing, and the re-read below
        hands back the winner's value — which the resolver then treats as an ordinary
        mismatch. A read-then-write in Python could not close that window.

        `RETURNING` gives the committed value rather than the one we sent, so the caller
        compares against reality instead of its own optimistic guess.
        """
        result = await self._session.execute(
            update(McpToken)
            .where(McpToken.id == token_id, McpToken.librechat_user_id.is_(None))
            .values(librechat_user_id=librechat_user_id)
            .returning(McpToken.librechat_user_id)
        )
        bound = result.scalar_one_or_none()
        if bound is not None:
            return str(bound)

        # Lost the race (or the row vanished mid-request). Re-read; a deleted row answers
        # with the empty string, which no presented header can equal after normalization,
        # so the caller refuses rather than binding something that is not there.
        current = await self._session.execute(
            select(McpToken.librechat_user_id).where(McpToken.id == token_id)
        )
        return str(current.scalar_one_or_none() or "")

    async def touch_last_used(self, token_id: UUID, *, now: datetime) -> None:
        """Stamp `last_used_at`. One UPDATE per authenticated MCP request.

        Worth the write: without it an admin cannot tell a token in daily use from one pasted into a
        config a year ago and forgotten, which is exactly the token worth revoking.
        """
        await self._session.execute(
            update(McpToken).where(McpToken.id == token_id).values(last_used_at=now)
        )

    async def touch_ldap_check(self, token_id: UUID, *, now: datetime) -> None:
        """Stamp `last_ldap_check_at` after the directory vouched for the operator."""
        await self._session.execute(
            update(McpToken).where(McpToken.id == token_id).values(last_ldap_check_at=now)
        )

    async def delete_tokens_for_user(self, user_id: UUID) -> int:
        """Cascade-revoke every token this operator holds; return how many.

        Every token, not only the presented one: the cascade revokes on the *operator* leaving, and
        a colleague-facing token left alive would authenticate on the next request. Deletion rather
        than a flag, matching the revoke path — revocation is the row's absence, and a status column
        would be a second source of truth the verify path could disagree with.
        """
        result = await self._session.execute(delete(McpToken).where(McpToken.user_id == user_id))
        return int(result.rowcount or 0)

    async def commit(self) -> None:
        """Make this path's writes durable. Called by the resolver, never implicitly."""
        await self._session.commit()


def _to_view(record: McpToken) -> McpTokenView:
    """ORM row → `McpTokenView`. `token_hash` has no destination and is dropped."""
    return McpTokenView(
        id=record.id,
        user_id=record.user_id,
        token_prefix=record.token_prefix,
        label=record.label,
        librechat_user_id=record.librechat_user_id,
        last_used_at=record.last_used_at,
        last_ldap_check_at=record.last_ldap_check_at,
        expires_at=record.expires_at,
        created_at=record.created_at,
    )
