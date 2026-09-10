"""Login rate limiter.

Ported from `noa-old` branch `MCP` (`core/auth/login_rate_limiter.py`).

The clock arithmetic moved to `core.auth.attempt_limiter.AttemptLimiter` when the MCP
identity resolver needed
the same counting for failed MCP authentication. What is left here is the part that
is about *login*: which keys an attempt lands in, and which error a block raises.

Two buckets accumulate per failed attempt — one keyed by source IP, one by the submitted
email — because either alone leaves a hole. IP-only lets a botnet spread guesses across one
account; email-only lets a single host spray the directory. `assert_allowed` denies when
*either* bucket is blocked.

The one deliberate departure from `noa-old` lives in the caller, not here: `AuthService`
records a failure only for credential-class errors. `noa-old` counted every `AuthError`
from LDAP, so a directory outage locked every operator out for the full block duration. See
`core.auth.auth_service`.

`LoginRateLimitBucket` and `LoginRateLimitRepository` are the shared names under their
original spelling. Kept as aliases rather than renamed at ~15 call sites, because the names
appear in `core.auth.auth_repository`, `noa_api.api.deps` and the test doubles, and a
rename there would be churn with no behavioural claim behind it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from core.auth.attempt_limiter import (
    UNKNOWN_KEY,
    AttemptBucket,
    AttemptKey,
    AttemptLimiter,
    AttemptLimitRepository,
)
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
UNKNOWN_IP: Final = UNKNOWN_KEY

DETAIL_BLOCKED = "login blocked by rate limiter"

# The shared shapes, under the names the login flow's callers already import.
LoginRateLimitBucket = AttemptBucket
LoginRateLimitRepository = AttemptLimitRepository


class LoginRateLimiter(AttemptLimiter):
    """Count failed logins per IP and per email; block past the max."""

    async def assert_allowed(
        self, *, email: str, ip_address: str, now: datetime | None = None
    ) -> None:
        """Raise `AuthRateLimitedError` when either bucket is still blocked."""
        await self._assert_keys_allowed(self._keys(email, ip_address), now=now)

    async def record_failure(
        self, *, email: str, ip_address: str, now: datetime | None = None
    ) -> None:
        """Count one failed attempt against both buckets."""
        await self._record_keys_failure(self._keys(email, ip_address), now=now)

    async def record_success(self, *, email: str, ip_address: str) -> None:
        """Drop both buckets: the operator proved they are not guessing.

        Clearing the IP bucket on any success does mean an attacker holding one valid
        credential can reset their own address's counter. Keeping it is the lesser
        evil — the alternative punishes a shared office NAT for one colleague's typos.
        """
        await self._clear_keys(self._keys(email, ip_address))

    # --- Internals ---

    def _blocked_error(self, retry_after_seconds: int, scope: str) -> AuthRateLimitedError:
        return AuthRateLimitedError(retry_after_seconds, f"{DETAIL_BLOCKED} on {scope}")

    @classmethod
    def _keys(cls, email: str, ip_address: str) -> tuple[AttemptKey, ...]:
        """(scope, key) pairs for one attempt.

        The email is lowercased as well as trimmed, or `Operator@Example.COM` is a fresh
        bucket and the limit is bypassable by holding shift.
        """
        return (
            (SCOPE_IP, cls._normalize_key(ip_address)),
            (SCOPE_EMAIL, email.strip().lower()),
        )


__all__ = [
    "SCOPES",
    "SCOPE_EMAIL",
    "SCOPE_IP",
    "UNKNOWN_IP",
    "LoginRateLimitBucket",
    "LoginRateLimitRepository",
    "LoginRateLimiter",
]
