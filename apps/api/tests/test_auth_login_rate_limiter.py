from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from noa_api.core.auth.login_rate_limiter import AuthRateLimitedError, LoginRateLimiter


@dataclass
class _Bucket:
    attempt_count: int = 0
    blocked_until: datetime | None = None
    window_started_at: datetime | None = None


class _FakeRepo:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], _Bucket] = {}

    async def get_bucket(self, scope: str, scope_key: str):
        return self.data.get((scope, scope_key))

    async def upsert_bucket(
        self,
        scope: str,
        scope_key: str,
        *,
        attempt_count: int,
        window_started_at: datetime,
        blocked_until: datetime | None,
    ):
        bucket = _Bucket(
            attempt_count=attempt_count,
            blocked_until=blocked_until,
            window_started_at=window_started_at,
        )
        self.data[(scope, scope_key)] = bucket
        return bucket

    async def clear_bucket(self, scope: str, scope_key: str):
        self.data.pop((scope, scope_key), None)


@pytest.mark.asyncio
async def test_rate_limiter_resets_attempts_after_window_expires() -> None:
    repo = _FakeRepo()
    limiter = LoginRateLimiter(
        repo, window_seconds=60, max_attempts=3, block_seconds=300
    )
    now = datetime(2026, 4, 4, tzinfo=UTC)

    await limiter.record_failure(
        email="user@example.com", ip_address="127.0.0.1", now=now
    )
    await limiter.record_failure(
        email="user@example.com",
        ip_address="127.0.0.1",
        now=now + timedelta(seconds=61),
    )

    assert repo.data[("ip", "127.0.0.1")].attempt_count == 1
    assert repo.data[("email", "user@example.com")].attempt_count == 1


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_configured_failures() -> None:
    repo = _FakeRepo()
    limiter = LoginRateLimiter(
        repo, window_seconds=60, max_attempts=3, block_seconds=300
    )
    now = datetime(2026, 4, 4, tzinfo=UTC)

    await limiter.record_failure(
        email="user@example.com", ip_address="127.0.0.1", now=now
    )
    await limiter.record_failure(
        email="user@example.com", ip_address="127.0.0.1", now=now + timedelta(seconds=1)
    )
    await limiter.record_failure(
        email="user@example.com", ip_address="127.0.0.1", now=now + timedelta(seconds=2)
    )

    with pytest.raises(AuthRateLimitedError):
        await limiter.assert_allowed(
            email="user@example.com",
            ip_address="127.0.0.1",
            now=now + timedelta(seconds=3),
        )


@pytest.mark.asyncio
async def test_rate_limiter_clears_buckets_after_success() -> None:
    repo = _FakeRepo()
    limiter = LoginRateLimiter(
        repo, window_seconds=60, max_attempts=3, block_seconds=300
    )
    now = datetime(2026, 4, 4, tzinfo=UTC)

    await limiter.record_failure(
        email="user@example.com", ip_address="127.0.0.1", now=now
    )
    await limiter.record_success(email="user@example.com", ip_address="127.0.0.1")

    await limiter.assert_allowed(
        email="user@example.com", ip_address="127.0.0.1", now=now + timedelta(seconds=1)
    )
