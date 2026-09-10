"""Doubles for MCP identity resolution.

Same split as `support.mcp_tokens`: these cover policy, and `SQLMcpIdentityRepository` gets
its own coverage against a live scratch database in `test_mcp_identity_repository.py`. The
*real* `McpIdentityResolver` runs against these doubles, so the gate order, the TOFU rules
and the fail-closed behaviour are exercised for real — only the SQL and the directory are
faked.

`FakeMcpIdentityRepository` stores `token_hash` and counts writes. Both are the assertion
surface for invariants a value-only double could not express:

- the digest, because V2's claim is that the lookup key is a SHA-256 of the plaintext and
  nothing else, which you can only check by looking at what was stored;
- `commits`, `binds` and `ldap_touches`, because several V4 rules are about *whether* a
  write happened — an LDAP outage must deny while touching nothing, and a fresh token must
  not call the directory at all.

`FakeDirectory` can answer, deny, or raise `LdapUnavailableError`, which is the three-way
split V4 rests on. It records the emails it was asked about so a test can prove the call
did not happen rather than inferring it from the result.

`build_auth_context` is the same doubles behind an `McpAuthContext`, so the verifier
and the HTTP request path are exercised over one set of fakes. Its session is a stub that
nothing touches: both repository doubles ignore it, which is what lets the R4 header check
run in the suite that always runs rather than only where Postgres is up.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.http import set_http_request
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from core.auth.errors import LdapUnavailableError
from core.auth.mcp_identity import McpIdentity, McpIdentityResolver
from core.auth.mcp_token_service import generate_mcp_token, hash_mcp_token
from noa_api.mcp_request_auth import McpAuthContext, identity_claims
from support.auth import FakeRateLimitRepository

# Fixed clock, so staleness and expiry assertions are equalities rather than tolerances.
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

EMAIL = "operator@example.com"
DISPLAY_NAME = "Ops Operator"

# Two LibreChat account identifiers — one the token binds to, one it must refuse.
LIBRECHAT_USER = "librechat-user-1"
OTHER_LIBRECHAT_USER = "librechat-user-2"

REVALIDATE_SECONDS = 900

# Rate-limit policy for T12's tests. Deliberately tighter than the shipped default (5) so a
# test reaches a block in three lines instead of five.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_ATTEMPTS = 3
RATE_LIMIT_BLOCK_SECONDS = 600


@dataclass
class StoredAuthToken:
    """One `mcp_tokens` row as the fake holds it, joined to its user's fields."""

    token_id: UUID
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    librechat_user_id: str | None
    expires_at: datetime | None
    last_ldap_check_at: datetime | None
    last_used_at: datetime | None
    token_hash: str


@dataclass(frozen=True)
class FakeAuthRow:
    """`McpAuthenticationRow` — a snapshot, so the resolver cannot mutate the store."""

    token_id: UUID
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    librechat_user_id: str | None
    expires_at: datetime | None
    last_ldap_check_at: datetime | None


