"""`LoginRateLimiter` policy guards (T8, V9).

Time is injected via the `now=` parameter on every method, so window rollover and block
expiry are asserted exactly rather than slept through. `AuthService` does not pass `now`
— it uses the wall clock — which is why the route tests in `test_auth_routes.py` cover
the wiring and these cover the arithmetic.

Storage is the same in-memory double the route tests use, so a change to its semantics
cannot make one file pass while the other fails.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.auth.errors import AuthRateLimitedError
from core.auth.login_rate_limiter import (
    SCOPE_EMAIL,
    SCOPE_IP,
    UNKNOWN_IP,
    LoginRateLimiter,
)
from support.auth import OPERATOR_EMAIL, FakeRateLimitRepository

IP = "203.0.113.7"
OTHER_IP = "203.0.113.8"
OTHER_EMAIL = "colleague@example.com"

WINDOW_SECONDS = 60
MAX_ATTEMPTS = 3
BLOCK_SECONDS = 600

T0 = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


def build_limiter(
    repository: FakeRateLimitRepository | None = None,
    *,
    window_seconds: int = WINDOW_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    block_seconds: int = BLOCK_SECONDS,
) -> tuple[LoginRateLimiter, FakeRateLimitRepository]:
    store = repository or FakeRateLimitRepository()
    limiter = LoginRateLimiter(
        store,
        window_seconds=window_seconds,
        max_attempts=max_attempts,
        block_seconds=block_seconds,
    )
    return limiter, store


async def fail(limiter: LoginRateLimiter, *, times: int, at: datetime) -> None:
    for _ in range(times):
        await limiter.record_failure(email=OPERATOR_EMAIL, ip_address=IP, now=at)


# --- Allowing ---


async def test_fresh_buckets_allow() -> None:
    limiter, _ = build_limiter()

    await limiter.assert_allowed(email=OPERATOR_EMAIL, ip_address=IP, now=T0)


async def test_below_max_attempts_still_allows() -> None:
    """V9 blocks *past* the max; the attempts up to it are the operator's typo budget."""
    limiter, _ = build_limiter()

    await fail(limiter, times=MAX_ATTEMPTS - 1, at=T0)

    await limiter.assert_allowed(email=OPERATOR_EMAIL, ip_address=IP, now=T0)


# --- Blocking ---


async def test_reaching_max_attempts_blocks() -> None:
    limiter, _ = build_limiter()

    await fail(limiter, times=MAX_ATTEMPTS, at=T0)

    with pytest.raises(AuthRateLimitedError):
        await limiter.assert_allowed(email=OPERATOR_EMAIL, ip_address=IP, now=T0)


async def test_retry_after_counts_down_toward_the_block_end() -> None:
    """The number the client gets is remaining time, not the configured duration."""
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS, at=T0)

    with pytest.raises(AuthRateLimitedError) as exc_info:
        await limiter.assert_allowed(
            email=OPERATOR_EMAIL, ip_address=IP, now=T0 + timedelta(seconds=100)
        )

    assert exc_info.value.retry_after_seconds == BLOCK_SECONDS - 100


def test_retry_after_never_reports_zero() -> None:
    """`Retry-After: 0` tells the client to retry immediately — the opposite of a block.

    A block with under a second left truncates to 0, so the floor lives in the error.
    """
    assert AuthRateLimitedError(0).retry_after_seconds == 1
    assert AuthRateLimitedError(-5).retry_after_seconds == 1


async def test_block_expires() -> None:
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS, at=T0)

    await limiter.assert_allowed(
        email=OPERATOR_EMAIL,
        ip_address=IP,
        now=T0 + timedelta(seconds=BLOCK_SECONDS + 1),
    )


async def test_block_boundary_is_exclusive() -> None:
    """At exactly `blocked_until` the block is over: `>` not `>=`, so it cannot hang."""
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS, at=T0)

    await limiter.assert_allowed(
        email=OPERATOR_EMAIL, ip_address=IP, now=T0 + timedelta(seconds=BLOCK_SECONDS)
    )


# --- Window ---


async def test_failures_outside_window_do_not_accumulate() -> None:
    """V9 is "max failures *within* a window", so a slow trickle never blocks."""
    limiter, store = build_limiter()

    for index in range(MAX_ATTEMPTS * 3):
        moment = T0 + timedelta(seconds=(WINDOW_SECONDS + 1) * index)
        await limiter.record_failure(email=OPERATOR_EMAIL, ip_address=IP, now=moment)
        await limiter.assert_allowed(email=OPERATOR_EMAIL, ip_address=IP, now=moment)

    assert store.buckets[(SCOPE_IP, IP)].attempt_count == 1


