"""MCP identity resolution.

New work: `noa-old` had no per-user MCP credential, so there is nothing to port.

This is the one function — *the* place a presented bearer becomes a NOA user. It is
deliberately framework-free: no fastmcp import, no Starlette request, nothing that knows
how the token arrived. `noa_api.mcp_auth.NoaTokenVerifier` is the fastmcp adapter over it
and `resolve_mcp_identity` is the HTTP one; both call `resolve()` and neither
re-implements a gate. "Auth mechanism swap = one file" only holds if the mechanism
lives in exactly one file, and `core/` declares no dependencies (root `pyproject.toml`),
so a `TokenVerifier` subclass could not live here anyway.

The gates run in a fixed order, and the order is the design:

1. **Bearer present** — a blank token is `mcp_token_missing`, not a failed lookup.
2. **Digest lookup** via `hash_mcp_token`, the *same* function `mint()` used
   (`core.auth.mcp_token_service`). A second digest implementation here would not fail
   loudly; it would reject every token that was ever minted.
3. **Expiry**.
4. **`users.is_active`** — read from the row on every request, never cached.
5. **TOFU read check** — header absent, or bound and unequal. No write yet.
6. **LDAP revalidation** when the check is stale.
7. **TOFU write + usage stamps**, one commit.

Steps 5 and 7 are two halves of one rule, split on purpose. The mismatch *check* is free
and must refuse before anything else happens; the *binding* is a write, and putting it
after the directory call means an LDAP outage never leaves a binding behind for a request
that was refused. A verifier that bound first would let one failed call pin a token
forever to whoever happened to make it.

Two things the fail-closed LDAP rule keeps apart, and conflating them is the classic bug this
module exists to avoid: "the directory says this person is gone" cascade-revokes every token they
hold, while "the directory did not answer" denies the request and touches nothing.
`LDAPService.user_exists_and_enabled` already separates them — `False` versus
`LdapUnavailableError` — and that error propagates from here unchanged, because the
fail-closed LDAP rule's answer already has a class, a message and a 503 mapping.

The plaintext is a parameter and a digest input, nothing else. It is never stored,
never logged, never placed on `McpIdentity`, and never in an error message or `detail`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from core.auth.mcp_auth_errors import (
    LibreChatUserHeaderMissingError,
    LibreChatUserMismatchError,
    McpTokenExpiredError,
    McpTokenInvalidError,
    McpTokenMissingError,
    McpUserInactiveError,
    McpUserNotInDirectoryError,
)
from core.auth.mcp_token_service import hash_mcp_token

# The header every MCP request needs, bound and unbound alike, because LibreChat is the sole
# MCP client. Lowercase because
# that is how `fastmcp.server.dependencies.get_http_headers()` returns custom keys and
# how HTTP/2 puts them on the wire; callers passing a raw dict should lowercase too.
LIBRECHAT_USER_HEADER = "x-noa-librechat-user"

# Internal diagnostics for the `detail` slot: logs only, never a response body. None of
# these interpolate a token, a digest or a prefix.
DETAIL_NO_BEARER = "no bearer token on the request"
# S105: a log diagnostic naming the table, not a credential — the `TOKEN` in the constant
# name is what trips the check.
DETAIL_NO_TOKEN_ROW = "presented digest matched no `mcp_tokens` row"  # noqa: S105
DETAIL_HEADER_ABSENT = f"`{LIBRECHAT_USER_HEADER}` absent; required — LibreChat is the sole client"


@dataclass(frozen=True)
class McpIdentity:
    """Who a verified MCP bearer belongs to, as of this request.

    Carries no plaintext and no digest — not redacted, absent, the same rule
    `McpTokenView` follows. A caller that wants to key a cache on the credential has to
    hash it themselves rather than find it hanging off the identity.

    `librechat_user_id` is the *effective* binding after this request, so a first call
    returns the value it just bound rather than the NULL it read.
    """

    user_id: UUID
    email: str
    display_name: str | None
    token_id: UUID
    librechat_user_id: str
    expires_at: datetime | None


class McpAuthenticationRow(Protocol):
    """The `mcp_tokens ⋈ users` slice the auth path reads.

    One shape rather than two lookups: `is_active` has to be read in the same statement as
    the token row, or a token could authenticate against a user row that changed between
    the two reads. No `token_hash` field — the caller supplied the digest, so handing
    it back would only create somewhere for it to leak from.
    """

    token_id: UUID
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    librechat_user_id: str | None
    expires_at: datetime | None
    last_ldap_check_at: datetime | None


class McpIdentityRepository(Protocol):
    """Persistence behind `McpIdentityResolver`.

    Distinct from `McpTokenRepository` because the transaction discipline is
    different, not because the table is: the admin CRUD path flushes into the request's
    transaction, while this path owns its session and commits its own writes. `commit()` is
    therefore on the Protocol — the resolver decides when a binding becomes durable, and a
    caller cannot skip it.
    """

    async def get_by_token_hash(self, token_hash: str) -> McpAuthenticationRow | None: ...

    async def bind_librechat_user(self, token_id: UUID, librechat_user_id: str) -> str: ...

    async def touch_last_used(self, token_id: UUID, *, now: datetime) -> None: ...

    async def touch_ldap_check(self, token_id: UUID, *, now: datetime) -> None: ...

    async def delete_tokens_for_user(self, user_id: UUID) -> int: ...

    async def commit(self) -> None: ...


class DirectoryPresence(Protocol):
    """The slice of `LDAPService` the revalidation path uses.

    Password-free by construction: this runs on a background-ish path with no operator
    credential in hand, which is exactly why `user_exists_and_enabled` exists separately
    from `authenticate` (mirrors `DirectoryAuthenticator` in `core.auth.auth_service`).
    """

    async def user_exists_and_enabled(self, email: str) -> bool: ...


class McpIdentityResolver:
    """Turn a presented bearer into an `McpIdentity`, or refuse with a named cause."""

    def __init__(
        self,
        *,
        repository: McpIdentityRepository,
        directory: DirectoryPresence,
        ldap_revalidate_seconds: int,
    ) -> None:
        self._repository = repository
        self._directory = directory
        # `MCP_TOKEN_LDAP_REVALIDATE_SECONDS`, default 900 (`core.config`). Zero means
        # revalidate on every request — a legitimate setting for a small deployment, and the
        # reason the staleness test below is `>=` rather than `>`.
        self._ldap_revalidate_seconds = max(0, ldap_revalidate_seconds)

    async def resolve(
        self,
        *,
        presented_token: str | None,
        librechat_user_id: str | None,
        now: datetime | None = None,
    ) -> McpIdentity:
        """Resolve the caller behind `presented_token`, or raise.

        Raises, in gate order: `McpTokenMissingError`, `McpTokenInvalidError`,
        `McpTokenExpiredError`, `McpUserInactiveError`, `LibreChatUserHeaderMissingError`,
        `LibreChatUserMismatchError`, `McpUserNotInDirectoryError`, and `LdapUnavailableError`
        straight from the directory (the fail-closed LDAP rule — deny, do not revoke).

        `now` is injectable so expiry and staleness are asserted as equalities in tests
        rather than tolerances; production passes nothing.
        """
        moment = now or datetime.now(UTC)

        row = await self._load_row(presented_token)
        self._assert_not_expired(row, moment)
        self._assert_active(row)

        presented_librechat_user = self._normalize_header(librechat_user_id)
        self._assert_binding_matches(row, presented_librechat_user)

        await self._revalidate_against_directory(row, moment)

        effective_binding = await self._bind_if_unbound(row, presented_librechat_user)
        await self._repository.touch_last_used(row.token_id, now=moment)
        await self._repository.commit()

        return McpIdentity(
            user_id=row.user_id,
            email=row.email,
            display_name=row.display_name,
            token_id=row.token_id,
            librechat_user_id=effective_binding,
            expires_at=row.expires_at,
        )

    # --- Gates ---

    async def _load_row(self, presented_token: str | None) -> McpAuthenticationRow:
        """Gates 1-2: a bearer is present, and its digest matches a row."""
        token = (presented_token or "").strip()
        if not token:
            raise McpTokenMissingError(DETAIL_NO_BEARER)

        row = await self._repository.get_by_token_hash(hash_mcp_token(token))
        if row is None:
            raise McpTokenInvalidError(DETAIL_NO_TOKEN_ROW)
        return row

    @staticmethod
    def _assert_not_expired(row: McpAuthenticationRow, moment: datetime) -> None:
        """Gate 3: `expires_at` has not passed.

        Checked here as well as by the SDK — `BearerAuthBackend` rejects an `AccessToken`
        whose `expires_at` is in the past, but only after `verify_token` has returned,
        by which point this path would already have stamped `last_used_at` and possibly
        bound a token on an expired credential. `<=` rather than `<` so a token is dead at
        its expiry instant rather than one second after it.
        """
        if row.expires_at is not None and row.expires_at <= moment:
            raise McpTokenExpiredError(f"token `{row.token_id}` expired at {row.expires_at}")

    @staticmethod
    def _assert_active(row: McpAuthenticationRow) -> None:
        """Gate 4: `users.is_active`.

        Before the TOFU gates on purpose: a disabled operator must not be able to bind a
        token, or re-enabling them later would silently hand the binding to whoever made
        the call while they were off.
        """
        if not row.is_active:
            raise McpUserInactiveError(f"`users.is_active` is False for `{row.user_id}`")

    @staticmethod
    def _assert_binding_matches(
        row: McpAuthenticationRow, presented_librechat_user: str | None
    ) -> None:
        """Gate 5: the read half of TOFU.

        Absent header refuses for bound *and* unbound tokens. LibreChat is the sole
        client, so "no header" is an unsupported client rather than a client that has not
        bound yet — and treating it as the latter would let anyone holding a copied token
        use it from `curl` forever, since an unbound token would never pin.
        """
        if presented_librechat_user is None:
            raise LibreChatUserHeaderMissingError(DETAIL_HEADER_ABSENT)

        if row.librechat_user_id is not None and row.librechat_user_id != presented_librechat_user:
            # `detail` names the token, not the two identifiers: a log line pairing them is
            # a map from NOA tokens to LibreChat accounts.
            raise LibreChatUserMismatchError(
                f"token `{row.token_id}` is bound to another LibreChat user"
            )

    async def _revalidate_against_directory(
        self, row: McpAuthenticationRow, moment: datetime
    ) -> None:
        """Gate 6: LDAP is the source of truth for employment.

        Three outcomes, and keeping them apart is the whole point:

        - fresh enough → no directory call at all, so a working token costs one query
        - present and enabled → stamp `last_ldap_check_at`
        - absent or disabled → cascade-revoke every token this operator holds, commit that
          revoke, and refuse

        `LdapUnavailableError` is not caught. The fail-closed LDAP rule holds, and swallowing it
        here would turn a network blip into a mass revoke — the one mistake in this module that
        cannot be undone.
        """
        if not self._is_stale(row.last_ldap_check_at, moment):
            return

        if await self._directory.user_exists_and_enabled(row.email):
            await self._repository.touch_ldap_check(row.token_id, now=moment)
            return

        revoked = await self._repository.delete_tokens_for_user(row.user_id)
        # Committed before raising: the caller's error path has no transaction of its own
        # to flush, and a revoke that rolls back is a revoke that did not happen.
        await self._repository.commit()
        raise McpUserNotInDirectoryError(
            f"directory has no enabled entry for `{row.user_id}`; revoked {revoked} token(s)"
        )

    async def _bind_if_unbound(
        self, row: McpAuthenticationRow, presented_librechat_user: str
    ) -> str:
        """Gate 7: the write half of TOFU.

        Delegated to the repository rather than done as read-then-write here, because the
        race is real: two first calls arriving together must not both bind. The repository
        binds conditionally and returns whatever the row *now* holds, so a loser sees the
        winner's value and is refused by the same rule an ordinary mismatch is.
        """
        if row.librechat_user_id is not None:
            return row.librechat_user_id

        bound = await self._repository.bind_librechat_user(row.token_id, presented_librechat_user)
        if bound != presented_librechat_user:
            raise LibreChatUserMismatchError(
                f"token `{row.token_id}` was bound concurrently to another LibreChat user"
            )
        return bound

    # --- Helpers ---

    def _is_stale(self, last_checked_at: datetime | None, moment: datetime) -> bool:
        """Whether the directory owes us an answer about this operator.

        Never checked → stale, so a token minted while LDAP was down cannot skip its first
        revalidation. `>=` on the interval so a configured `0` means "every request" rather
        than "never, unless a whole second of clock passes".
        """
        if last_checked_at is None:
            return True
        return moment - last_checked_at >= timedelta(seconds=self._ldap_revalidate_seconds)

    @staticmethod
    def _normalize_header(librechat_user_id: str | None) -> str | None:
        """Strip the header value; blank counts as absent.

        A header sent as `X-Noa-LibreChat-User:` with an empty value is a client that
        failed to interpolate `{{LIBRECHAT_USER_ID}}`. Binding a token to the empty string
        would pin it to every other client that makes the same mistake.
        """
        if librechat_user_id is None:
            return None
        normalized = librechat_user_id.strip()
        return normalized or None
