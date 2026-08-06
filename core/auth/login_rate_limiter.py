"""Login rate limiter (T8, V9).

Ported from `noa-old` branch `MCP` (`core/auth/login_rate_limiter.py`, C13).

Two buckets accumulate per failed attempt — one keyed by source IP, one by the
submitted email — because either alone leaves a hole. IP-only lets a botnet spread
guesses across one account; email-only lets a single host spray the directory.
`assert_allowed` denies when *either* bucket is blocked.

Storage sits behind `LoginRateLimitRepository` so the policy here is testable
without Postgres, and so the counters survive a restart when the real
implementation is used. In-process counters would reset on every deploy, which is
the moment an attacker's window opens widest.

The one deliberate departure from `noa-old` lives in the caller, not here:
`AuthService` records a failure only for credential-class errors. `noa-old` counted
every `AuthError` from LDAP, so a directory outage locked every operator out for the
full block duration. See `core.auth.auth_service`.

Not a defence against a distributed attack: an attacker with many source addresses
gets one IP bucket per address, and the email bucket is the only thing standing in
the way. That is the shape V9 asks for — attempt limiting, not bot detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from core.auth.errors import AuthRateLimitedError

# Bucket scopes. Values reach `login_rate_limits.scope` (String(20)) and are stable
# — renaming one orphans every live bucket under the old name.
SCOPE_IP: Final = "ip"
SCOPE_EMAIL: Final = "email"

# Checked in this order, but the outcome does not depend on it: both are consulted
# and either being blocked denies the attempt.
SCOPES: Final = (SCOPE_IP, SCOPE_EMAIL)

# Stand-in when the ASGI server reports no client address. Every such request shares
# one bucket, which is deliberate: an unattributable attempt is still an attempt,
# and dropping it from the count would make "no client address" a bypass.
UNKNOWN_IP: Final = "unknown"

DETAIL_BLOCKED = "login blocked by rate limiter"


@dataclass(frozen=True)
class LoginRateLimitBucket:
    """Attempt counter for one (scope, key) pair.

    `blocked_until` NULL means counting but not blocked. `window_started_at` is what
    ages the counter out: attempts older than the window do not accumulate.
    """

    attempt_count: int
    window_started_at: datetime
    blocked_until: datetime | None


class LoginRateLimitRepository(Protocol):
    """Bucket persistence. One implementation is SQL, one is a test double."""

    async def get_bucket(self, scope: str, scope_key: str) -> LoginRateLimitBucket | None: ...

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
    """Count failed logins per IP and per email; block past the max (V9)."""

    def __init__(
        self,
        repository: LoginRateLimitRepository,
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
        """Raise `AuthRateLimitedError` when either bucket is still blocked.

        Reads `blocked_until` only, never `attempt_count`: the count alone cannot say
        whether its window has aged out, and `record_failure` is what converts a
        reached count into a block with an end time.
        """
        current_time = now or datetime.now(UTC)

        for scope, scope_key in self._keys(email, ip_address):
            bucket = await self._repository.get_bucket(scope, scope_key)
            if bucket is None or bucket.blocked_until is None:
                continue
            if bucket.blocked_until > current_time:
                remaining = (bucket.blocked_until - current_time).total_seconds()
                raise AuthRateLimitedError(int(remaining), f"{DETAIL_BLOCKED} on {scope}")

    async def record_failure(
        self, *, email: str, ip_address: str, now: datetime | None = None
    ) -> None:
        """Count one failed attempt against both buckets.

        A bucket whose window has elapsed restarts at 1 rather than resuming, so
        attempts spread thinner than the window never accumulate into a block.
        """
        current_time = now or datetime.now(UTC)

        for scope, scope_key in self._keys(email, ip_address):
            bucket = await self._repository.get_bucket(scope, scope_key)

            if bucket is None or self._window_elapsed(bucket, current_time):
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
        """Drop both buckets: the operator proved they are not guessing.

        Clearing the IP bucket on any success does mean an attacker holding one valid
        credential can reset their own address's counter. Keeping it is the lesser
        evil — the alternative punishes a shared office NAT for one colleague's typos.
        """
        for scope, scope_key in self._keys(email, ip_address):
            await self._repository.clear_bucket(scope, scope_key)

    # --- Internals ---

    @staticmethod
    def _keys(email: str, ip_address: str) -> tuple[tuple[str, str], ...]:
        """(scope, key) pairs for one attempt. Blank IP folds into `UNKNOWN_IP`."""
        return (
            (SCOPE_IP, ip_address.strip() or UNKNOWN_IP),
            (SCOPE_EMAIL, email.strip().lower()),
        )

    def _window_elapsed(self, bucket: LoginRateLimitBucket, current_time: datetime) -> bool:
        return bucket.window_started_at + timedelta(seconds=self._window_seconds) <= current_time


__all__ = [
    "SCOPES",
    "SCOPE_EMAIL",
    "SCOPE_IP",
    "UNKNOWN_IP",
    "LoginRateLimitBucket",
    "LoginRateLimitRepository",
    "LoginRateLimiter",
]
