from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.auth.login_rate_limiter import LoginRateLimitBucket
from noa_api.storage.postgres.models import LoginRateLimitRecord


class SQLLoginRateLimitRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_bucket(
        self, scope: str, scope_key: str
    ) -> LoginRateLimitBucket | None:
        result = await self._session.execute(
            select(LoginRateLimitRecord).where(
                LoginRateLimitRecord.scope == scope,
                LoginRateLimitRecord.scope_key == scope_key,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return LoginRateLimitBucket(
            attempt_count=record.attempt_count,
            blocked_until=record.blocked_until,
            window_started_at=record.window_started_at,
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
        # Use Postgres INSERT ... ON CONFLICT DO UPDATE for atomic upsert
        stmt = insert(LoginRateLimitRecord).values(
            scope=scope,
            scope_key=scope_key,
            attempt_count=attempt_count,
            window_started_at=window_started_at,
            blocked_until=blocked_until,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_login_rate_limits_scope_key",
            set_={
                "attempt_count": stmt.excluded.attempt_count,
                "window_started_at": stmt.excluded.window_started_at,
                "blocked_until": stmt.excluded.blocked_until,
            },
        ).returning(
            LoginRateLimitRecord.attempt_count,
            LoginRateLimitRecord.blocked_until,
            LoginRateLimitRecord.window_started_at,
        )
        result = await self._session.execute(stmt)
        row = result.one()
        return LoginRateLimitBucket(
            attempt_count=row.attempt_count,
            blocked_until=row.blocked_until,
            window_started_at=row.window_started_at,
        )

    async def clear_bucket(self, scope: str, scope_key: str) -> None:
        await self._session.execute(
            delete(LoginRateLimitRecord).where(
                LoginRateLimitRecord.scope == scope,
                LoginRateLimitRecord.scope_key == scope_key,
            )
        )
