"""`McpAuthRateLimiter` policy guards.

`test_login_rate_limiter.py` covers the arithmetic shared through `AttemptLimiter` — window
rollover, the block boundary, the count restarting at 1. This file covers only what is
specific to the MCP surface, because that is where the decisions were made:

- **which keys a bucket lands under.** No source-IP bucket: NOA runs in-cluster behind many
  workers, and LibreChat being the sole MCP client makes the address a request appears to come
  from identifies neither the operator nor even the pod for long. A block on it would take
  out every operator at once.
- **which denials count.** Five of the seven `McpAuthError` classes, and
  `LdapUnavailableError`, must not move a counter. That set is the whole point: `noa-old`
  counted every `AuthError` from LDAP, so a directory blip locked everybody out for the full
  block duration.

Time is injected on every call, so a block is asserted exactly rather than slept through.
The bucket store is the same in-memory double the login tests use, so a change to its
semantics cannot make one file pass while the other fails.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.auth.errors import LdapUnavailableError
from core.auth.mcp_auth_errors import (
    LibreChatUserHeaderMissingError,
    LibreChatUserMismatchError,
    McpAuthError,
    McpAuthRateLimitedError,
    McpTokenExpiredError,
    McpTokenInvalidError,
    McpTokenMissingError,
    McpUserInactiveError,
    McpUserNotInDirectoryError,
)
from core.auth.mcp_auth_rate_limiter import (
    SCOPE_MCP_CLIENT,
    SCOPE_MCP_TOKEN,
    McpAuthRateLimiter,
    counts_against_limit,
)
from support.auth import FakeRateLimitRepository
from support.errors import error_subclasses

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64

CLIENT = "librechat-user-1"
OTHER_CLIENT = "librechat-user-2"

WINDOW_SECONDS = 60
MAX_ATTEMPTS = 3
BLOCK_SECONDS = 600

T0 = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


def build_limiter(
    repository: FakeRateLimitRepository | None = None,
    *,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[McpAuthRateLimiter, FakeRateLimitRepository]:
    store = repository or FakeRateLimitRepository()
    limiter = McpAuthRateLimiter(
        store,
        window_seconds=WINDOW_SECONDS,
        max_attempts=max_attempts,
        block_seconds=BLOCK_SECONDS,
    )
    return limiter, store


async def fail(
    limiter: McpAuthRateLimiter,
    *,
    times: int,
    client: str | None = CLIENT,
    digest: str = DIGEST,
    at: datetime = T0,
) -> None:
    for _ in range(times):
        await limiter.record_failure(librechat_user_id=client, token_digest=digest, now=at)


# --- Keys ---


async def test_a_failure_lands_in_both_buckets() -> None:
    """Either key alone leaves a hole, so both are written."""
    limiter, store = build_limiter()

    await fail(limiter, times=1)

    assert set(store.buckets) == {(SCOPE_MCP_CLIENT, CLIENT), (SCOPE_MCP_TOKEN, DIGEST)}


async def test_no_source_address_bucket_exists() -> None:
    """In-cluster the client address identifies nothing, and being the sole MCP client makes
    it fleet-shared.

    Asserted as "no third bucket" rather than by name: the failure this guards against is
    someone adding an IP key back, and that would show up here as an extra row.
    """
    limiter, store = build_limiter()

    await fail(limiter, times=1)

    assert {scope for scope, _ in store.buckets} == {SCOPE_MCP_CLIENT, SCOPE_MCP_TOKEN}


async def test_an_absent_header_writes_no_client_bucket() -> None:
    """A missing header is refused uncounted (the named-401-body rule), so a shared "unknown"
    bucket would
    only ever collect misconfigured clients and block them against each other."""
    limiter, store = build_limiter()

    await fail(limiter, times=1, client=None)

    assert set(store.buckets) == {(SCOPE_MCP_TOKEN, DIGEST)}


@pytest.mark.parametrize("client", ["", "   "])
async def test_a_blank_header_writes_no_client_bucket(client: str) -> None:
    limiter, store = build_limiter()

    await fail(limiter, times=1, client=client)

    assert set(store.buckets) == {(SCOPE_MCP_TOKEN, DIGEST)}


# --- Blocking ---


async def test_fresh_buckets_allow() -> None:
    limiter, _ = build_limiter()

    await limiter.assert_allowed(librechat_user_id=CLIENT, token_digest=DIGEST, now=T0)


async def test_below_max_attempts_still_allows() -> None:
    limiter, _ = build_limiter()

    await fail(limiter, times=MAX_ATTEMPTS - 1)

    await limiter.assert_allowed(librechat_user_id=CLIENT, token_digest=DIGEST, now=T0)


async def test_reaching_max_attempts_blocks_the_client_key() -> None:
    """A fresh token from a blocked LibreChat account is still refused."""
    limiter, _ = build_limiter()

    for index in range(MAX_ATTEMPTS):
        await fail(limiter, times=1, digest=f"{index:064d}")

    with pytest.raises(McpAuthRateLimitedError):
        await limiter.assert_allowed(librechat_user_id=CLIENT, token_digest=OTHER_DIGEST, now=T0)


async def test_a_rotated_header_still_blocks_on_the_token_key() -> None:
    """The reason the token bucket exists: replay of one stolen token, header rotated.

    Each attempt supplies a different LibreChat id, so no client bucket ever accumulates;
    the digest is the only thing that stays put.
    """
    limiter, _ = build_limiter()

    for index in range(MAX_ATTEMPTS):
        await fail(limiter, times=1, client=f"rotated-{index}")

    with pytest.raises(McpAuthRateLimitedError):
        await limiter.assert_allowed(librechat_user_id="rotated-999", token_digest=DIGEST, now=T0)


async def test_an_unrelated_client_with_its_own_token_is_unaffected() -> None:
    """Blocks are scoped: one operator's bad token must never deny everybody (the
    sole-MCP-client hazard)."""
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS)

    await limiter.assert_allowed(librechat_user_id=OTHER_CLIENT, token_digest=OTHER_DIGEST, now=T0)


async def test_retry_after_counts_down_toward_the_block_end() -> None:
    """The number the client gets is remaining time, not the configured duration."""
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS)

    with pytest.raises(McpAuthRateLimitedError) as exc_info:
        await limiter.assert_allowed(
            librechat_user_id=CLIENT,
            token_digest=DIGEST,
            now=T0 + timedelta(seconds=100),
        )

    assert exc_info.value.retry_after_seconds == BLOCK_SECONDS - 100


def test_retry_after_never_reports_zero() -> None:
    """`Retry-After: 0` tells the client to retry immediately — the opposite of a block."""
    assert McpAuthRateLimitedError(0).retry_after_seconds == 1
    assert McpAuthRateLimitedError(-5).retry_after_seconds == 1


async def test_the_block_expires() -> None:
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS)

    await limiter.assert_allowed(
        librechat_user_id=CLIENT,
        token_digest=DIGEST,
        now=T0 + timedelta(seconds=BLOCK_SECONDS + 1),
    )


async def test_max_attempts_of_one_blocks_on_the_first_failure() -> None:
    limiter, _ = build_limiter(max_attempts=1)

    await fail(limiter, times=1)

    with pytest.raises(McpAuthRateLimitedError):
        await limiter.assert_allowed(librechat_user_id=CLIENT, token_digest=DIGEST, now=T0)


async def test_the_block_detail_names_the_scope_not_the_credential() -> None:
    """`detail` reaches the logs, so it may name a bucket but never a token."""
    limiter, _ = build_limiter()
    await fail(limiter, times=MAX_ATTEMPTS)

    with pytest.raises(McpAuthRateLimitedError) as exc_info:
        await limiter.assert_allowed(librechat_user_id=CLIENT, token_digest=DIGEST, now=T0)

    assert SCOPE_MCP_CLIENT in exc_info.value.detail
    assert DIGEST not in exc_info.value.detail
    assert CLIENT not in exc_info.value.detail


# --- Which denials count ---


@pytest.mark.parametrize(
    "error",
    [
        McpTokenInvalidError(),
        LibreChatUserMismatchError(),
    ],
)
def test_credential_class_denials_count(error: McpAuthError) -> None:
    """Guessing an unknown token, or presenting one bound to somebody else."""
    assert counts_against_limit(error) is True


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(McpTokenMissingError(), id="no-credential-is-not-a-guess"),
        pytest.param(McpTokenExpiredError(), id="a-real-token-that-aged-out"),
        pytest.param(LibreChatUserHeaderMissingError(), id="misconfigured-client"),
        pytest.param(McpUserInactiveError(), id="authenticated-then-refused"),
        pytest.param(McpUserNotInDirectoryError(), id="employment-ended"),
        pytest.param(McpAuthRateLimitedError(5), id="already-blocked"),
        pytest.param(McpAuthError(), id="unclassified"),
    ],
)
def test_non_credential_denials_do_not_count(error: McpAuthError) -> None:
    assert counts_against_limit(error) is False


def test_a_directory_outage_does_not_count() -> None:
    """The login flow's lesson: counting an LDAP outage locks every operator out for the block."""
    assert counts_against_limit(LdapUnavailableError("directory unreachable")) is False


def test_every_mcp_auth_error_class_has_a_counting_verdict() -> None:
    """A class added later must be a deliberate include or exclude, never an accident.

    `counts_against_limit` answers for anything, so this asserts the *decision* is written
    down: every concrete subclass appears in one of the two parametrized lists above.
    """
    decided = {
        McpAuthError,
        McpTokenMissingError,
        McpTokenInvalidError,
        McpTokenExpiredError,
        LibreChatUserHeaderMissingError,
        LibreChatUserMismatchError,
        McpUserInactiveError,
        McpUserNotInDirectoryError,
        McpAuthRateLimitedError,
    }

    assert error_subclasses(McpAuthError) == decided