class FakeMcpIdentityRepository:
    """In-memory `McpIdentityRepository`."""

    def __init__(self) -> None:
        self.tokens: dict[UUID, StoredAuthToken] = {}
        # Write counters — see the module docstring.
        self.commits = 0
        self.binds = 0
        self.ldap_touches = 0
        self.used_touches = 0
        # Set by a test to simulate losing the TOFU bind race.
        self.bind_race_winner: str | None = None

    # --- Protocol ---

    async def get_by_token_hash(self, token_hash: str) -> FakeAuthRow | None:
        for stored in self.tokens.values():
            if stored.token_hash == token_hash:
                return FakeAuthRow(
                    token_id=stored.token_id,
                    user_id=stored.user_id,
                    email=stored.email,
                    display_name=stored.display_name,
                    is_active=stored.is_active,
                    librechat_user_id=stored.librechat_user_id,
                    expires_at=stored.expires_at,
                    last_ldap_check_at=stored.last_ldap_check_at,
                )
        return None

    async def bind_librechat_user(self, token_id: UUID, librechat_user_id: str) -> str:
        """Compare-and-set, like the SQL: an already-bound row keeps what it has."""
        self.binds += 1
        stored = self.tokens.get(token_id)
        if stored is None:
            return ""
        if self.bind_race_winner is not None:
            # Someone else bound between the read and this write.
            stored.librechat_user_id = self.bind_race_winner
        elif stored.librechat_user_id is None:
            stored.librechat_user_id = librechat_user_id
        return stored.librechat_user_id

    async def touch_last_used(self, token_id: UUID, *, now: datetime) -> None:
        self.used_touches += 1
        stored = self.tokens.get(token_id)
        if stored is not None:
            stored.last_used_at = now

    async def touch_ldap_check(self, token_id: UUID, *, now: datetime) -> None:
        self.ldap_touches += 1
        stored = self.tokens.get(token_id)
        if stored is not None:
            stored.last_ldap_check_at = now

    async def delete_tokens_for_user(self, user_id: UUID) -> int:
        doomed = [token_id for token_id, stored in self.tokens.items() if stored.user_id == user_id]
        for token_id in doomed:
            del self.tokens[token_id]
        return len(doomed)

    async def commit(self) -> None:
        self.commits += 1

    # --- Test helpers ---

    def add_token(
        self,
        *,
        user_id: UUID | None = None,
        email: str = EMAIL,
        is_active: bool = True,
        librechat_user_id: str | None = None,
        expires_at: datetime | None = None,
        last_ldap_check_at: datetime | None = NOW,
    ) -> tuple[str, StoredAuthToken]:
        """Store a token and return its plaintext plus the row.

        The plaintext goes through `generate_mcp_token`/`hash_mcp_token` rather than being
        a literal, so what the fake holds is exactly what `mint()` would have written.
        """
        plaintext = generate_mcp_token()
        stored = StoredAuthToken(
            token_id=uuid4(),
            user_id=user_id or uuid4(),
            email=email,
            display_name=DISPLAY_NAME,
            is_active=is_active,
            librechat_user_id=librechat_user_id,
            expires_at=expires_at,
            last_ldap_check_at=last_ldap_check_at,
            last_used_at=None,
            token_hash=hash_mcp_token(plaintext),
        )
        self.tokens[stored.token_id] = stored
        return plaintext, stored

    @property
    def stored_hashes(self) -> list[str]:
        return [stored.token_hash for stored in self.tokens.values()]


class FakeDirectory:
    """`DirectoryPresence` with the three answers V4 distinguishes."""

    def __init__(self, *, present: bool = True, unavailable: bool = False) -> None:
        self.present = present
        self.unavailable = unavailable
        self.checked_emails: list[str] = []

    async def user_exists_and_enabled(self, email: str) -> bool:
        self.checked_emails.append(email)
        if self.unavailable:
            raise LdapUnavailableError("directory unreachable in this test")
        return self.present

    @property
    def call_count(self) -> int:
        return len(self.checked_emails)


@dataclass
class IdentityFixture:
    """The resolver under test plus the doubles behind it."""

    resolver: McpIdentityResolver
    repository: FakeMcpIdentityRepository
    directory: FakeDirectory
    tokens: list[str] = field(default_factory=list)


def build_resolver(
    *,
    present: bool = True,
    unavailable: bool = False,
    ldap_revalidate_seconds: int = REVALIDATE_SECONDS,
) -> IdentityFixture:
    """A real `McpIdentityResolver` over in-memory doubles."""
    repository = FakeMcpIdentityRepository()
    directory = FakeDirectory(present=present, unavailable=unavailable)
    return IdentityFixture(
        resolver=McpIdentityResolver(
            repository=repository,
            directory=directory,
            ldap_revalidate_seconds=ldap_revalidate_seconds,
        ),
        repository=repository,
        directory=directory,
    )


def stale_check(*, seconds: int = REVALIDATE_SECONDS) -> datetime:
    """A `last_ldap_check_at` exactly `seconds` old — the staleness boundary."""
    return NOW - timedelta(seconds=seconds)


