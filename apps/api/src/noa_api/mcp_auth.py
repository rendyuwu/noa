"""FastMCP bearer verification (T11 — C5, C20, V2, V3, V4, R1-R5).

The adapter between fastmcp's auth contract and `core.auth.mcp_identity`. Everything that
decides *whether* a caller may act lives in the resolver; this file only knows how a token
and a header reach it, and how an accepted identity is shaped for the SDK. V5 wants one
identity-resolution function, so this class holds no repository, runs no query, and makes
no policy decision of its own — swap fastmcp for something else and this is the file that
changes.

Verified against the installed `fastmcp==3.4.5` and `mcp==1.29.0` (C23), not against docs:

- `TokenVerifier.verify_token(self, token) -> AccessToken | None`, `None` = reject (R1).
  There is no hook for a response body here: `BearerAuthBackend.authenticate` turns `None`
  into a bare 401 (`mcp/server/auth/middleware/bearer_auth.py`). So T11 makes the causes
  *distinguishable* — one error class each, logged — and T12's `resolve_mcp_identity` is
  the row that puts those codes in a 401 body (V3).
- `verify_token` receives the token string only, but `RequestContextMiddleware` is inserted
  outermost, ahead of `AuthenticationMiddleware` (`fastmcp/server/http.py`), so
  `get_http_headers()` works inside it (R4). `test_mcp_token_verifier.py` proves that in
  *our* wiring rather than trusting the source read.
- Custom headers come back lowercased and are not on the exclusion list, so
  `x-noa-librechat-user` needs no `include=` (R5). The bearer arrives as the argument, so
  nothing here re-parses `authorization` — the caveat that `get_http_headers()` strips it
  by default never bites us.

The session: this runs outside FastAPI's dependency graph, so there is no request session
to join. The verifier holds the factory and opens one session per verification, which the
resolver commits. That session is closed before the tool call it authenticates begins, so
an MCP request's own database work never inherits a transaction from its authentication.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Final, Protocol

import structlog
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_http_headers
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.errors import LdapUnavailableError
from core.auth.mcp_auth_errors import McpAuthError
from core.auth.mcp_identity import (
    LIBRECHAT_USER_HEADER,
    DirectoryPresence,
    McpIdentity,
    McpIdentityRepository,
    McpIdentityResolver,
)
from core.auth.mcp_token_repository import SQLMcpIdentityRepository
from core.auth.mcp_token_service import hash_mcp_token

# One structured event for every refusal, so a query on this name shows the whole denial
# stream and `error_code` says which gate closed (V8: nothing else in the payload).
LOG_DENIED: Final = "mcp_auth_denied"

logger = structlog.get_logger(__name__)


class McpSessionFactory(Protocol):
    """What the verifier needs from `async_sessionmaker`: call it, get a session.

    A Protocol rather than the concrete `async_sessionmaker[AsyncSession]` so the adapter
    can be exercised without Postgres. That matters here specifically: the R4 claim — that
    `get_http_headers()` works *inside* `verify_token` — is the one thing in this file no
    source read can settle for our wiring, and a check that skips when Docker is absent is
    a check that stops running.
    """

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


class NoaTokenVerifier(TokenVerifier):
    """Resolve a NOA-minted bearer into a fastmcp `AccessToken` (R1, R2).

    Constructed once at startup and passed as `FastMCP(..., auth=<instance>)` — keyword
    only, the instance directly, with no provider wrapper, because `TokenVerifier` is
    already an `AuthProvider` (R3). T13 does that wiring; T11 ships the class.

    No `base_url` and no `required_scopes`: those drive RFC 9728 protected-resource
    metadata routes, and C5 puts NOA on per-user minted tokens rather than OAuth. Scopes
    stay empty for the reason in `_to_access_token`.
    """

    def __init__(
        self,
        *,
        session_factory: McpSessionFactory,
        directory: DirectoryPresence,
        ldap_revalidate_seconds: int,
        repository_factory: Callable[[AsyncSession], McpIdentityRepository] = (
            SQLMcpIdentityRepository
        ),
    ) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._directory = directory
        self._ldap_revalidate_seconds = ldap_revalidate_seconds
        # Production never passes this. It exists so the adapter's own behaviour — header
        # reading, `AccessToken` shaping, denial logging — is testable without a database,
        # which is what keeps the R4 check in the suite that always runs.
        self._repository_factory = repository_factory

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify one bearer. `None` rejects (R1).

        Every refusal is one `McpAuthError` (or `LdapUnavailableError`, V4's fail-closed
        answer) caught here and logged with its code. Nothing else is caught: a broken
        database or a bug in the resolver must surface as a 500, because answering 401 to
        an infrastructure fault would tell an operator their token is bad and send them
        re-minting a credential that was fine.
        """
        librechat_user_id = get_http_headers().get(LIBRECHAT_USER_HEADER)

        async with self._session_factory() as session:
            resolver = McpIdentityResolver(
                repository=self._repository_factory(session),
                directory=self._directory,
                ldap_revalidate_seconds=self._ldap_revalidate_seconds,
            )
            try:
                identity = await resolver.resolve(
                    presented_token=token, librechat_user_id=librechat_user_id
                )
            except (McpAuthError, LdapUnavailableError) as exc:
                self._log_denial(exc)
                return None

        return self._to_access_token(identity, token)

    # --- Internals ---

    @staticmethod
    def _log_denial(error: McpAuthError | LdapUnavailableError) -> None:
        """Record one refusal (V8).

        `error_code` and the internal `detail` only. No token, no digest, no prefix, and no
        LibreChat identifier — a mismatch line pairing a NOA token with a LibreChat account
        would be a map between the two systems sitting in the log.
        """
        logger.warning(LOG_DENIED, error_code=error.error_code, detail=error.detail)

    @staticmethod
    def _to_access_token(identity: McpIdentity, presented_token: str) -> AccessToken:
        """Shape the accepted identity for the SDK (R2).

        Four choices worth naming:

        - **`token` carries the digest, not the plaintext.** `AccessToken` is a pydantic
          model that renders in tracebacks, in structlog values and in any debug dump of
          the request scope; holding the live credential there would put it one exception
          away from a log file (V2 "⊥ logged", V8). Nothing on a resource-server path reads
          `.token` — only the OAuth-proxy providers and a cache-key hash do — so the digest
          costs nothing and the identity is still uniquely keyed.
        - **`scopes` stays empty.** RBAC is not OAuth scope. Putting the permitted tools
          here would create a second authorization source that lives for the connection,
          and V1/V74 require the execution-time re-check to be the authority — a cached
          scope list is exactly the stale catalog V74 refuses to trust.
        - **`client_id` and `subject` are per user, not per token.**
          `streamable_http_manager` pins an `Mcp-Session-Id` to the principal that created
          it and 404s anything else (`authorization_context`), so keying on the token id
          would kill a live session the moment an operator rotated their credential.
        - **`expires_at` is passed through** so the SDK enforces it too (R2). The resolver
          already refused an expired token; this is the belt to that braces, and it costs
          one integer.

        `claims` carries what a tool needs from `TokenClaim`/`get_access_token()` (R5) and
        nothing sensitive: ids, the email the audit trail already records, and the binding.
        """
        return AccessToken(
            token=hash_mcp_token(presented_token),
            client_id=str(identity.user_id),
            subject=str(identity.user_id),
            scopes=[],
            expires_at=(
                int(identity.expires_at.timestamp()) if identity.expires_at is not None else None
            ),
            claims={
                "user_id": str(identity.user_id),
                "email": identity.email,
                "token_id": str(identity.token_id),
                "librechat_user_id": identity.librechat_user_id,
            },
        )


__all__ = ["LOG_DENIED", "NoaTokenVerifier"]
