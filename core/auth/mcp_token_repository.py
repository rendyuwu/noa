"""SQL behind MCP token management (T10, C5, V2).

New work — `noa-old` has no `mcp_tokens` table to port from.

Like T9's `SQLAuthorizationRepository`, every method takes the caller's `AsyncSession` and
flushes rather than commits: the transaction boundary belongs to the request (see
`noa_api.api.deps`), so a mint and its audit event land together or not at all.

Rows are converted to `McpTokenView` here rather than returned as ORM objects, following
`SQLLoginRateLimitRepository` returning `LoginRateLimitBucket`. Two reasons, and the
second is the security one: the service never holds an object carrying `token_hash`, and
the in-memory double in the tests cannot accidentally expose a field the SQL path would
have hidden.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
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

        Flushed, not committed, so `created_at` (a server default) and the generated id
        are readable in the same transaction the caller is building.

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

        Tie-broken on `id` because two tokens minted in one request share a `created_at`
        from the same statement timestamp, and an unstable order makes a paginated admin
        list drop or repeat a row.
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


def _to_view(record: McpToken) -> McpTokenView:
    """ORM row → `McpTokenView`. `token_hash` has no destination and is dropped (V2)."""
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


__all__ = ["SQLMcpTokenRepository"]