async def test_window_restart_resets_the_count_to_one() -> None:
    limiter, store = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS - 1, at=T0)

    await limiter.record_failure(
        email=OPERATOR_EMAIL, ip_address=IP, now=T0 + timedelta(seconds=WINDOW_SECONDS)
    )

    assert store.buckets[(SCOPE_IP, IP)].attempt_count == 1
    assert store.buckets[(SCOPE_IP, IP)].blocked_until is None


async def test_failures_inside_window_accumulate() -> None:
    limiter, store = build_limiter()

    for index in range(MAX_ATTEMPTS - 1):
        await limiter.record_failure(
            email=OPERATOR_EMAIL, ip_address=IP, now=T0 + timedelta(seconds=index)
        )

    assert store.buckets[(SCOPE_IP, IP)].attempt_count == MAX_ATTEMPTS - 1


# --- Scope independence ---


async def test_email_bucket_blocks_across_source_addresses() -> None:
    """IP-only limiting would let a botnet spread guesses over one account."""
    limiter, _ = build_limiter()

    for index in range(MAX_ATTEMPTS):
        await limiter.record_failure(email=OPERATOR_EMAIL, ip_address=f"198.51.100.{index}", now=T0)

    with pytest.raises(AuthRateLimitedError):
        await limiter.assert_allowed(email=OPERATOR_EMAIL, ip_address=OTHER_IP, now=T0)


async def test_ip_bucket_blocks_across_accounts() -> None:
    """Email-only limiting would let one host spray the whole directory."""
    limiter, _ = build_limiter()

    for index in range(MAX_ATTEMPTS):
        await limiter.record_failure(email=f"target{index}@example.com", ip_address=IP, now=T0)

    with pytest.raises(AuthRateLimitedError):
        await limiter.assert_allowed(email=OTHER_EMAIL, ip_address=IP, now=T0)


async def test_an_unrelated_operator_from_a_clean_address_is_unaffected() -> None:
    """Blocks are scoped: one operator's typos ⊥ deny everybody."""
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS, at=T0)

    await limiter.assert_allowed(email=OTHER_EMAIL, ip_address=OTHER_IP, now=T0)


# --- Keys ---


async def test_email_key_is_normalized() -> None:
    """Otherwise `Operator@Example.COM` is a fresh bucket and the limit is bypassable."""
    limiter, store = build_limiter()

    await limiter.record_failure(email="  Operator@Example.COM  ", ip_address=IP, now=T0)

    assert (SCOPE_EMAIL, OPERATOR_EMAIL) in store.buckets


@pytest.mark.parametrize("ip_address", ["", "   "])
async def test_blank_ip_folds_into_one_known_bucket(ip_address: str) -> None:
    """An unattributable attempt is still an attempt; dropping it would be a bypass."""
    limiter, store = build_limiter()

    await limiter.record_failure(email=OPERATOR_EMAIL, ip_address=ip_address, now=T0)

    assert (SCOPE_IP, UNKNOWN_IP) in store.buckets


# --- Success ---


async def test_record_success_clears_both_scopes() -> None:
    limiter, store = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS - 1, at=T0)

    await limiter.record_success(email=OPERATOR_EMAIL, ip_address=IP)

    assert store.buckets == {}
    assert set(store.cleared) == {(SCOPE_IP, IP), (SCOPE_EMAIL, OPERATOR_EMAIL)}


async def test_record_success_does_not_lift_a_block_on_another_account() -> None:
    """Clearing is per key, so one valid credential ⊥ unlock the whole directory."""
    limiter, _ = build_limiter()
    for _ in range(MAX_ATTEMPTS):
        await limiter.record_failure(email=OTHER_EMAIL, ip_address=OTHER_IP, now=T0)

    await limiter.record_success(email=OPERATOR_EMAIL, ip_address=IP)

    with pytest.raises(AuthRateLimitedError):
        await limiter.assert_allowed(email=OTHER_EMAIL, ip_address=OTHER_IP, now=T0)


# --- Configuration edges ---


async def test_max_attempts_of_one_blocks_on_the_first_failure() -> None:
    """A lockout-on-first-failure deployment is configurable and must behave."""
    limiter, _ = build_limiter(max_attempts=1)

    await limiter.record_failure(email=OPERATOR_EMAIL, ip_address=IP, now=T0)

    with pytest.raises(AuthRateLimitedError):
        await limiter.assert_allowed(email=OPERATOR_EMAIL, ip_address=IP, now=T0)
