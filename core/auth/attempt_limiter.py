"""Attempt counting shared by every rate-limited auth surface.

Extracted from `core.auth.login_rate_limiter` when MCP token auth needed the same
arithmetic for failed MCP authentication. Nothing here is new behaviour — the window
rollover, the `>=` on the maximum and the `>` on `blocked_until` are the semantics the
LDAP-to-JWT login flow shipped and `test_login_rate_limiter.py` pins. What changed is
that they are now stated once:
duplicating ~60 lines of clock arithmetic per surface is how two limiters end up
disagreeing about whether a bucket at exactly its window boundary still counts.

A subclass supplies two things, and only two:

- **the keys** — which (scope, key) pairs one attempt lands in. Login uses source IP and
  submitted email; the MCP path uses the LibreChat account and the presented token digest
  (`core.auth.mcp_auth_rate_limiter`). Every key is consulted, and *any* blocked bucket
  denies, because a single key always leaves a hole: one shared address hides a botnet,
  one account hides a spray across a directory.
- **the error** — `_blocked_error`, so a login block reports `login_rate_limited` and an
  MCP block reports `mcp_auth_rate_limited`. One shared error class would put the login
  page's advice in front of an MCP client that has no login page to visit.

Storage sits behind `AttemptLimitRepository` so policy is testable without Postgres, and
so counters survive a restart when the SQL implementation is used. In-process counters
would reset on every deploy, which is the moment an attacker's window opens widest.

Deliberately NOT here: what counts as a failure. That is the caller's judgement and it is
where the interesting bug lives — `noa-old` counted every `AuthError` from LDAP, so a
directory outage locked every operator out for the full block duration. Both callers now
record failures for credential-class denials only (`core.auth.auth_service`,
`core.auth.mcp_auth_rate_limiter.COUNTED_DENIALS`).

Not a defence against a distributed attack: an attacker who can vary every key gets a
fresh bucket each time. That is the shape rate limiting asks for — attempt limiting,
not bot detection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from core.errors import NoaError

# Stand-in for a key the request did not supply. Every such attempt shares one bucket,
# which is deliberate: an unattributable attempt is still an attempt, and dropping it
# would make "no value" a bypass.
UNKNOWN_KEY: Final = "unknown"

# One (scope, key) pair — the address of a bucket.
AttemptKey = tuple[str, str]


@dataclass(frozen=True)
class AttemptBucket:
    """Attempt counter for one (scope, key) pair.

    `blocked_until` NULL means counting but not blocked. `window_started_at` is what ages
    the counter out: attempts older than the window do not accumulate.
    """

    attempt_count: int
    window_started_at: datetime
    blocked_until: datetime | None


class AttemptLimitRepository(Protocol):
    """Bucket persistence. One implementation is SQL, one is a test double."""

    async def get_bucket(self, scope: str, scope_key: str) -> AttemptBucket | None: ...

    async def upsert_bucket(
        self,
        scope: str,
        scope_key: str,
        *,
        attempt_count: int,
        window_started_at: datetime,
        blocked_until: datetime | None,
    ) -> AttemptBucket: ...

    async def clear_bucket(self, scope: str, scope_key: str) -> None: ...


class AttemptLimiter:
    """Count failures per key; block past the configured maximum.

    Every operation is protected, and the public surface belongs to the subclass. That is
    on purpose: a limiter's callers should name the thing being limited
    (`assert_allowed(email=…, ip_address=…)`), not assemble `(scope, key)` tuples at the
    call site, and a public keys-based method here would be a second way in that skips the
    subclass's key normalization.

    Abstract in one respect: `_blocked_error` has no useful default, because the error a
    caller raises is what tells the operator where they are blocked.
    """

    def __init__(
        self,
        repository: AttemptLimitRepository,
        *,
        window_seconds: int,
        max_attempts: int,
        block_seconds: int,
    ) -> None:
        self._repository = repository
        self._window_seconds = window_seconds
        self._max_attempts = max_attempts
        self._block_seconds = block_seconds

    async def _assert_keys_allowed(
        self, keys: Sequence[AttemptKey], *, now: datetime | None = None
    ) -> None:
        """Raise when any of `keys` is still blocked.

        Reads `blocked_until` only, never `attempt_count`: the count alone cannot say
        whether its window has aged out, and `record_failure` is what converts a reached
        count into a block with an end time.
        """
        current_time = now or datetime.now(UTC)

        for scope, scope_key in keys:
            bucket = await self._repository.get_bucket(scope, scope_key)
            if bucket is None or bucket.blocked_until is None:
                continue
            if bucket.blocked_until > current_time:
                remaining = (bucket.blocked_until - current_time).total_seconds()
                raise self._blocked_error(int(remaining), scope)

    async def _record_keys_failure(
        self, keys: Sequence[AttemptKey], *, now: datetime | None = None
    ) -> None:
        """Count one failed attempt against every key.

        A bucket whose window has elapsed restarts at 1 rather than resuming, so attempts
        spread thinner than the window never accumulate into a block.
        """
        current_time = now or datetime.now(UTC)

        for scope, scope_key in keys:
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

    async def _clear_keys(self, keys: Sequence[AttemptKey]) -> None:
        """Drop every bucket in `keys` — the caller proved they are not guessing."""
        for scope, scope_key in keys:
            await self._repository.clear_bucket(scope, scope_key)

    # --- Internals ---

    def _blocked_error(self, retry_after_seconds: int, scope: str) -> NoaError:
        """The error to raise for a blocked bucket in `scope`. Subclasses override."""
        raise NotImplementedError

    def _window_elapsed(self, bucket: AttemptBucket, current_time: datetime) -> bool:
        return bucket.window_started_at + timedelta(seconds=self._window_seconds) <= current_time

    @staticmethod
    def _normalize_key(value: str) -> str:
        """Trim a key; blank folds into `UNKNOWN_KEY` rather than vanishing."""
        return value.strip() or UNKNOWN_KEY


__all__ = [
    "UNKNOWN_KEY",
    "AttemptBucket",
    "AttemptKey",
    "AttemptLimitRepository",
    "AttemptLimiter",
]
