from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from noa_api.core.auth.errors import AuthRateLimitedError


@dataclass
class LoginRateLimitBucket:
    attempt_count: int
    blocked_until: datetime | None
    window_started_at: datetime


class LoginRateLimitRepositoryProtocol(Protocol):
    async def get_bucket(
        self, scope: str, scope_key: str
    ) -> LoginRateLimitBucket | None: ...

    async def upsert_bucket(
        self,
        scope: str,
        scope_key: str,
        *,
        attempt_count: int,
        window_started_at: datetime,
        blocked_until: datetime | None,
    ) -> LoginRateLimitBucket: ...

    async def clear_bucket(self, scope: str, scope_key: str) -> None: ...


class LoginRateLimiter:
    def __init__(
        self,
        repository: LoginRateLimitRepositoryProtocol,
        *,
        window_seconds: int,
        max_attempts: int,
        block_seconds: int,
    ) -> None:
        self._repository = repository
        self._window_seconds = window_seconds
        self._max_attempts = max_attempts
        self._block_seconds = block_seconds

    async def assert_allowed(
        self, *, email: str, ip_address: str, now: datetime | None = None
    ) -> None:
        current_time = now or datetime.now(UTC)
        for scope, scope_key in (("ip", ip_address), ("email", email)):
            bucket = await self._repository.get_bucket(scope, scope_key)
            if bucket and bucket.blocked_until and bucket.blocked_until > current_time:
                raise AuthRateLimitedError(
                    int((bucket.blocked_until - current_time).total_seconds())
                )

    async def record_failure(
        self, *, email: str, ip_address: str, now: datetime | None = None
    ) -> None:
        current_time = now or datetime.now(UTC)
        for scope, scope_key in (("ip", ip_address), ("email", email)):
            bucket = await self._repository.get_bucket(scope, scope_key)
            if (
                bucket is None
                or bucket.window_started_at + timedelta(seconds=self._window_seconds)
                <= current_time
            ):
                attempt_count = 1
                window_started_at = current_time
            else:
                attempt_count = bucket.attempt_count + 1
                window_started_at = bucket.window_started_at

            blocked_until = None
            if attempt_count >= self._max_attempts:
                blocked_until = current_time + timedelta(seconds=self._block_seconds)

            await self._repository.upsert_bucket(
                scope,
                scope_key,
                attempt_count=attempt_count,
                window_started_at=window_started_at,
                blocked_until=blocked_until,
            )

    async def record_success(self, *, email: str, ip_address: str) -> None:
        await self._repository.clear_bucket("ip", ip_address)
        await self._repository.clear_bucket("email", email)
