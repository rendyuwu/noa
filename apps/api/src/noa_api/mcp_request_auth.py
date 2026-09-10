"""The MCP request-path authentication entry point.

`core.auth.mcp_identity` decides *whether* a caller may act. This module is how an HTTP
request reaches that decision, and how a refusal reaches the client. Three jobs, and each
one exists because something in the fastmcp/SDK stack does not do it:

1. **`resolve_mcp_identity` — the one HTTP-side entry.** It reads the two headers,
   consults the rate limiter, calls `McpIdentityResolver.resolve()`, and logs the outcome.
   Every MCP request passes through it exactly once, via `NoaTokenVerifier.verify_token`.

2. **`McpAuthErrorMiddleware` — the named body.** `TokenVerifier.verify_token` has no
   response hook: returning `None` makes `RequireAuthMiddleware` answer `{"error": "invalid_token",
   "error_description": "Authentication required"}` (`mcp/server/auth/middleware/bearer_auth.py`),
   which cannot tell "you forgot the header" from "this token belongs to someone else's LibreChat
   account" — the two cases the named 401 bodies separate, each with its own client-visible string.
   So the refusal is stashed on the ASGI scope and this middleware renders it through the same
   `status_for`/`error_body`/`error_headers` the FastAPI handler uses.

3. **`current_mcp_identity` — identity for tools.** Tools read
   `get_access_token().claims`, never the bearer again. The claim keys are constants here
   and `identity_claims()` is what writes them, so the writer and the reader cannot drift.

**Why the middleware, and where it sits.** Verified against the installed
`fastmcp==3.4.5` rather than docs — `create_base_app` inserts `RequestContextMiddleware`
at position 0 and `create_streamable_http_app` *appends* caller middleware after the auth
middleware (`fastmcp/server/http.py`), so the stack per request is:

    RequestContextMiddleware          sets the request contextvar
    └─ AuthenticationMiddleware       BearerAuthBackend → verify_token → resolve_mcp_identity
       └─ McpAuthErrorMiddleware      this module: renders the named refusal
          └─ router → Route(endpoint=RequireAuthMiddleware)   the bare 401 we replace
             └─ StreamableHTTPASGIApp

That position is the point. Resolution has already happened by the time this runs, so the
refusal is *read*, never recomputed — a middleware placed outside the auth middleware would
have to resolve the token itself, and two resolutions per request would mean two LDAP calls
and, on a first call, a TOFU bind performed by whichever one ran first.

**Why it still reads the `Authorization` header itself.** `BearerAuthBackend.authenticate`
returns `None` *without calling* `verify_token` when the header is absent or not `Bearer `,
so `mcp_token_missing` is unreachable from inside the verifier. The read goes through
`get_http_headers(include={"authorization"})`: verified against the installed SDK, the default
exclusion list
strips `authorization` (and `mcp-session-id`), so the obvious spelling silently returns
nothing and every request would look like a missing token.

The identity resolver mounts nothing. `build_mcp_auth_context` is the production wiring the
FastMCP mount calls; until then
`/mcp` is unreachable and the execution-time permission re-check holds trivially.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

import structlog
from fastapi import status
from fastmcp.server.dependencies import get_access_token, get_http_headers, get_http_request
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from core.auth.attempt_limiter import AttemptLimitRepository
from core.auth.auth_repository import SQLLoginRateLimitRepository
from core.auth.errors import LdapUnavailableError
from core.auth.mcp_auth_errors import (
    McpAuthError,
    McpTokenExpiredError,
    McpTokenInvalidError,
    McpTokenMissingError,
)
from core.auth.mcp_auth_rate_limiter import McpAuthRateLimiter, counts_against_limit
from core.auth.mcp_identity import (
    DETAIL_NO_BEARER,
    LIBRECHAT_USER_HEADER,
    DirectoryPresence,
    McpIdentity,
    McpIdentityRepository,
    McpIdentityResolver,
)
from core.auth.mcp_token_repository import SQLMcpIdentityRepository
from core.auth.mcp_token_service import hash_mcp_token
from core.config import Settings
from core.errors import NoaError
from noa_api.api.errors import error_body, error_headers, status_for
from noa_api.api.request_context import REQUEST_ID_HEADER, request_id_for

# One structured event for every refusal, so a query on this name shows the whole denial
# stream and `error_code` says which gate closed (the envelope shape: nothing else in the payload).
LOG_DENIED: Final = "mcp_auth_denied"

AUTHORIZATION_HEADER: Final = "authorization"
BEARER_SCHEME: Final = "bearer"

# Where a refusal waits between `verify_token` and the middleware. On the ASGI scope, not a
# contextvar: the scope is unambiguously one request's, so a stash cannot outlive its
# request or be read by a concurrent one.
SCOPE_AUTH_ERROR: Final = "noa_mcp_auth_error"

# `AccessToken.claims` keys. Written by `identity_claims`, read by `current_mcp_identity`;
# named constants because a typo on either side would be an authenticated request whose
# tool cannot tell who is calling.
CLAIM_USER_ID: Final = "user_id"
CLAIM_EMAIL: Final = "email"
# S105: a claim *key* name, not a credential — the `TOKEN` in the constant name trips it.
CLAIM_TOKEN_ID: Final = "token_id"  # noqa: S105
CLAIM_LIBRECHAT_USER_ID: Final = "librechat_user_id"

# Internal diagnostics for the `detail` slot: logs only, never a response body.
DETAIL_NO_ACCESS_TOKEN = "no access token in context; called off an authenticated request"  # noqa: S105
DETAIL_CLAIMS_MALFORMED = "access token claims do not carry a usable NOA identity"

logger = structlog.get_logger(__name__)


class McpSessionFactory(Protocol):
    """What this path needs from `async_sessionmaker`: call it, get a session.

    A Protocol rather than the concrete `async_sessionmaker[AsyncSession]` so the whole
    request path can be exercised without Postgres. That matters specifically here: the
    claim — that `get_http_headers()` works *inside* `verify_token` — is the one thing in
    this area no source read can settle for our wiring, and a check that skips when Docker
    is absent is a check that stops running.
    """

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


@dataclass(frozen=True)
class McpAuthContext:
    """Everything `resolve_mcp_identity` needs, built once at startup.

    A value object rather than parameters on the verifier: the FastMCP mount wires one of these into
    `NoaTokenVerifier`, and a test builds one over doubles. Both repository factories are
    injectable for that reason, and only for that reason — production never passes them.

    The two repositories are handed the *same* session per request, so a recorded
    rate-limit failure and any identity write commit as one unit of work.
    """

    session_factory: McpSessionFactory
    directory: DirectoryPresence
    ldap_revalidate_seconds: int
    rate_limit_window_seconds: int
    rate_limit_max_attempts: int
    rate_limit_block_seconds: int
    identity_repository_factory: Callable[[AsyncSession], McpIdentityRepository] = (
        SQLMcpIdentityRepository
    )
    rate_limit_repository_factory: Callable[[AsyncSession], AttemptLimitRepository] = (
        SQLLoginRateLimitRepository
    )


def build_mcp_auth_context(
    *,
    session_factory: McpSessionFactory,
    directory: DirectoryPresence,
    settings: Settings,
) -> McpAuthContext:
    """Production wiring, from `Settings` (the FastMCP mount calls this in the app lifespan)."""
    return McpAuthContext(
        session_factory=session_factory,
        directory=directory,
        ldap_revalidate_seconds=settings.mcp_token_ldap_revalidate_seconds,
        rate_limit_window_seconds=settings.mcp_auth_rate_limit_window_seconds,
        rate_limit_max_attempts=settings.mcp_auth_rate_limit_max_attempts,
        rate_limit_block_seconds=settings.mcp_auth_rate_limit_block_seconds,
    )


# --- Header reads ---


def parse_bearer(header_value: str | None) -> str | None:
    """The token out of an `Authorization` value, or `None` if there is not one.

    Scheme compared case-insensitively (RFC 7235 makes it case-insensitive, and
    `BearerAuthBackend` does the same), and a blank credential counts as absent — a client
    that failed to interpolate `{{NOA_MCP_TOKEN}}` sends `Bearer ` with nothing after it,
    which is a missing token, not an invalid one.
    """
    if not header_value:
        return None

    scheme, _, credential = header_value.partition(" ")
    if scheme.strip().lower() != BEARER_SCHEME:
        return None
    return credential.strip() or None


def read_presented_bearer() -> str | None:
    """The bearer on the current request, read through fastmcp's request context.

    `include={"authorization"}` is load-bearing: the default exclusion list drops
    `authorization`, so without it this returns `None` on every request and every caller
    looks like it presented no token. Off-request `get_http_headers()` answers `{}`, which
    reads as "absent" rather than raising.
    """
    return parse_bearer(get_http_headers(include={AUTHORIZATION_HEADER}).get(AUTHORIZATION_HEADER))


def read_librechat_user() -> str | None:
    """The `X-Noa-LibreChat-User` value on the current request.

    No `include=` needed: custom headers are not on the exclusion list, and they come back
    lowercased, which is why `LIBRECHAT_USER_HEADER` is spelled lowercase.
    """
    return get_http_headers().get(LIBRECHAT_USER_HEADER)


# --- Resolution ---


async def resolve_mcp_identity(
    context: McpAuthContext, *, presented_token: str | None = None
) -> McpIdentity:
    """Resolve the caller behind this request's bearer, or raise a named refusal.

    The function the single identity resolver's rule names on the HTTP side:
    `NoaTokenVerifier.verify_token` is its only caller, so there is exactly one place a presented
    bearer becomes a NOA user per request, and swapping the auth mechanism is a change to this file
    plus `core.auth.mcp_identity`.

    `presented_token` exists because `verify_token` is *handed* the token by the SDK and
    re-parsing the header there would be a second parse that could disagree with the first.
    Omitted, the header is read — that is the path the middleware needs when the SDK
    never called the verifier at all.

    Raises `McpTokenMissingError` before touching the database, then `McpAuthRateLimitedError`, then
    whatever `McpIdentityResolver.resolve` raises in gate order, plus `LdapUnavailableError`
    straight through (the LDAP-staleness rule — deny, do not revoke). Every refusal is logged once,
    here, because this is where the cause is known.
    """
    bearer = (presented_token if presented_token is not None else read_presented_bearer()) or ""
    bearer = bearer.strip()
    librechat_user_id = read_librechat_user()

    if not bearer:
        # No database work for a request that carries no credential: it cannot be a guess,
        # and a limiter bucket for it would collect every misconfigured client into one
        # counter (see `core.auth.mcp_auth_rate_limiter`).
        missing = McpTokenMissingError(DETAIL_NO_BEARER)
        log_mcp_auth_denial(missing)
        raise missing

    # The same digest `mint()` stored and the resolver will look up. Computed once and
    # reused as the limiter's token key, so a stolen token is counted under the value it
    # will keep presenting.
    token_digest = hash_mcp_token(bearer)

    async with context.session_factory() as session:
        identity_repository = context.identity_repository_factory(session)
        limiter = McpAuthRateLimiter(
            context.rate_limit_repository_factory(session),
            window_seconds=context.rate_limit_window_seconds,
            max_attempts=context.rate_limit_max_attempts,
            block_seconds=context.rate_limit_block_seconds,
        )

        try:
            await limiter.assert_allowed(
                librechat_user_id=librechat_user_id, token_digest=token_digest
            )
            resolver = McpIdentityResolver(
                repository=identity_repository,
                directory=context.directory,
                ldap_revalidate_seconds=context.ldap_revalidate_seconds,
            )
            identity = await resolver.resolve(
                presented_token=bearer, librechat_user_id=librechat_user_id
            )
        except (McpAuthError, LdapUnavailableError) as exc:
            if counts_against_limit(exc):
                await limiter.record_failure(
                    librechat_user_id=librechat_user_id, token_digest=token_digest
                )
                # Both repositories were handed this request's session, so committing
                # through the identity repository (the Protocol that declares `commit`)
                # makes the counter durable. A counter that rolls back with the refusal is
                # a counter that never counted.
                await identity_repository.commit()
            log_mcp_auth_denial(exc)
            raise

    return identity


def identity_claims(identity: McpIdentity) -> dict[str, str]:
    """`AccessToken.claims` for an accepted identity — what tools read.

    Ids, the email the audit trail already records, and the binding. Nothing sensitive and
    no token material: `claims` renders in tracebacks and structlog values.
    """
    return {
        CLAIM_USER_ID: str(identity.user_id),
        CLAIM_EMAIL: identity.email,
        CLAIM_TOKEN_ID: str(identity.token_id),
        CLAIM_LIBRECHAT_USER_ID: identity.librechat_user_id,
    }


@dataclass(frozen=True)
class McpToolIdentity:
    """Who is calling, as a tool sees it.

    Deliberately not `McpIdentity`: that one is the resolver's output and carries
    `expires_at`, which a tool has no business branching on — the gates already ran.
    """

    user_id: UUID
    email: str
    token_id: UUID
    librechat_user_id: str


def current_mcp_identity() -> McpToolIdentity:
    """The caller behind the current tool invocation.

    Read from `get_access_token()`, never by re-parsing the bearer: the token was already
    resolved once this request, and a second parse would be a second answer to "who is
    this?" that could disagree with the one the RBAC check used.

    Raises rather than returning `None`. A tool reached without an access token means the
    mount lost its verifier, and every tool would otherwise need a branch for a state that
    must not exist.
    """
    access_token = get_access_token()
    if access_token is None:
        raise McpTokenMissingError(DETAIL_NO_ACCESS_TOKEN)

    claims = access_token.claims or {}
    try:
        return McpToolIdentity(
            user_id=UUID(str(claims[CLAIM_USER_ID])),
            email=str(claims[CLAIM_EMAIL]),
            token_id=UUID(str(claims[CLAIM_TOKEN_ID])),
            librechat_user_id=str(claims[CLAIM_LIBRECHAT_USER_ID]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # A verifier that shaped `claims` differently, or an access token minted by
        # something else entirely. Refuse rather than serve a tool a half-known caller.
        raise McpTokenInvalidError(DETAIL_CLAIMS_MALFORMED) from exc


# --- The refusal stash ---


def remember_mcp_auth_error(error: NoaError) -> None:
    """Leave `error` on the request scope for `McpAuthErrorMiddleware` to render.

    Silent no-op off-request: `verify_token` is also reachable from tests and from
    non-HTTP transports, and there a stash has nowhere to go and nobody to read it.
    """
    try:
        request = get_http_request()
    except RuntimeError:
        return
    request.scope[SCOPE_AUTH_ERROR] = error


def take_mcp_auth_error(scope: Scope) -> NoaError | None:
    """Pop the stashed refusal. Popped, not read: one refusal renders once."""
    error = scope.pop(SCOPE_AUTH_ERROR, None)
    return error if isinstance(error, NoaError) else None


def log_mcp_auth_denial(error: NoaError) -> None:
    """Record one refusal.

    `error_code` and the internal `detail` only. No token, no digest, no prefix, and no
    LibreChat identifier — a mismatch line pairing a NOA token with a LibreChat account
    would be a map between the two systems sitting in the log.
    """
    logger.warning(LOG_DENIED, error_code=error.error_code, detail=error.detail)


# --- Named refusal responses ---


def mount_relative_path(scope: Scope) -> str:
    """The request path as the app this middleware runs in sees it (ASGI `root_path`).

    Starlette's `Mount` does **not** rewrite `scope["path"]`; it extends `root_path` with
    the matched prefix and leaves the full path in place, and every router below strips
    `root_path` again when it matches. So under `app.mount("/mcp", mcp_app)` a request
    to `/mcp/` arrives here as `path="/mcp/"`, `root_path="/mcp"` — comparing `scope["path"]`
    to the sub-app's own `/` would never match, the refusal would pass straight through, and
    the named 401 bodies would silently become the SDK's bare `invalid_token` while the status
    code stayed 401. That failure has no symptom other than the body, which is why it is
    computed here rather than assumed.

    Deliberately not `starlette._utils.get_route_path`, whose behaviour this mirrors: it is
    a private module, and this is four lines of ASGI spec.
    """
    path: str = scope.get("path", "")
    root_path: str = scope.get("root_path", "")

    if not root_path or not path.startswith(root_path):
        return path
    # A path equal to its mount prefix (`/mcp` exactly) leaves nothing behind; Starlette
    # answers that with a redirect to the trailing-slash form before any of this runs.
    return path[len(root_path) :]


class McpAuthErrorMiddleware:
    """Replace the SDK's bare 401 with a NOA error envelope.

    Runs after the auth middleware and before the route, so an unauthenticated request is
    answered here instead of by `RequireAuthMiddleware`. Nothing is re-resolved: the cause
    was decided in `resolve_mcp_identity` and left on the scope.

    Every unauthenticated request has a cause, and there are exactly three sources for it:

    - a stashed error — `verify_token` ran and refused;
    - no stash but a bearer was presented — the SDK rejected the `AccessToken` itself, which
      it does only for `expires_at` in the past (`BearerAuthBackend.authenticate`), a check
      that bypasses our code entirely;
    - no stash and no bearer — `BearerAuthBackend` never called the verifier.

    `mcp_path` bounds the interception to the MCP endpoint (`http_app(path=...)`), so a
    public route added to the same sub-app keeps answering for itself. It is compared
    against the *mount-relative* path, which is the same value the sub-app's own router
    matches on — see `mount_relative_path`.
    """

    def __init__(self, app: ASGIApp, *, mcp_path: str = "/") -> None:
        self.app = app
        self._mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or mount_relative_path(scope) != self._mcp_path:
            await self.app(scope, receive, send)
            return

        error = take_mcp_auth_error(scope)
        if isinstance(scope.get("user"), AuthenticatedUser):
            await self.app(scope, receive, send)
            return

        await self._send_error(
            send, error or self._infer_cause(scope), request_id=request_id_for(scope)
        )

    # --- Internals ---

    @staticmethod
    def _infer_cause(scope: Scope) -> NoaError:
        """The refusal for a request `verify_token` never got to judge.

        Read off the scope rather than through `get_http_headers()`: the middleware holds
        the scope already, and going via the contextvar would make this depend on
        `RequestContextMiddleware` still being outermost.
        """
        headers: Mapping[str, str] = Headers(scope=scope)
        if parse_bearer(headers.get(AUTHORIZATION_HEADER)) is not None:
            # A token that resolved and was then rejected on `expires_at` by the SDK.
            error: NoaError = McpTokenExpiredError("token rejected on expiry by the SDK backend")
        else:
            error = McpTokenMissingError(DETAIL_NO_BEARER)
        log_mcp_auth_denial(error)
        return error

    @staticmethod
    async def _send_error(send: Send, error: NoaError, *, request_id: str) -> None:
        """Write the envelope the FastAPI handler would have written.

        Raw ASGI because the mounted MCP app is a Starlette app with no NOA exception
        handler on it; `status_for`/`error_body`/`error_headers` are shared with
        `noa_api.api.errors` so the two surfaces cannot drift. `detail` stays out of the
        body, and `request_id` is in it for the same reason it is in every other error
        body: it is what an operator quotes, and it names the log line this refusal wrote.

        The header is written here too, not left to `RequestContextMiddleware`. That
        middleware does cover this response in the mounted app, but this method is the thing
        that promises the envelope, and a promise that depends on a middleware two apps up
        is one that a future standalone mount breaks silently.
        """
        status_code = status_for(error)
        body = json.dumps(error_body(error, request_id=request_id)).encode()

        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (REQUEST_ID_HEADER.encode(), request_id.encode()),
        ]
        headers.extend(
            (name.lower().encode(), value.encode()) for name, value in error_headers(error).items()
        )
        if status_code == status.HTTP_401_UNAUTHORIZED:
            # RFC 6750 section 3: a 401 owes the client a challenge. The SDK's own 401 sends one,
            # and dropping it would regress a spec-compliant client to guessing.
            challenge = f'Bearer error="invalid_token", error_description="{error.error_code}"'
            headers.append((b"www-authenticate", challenge.encode()))

        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "AUTHORIZATION_HEADER",
    "CLAIM_EMAIL",
    "CLAIM_LIBRECHAT_USER_ID",
    "CLAIM_TOKEN_ID",
    "CLAIM_USER_ID",
    "LOG_DENIED",
    "SCOPE_AUTH_ERROR",
    "McpAuthContext",
    "McpAuthErrorMiddleware",
    "McpSessionFactory",
    "McpToolIdentity",
    "build_mcp_auth_context",
    "current_mcp_identity",
    "identity_claims",
    "log_mcp_auth_denial",
    "mount_relative_path",
    "parse_bearer",
    "read_librechat_user",
    "read_presented_bearer",
    "remember_mcp_auth_error",
    "resolve_mcp_identity",
    "take_mcp_auth_error",
]