class StubSession:
    """Stands in for `AsyncSession`. Both repository doubles ignore it."""


def authenticated_caller(user_id: UUID | None = None) -> tuple[AuthenticatedUser, UUID]:
    """An accepted caller, shaped exactly as `NoaTokenVerifier` shapes one.

    `scope["user"]` is where `get_access_token()` looks first, so this is what lets a test
    drive a tool function through the *production* `current_mcp_identity` rather than a
    patched name — the identity a tool reads is the one the auth middleware left behind.

    `identity_claims` rather than a dict literal, so the keys the verifier writes and the keys
    `current_mcp_identity` reads stay one set of constants: a hand-written claims dict would
    let a test pass against an identity production never produces.

    Shared by T33's gate tests and T63's result tool — both need a caller whose id they
    then assert a row against, and two copies of this construction would be two chances for a
    test to agree with itself.
    """
    resolved = user_id or uuid4()
    identity = McpIdentity(
        user_id=resolved,
        email=EMAIL,
        display_name=DISPLAY_NAME,
        token_id=uuid4(),
        librechat_user_id=LIBRECHAT_USER,
        expires_at=None,
    )
    return AuthenticatedUser(
        AccessToken(
            token="opaque",
            client_id=str(resolved),
            scopes=[],
            claims=identity_claims(identity),
        )
    ), resolved


@contextmanager
def http_request_context(
    headers: Mapping[str, str] | None = None,
    *,
    path: str = "/mcp",
    user: Any = None,
) -> Iterator[Request]:
    """Run a block inside the request contextvar `RequestContextMiddleware` sets.

    Built from a raw ASGI scope rather than a `TestClient` round trip so a unit test can
    name the exact headers on the wire, including their absence. The `Request` is yielded so
    a test can read back what production wrote onto the scope — T12 stashes its refusal
    there.

    `user` fills `scope["user"]`, which is where `get_access_token()` looks first; pass an
    `AuthenticatedUser` to stand in for a request the auth middleware already accepted.
    """
    raw = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw,
        "client": ("127.0.0.1", 12345),
        "scheme": "http",
        "server": ("noa.internal", 80),
    }
    if user is not None:
        scope["user"] = user

    with set_http_request(Request(scope)) as request:
        yield request


def build_auth_context(
    *,
    repository: FakeMcpIdentityRepository,
    directory: FakeDirectory | None = None,
    rate_limits: FakeRateLimitRepository | None = None,
    ldap_revalidate_seconds: int = REVALIDATE_SECONDS,
    max_attempts: int = RATE_LIMIT_MAX_ATTEMPTS,
) -> McpAuthContext:
    """An `McpAuthContext` over in-memory doubles.

    The context is the production value object and `resolve_mcp_identity` is the production
    function; only the session, the two repositories and the directory are faked.
    """

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield cast("AsyncSession", StubSession())

    limits = rate_limits if rate_limits is not None else FakeRateLimitRepository()
    return McpAuthContext(
        session_factory=session_factory,
        directory=directory or FakeDirectory(),
        ldap_revalidate_seconds=ldap_revalidate_seconds,
        rate_limit_window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        rate_limit_max_attempts=max_attempts,
        rate_limit_block_seconds=RATE_LIMIT_BLOCK_SECONDS,
        identity_repository_factory=lambda _session: repository,
        rate_limit_repository_factory=lambda _session: limits,
    )


__all__ = [
    "DISPLAY_NAME",
    "EMAIL",
    "LIBRECHAT_USER",
    "NOW",
    "OTHER_LIBRECHAT_USER",
    "RATE_LIMIT_BLOCK_SECONDS",
    "RATE_LIMIT_MAX_ATTEMPTS",
    "RATE_LIMIT_WINDOW_SECONDS",
    "REVALIDATE_SECONDS",
    "FakeAuthRow",
    "FakeDirectory",
    "FakeMcpIdentityRepository",
    "IdentityFixture",
    "StoredAuthToken",
    "StubSession",
    "authenticated_caller",
    "build_auth_context",
    "build_resolver",
    "http_request_context",
    "stale_check",
]
