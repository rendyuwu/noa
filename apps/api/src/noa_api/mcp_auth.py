"""FastMCP bearer verification (T11, T12 — C5, C20, V2, V3, V4, V5, R1-R5).

The adapter between fastmcp's auth contract and NOA's request-path authentication.
Everything that decides *whether* a caller may act lives in `core.auth.mcp_identity`;
everything about how a request reaches that decision lives in `noa_api.mcp_request_auth`.
What is left here is only what fastmcp specifically requires: the `TokenVerifier` subclass,
and the `AccessToken` an accepted identity becomes. V5 wants one identity-resolution path,
so this class holds no repository, opens no session, reads no header and makes no policy
decision — swap fastmcp for something else and this is the file that changes.

Verified against the installed `fastmcp==3.4.5` and `mcp==1.29.0` (C23), not against docs:

- `TokenVerifier.verify_token(self, token) -> AccessToken | None`, `None` = reject (R1).
  There is no hook for a response body here: `BearerAuthBackend.authenticate` turns `None`
  into a bare 401 (`mcp/server/auth/middleware/bearer_auth.py`). So the refusal is stashed
  on the request scope and `McpAuthErrorMiddleware` (T12) renders it with its code (V3).
- `verify_token` receives the token string only, but `RequestContextMiddleware` is inserted
  outermost, ahead of `AuthenticationMiddleware` (`fastmcp/server/http.py`), so
  `get_http_headers()` works inside it (R4). `test_mcp_token_verifier.py` proves that in
  *our* wiring rather than trusting the source read.
- The token arrives as the argument, so nothing here re-parses `authorization`; it is
  forwarded to `resolve_mcp_identity`, which would otherwise read the header itself and
  produce a second parse that could disagree with the SDK's.

The session: this runs outside FastAPI's dependency graph, so there is no request session
to join. `resolve_mcp_identity` opens one per verification from the factory on
`McpAuthContext` and commits through it. That session is closed before the tool call it
authenticates begins, so an MCP request's own database work never inherits a transaction
from its authentication.
"""

from __future__ import annotations

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken

from core.auth.errors import LdapUnavailableError
from core.auth.mcp_auth_errors import McpAuthError
from core.auth.mcp_identity import McpIdentity
from core.auth.mcp_token_service import hash_mcp_token
from noa_api.mcp_request_auth import (
    LOG_DENIED,
    McpAuthContext,
    identity_claims,
    remember_mcp_auth_error,
    resolve_mcp_identity,
)

__all__ = ["LOG_DENIED", "NoaTokenVerifier"]


class NoaTokenVerifier(TokenVerifier):
    """Resolve a NOA-minted bearer into a fastmcp `AccessToken` (R1, R2).

    Constructed once at startup and passed as `FastMCP(..., auth=<instance>)` — keyword
    only, the instance directly, with no provider wrapper, because `TokenVerifier` is
    already an `AuthProvider` (R3). T13 does that wiring; T11 shipped the class.

    No `base_url` and no `required_scopes`: those drive RFC 9728 protected-resource
    metadata routes, and C5 puts NOA on per-user minted tokens rather than OAuth. Scopes
    stay empty for the reason in `_to_access_token`.
    """

    def __init__(self, *, context: McpAuthContext) -> None:
        super().__init__()
        self._context = context

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify one bearer. `None` rejects (R1).

        Every refusal is one `McpAuthError` (or `LdapUnavailableError`, V4's fail-closed
        answer) caught here and left on the request scope for the middleware to name.
        Nothing else is caught: a broken database or a bug in the resolver must surface as a
        500, because answering 401 to an infrastructure fault would tell an operator their
        token is bad and send them re-minting a credential that was fine.

        The refusal is not logged here — `resolve_mcp_identity` logs at the point the cause
        is known, so a second log line here would double every denial in the stream.
        """
        try:
            identity = await resolve_mcp_identity(self._context, presented_token=token)
        except (McpAuthError, LdapUnavailableError) as exc:
            remember_mcp_auth_error(exc)
            return None

        return self._to_access_token(identity, token)

    # --- Internals ---

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

        `claims` comes from `identity_claims` rather than a dict literal here, so the keys
        a tool reads through `current_mcp_identity` and the keys written here are the same
        constants (R5).
        """
        return AccessToken(
            token=hash_mcp_token(presented_token),
            client_id=str(identity.user_id),
            subject=str(identity.user_id),
            scopes=[],
            expires_at=(
                int(identity.expires_at.timestamp()) if identity.expires_at is not None else None
            ),
            claims=identity_claims(identity),
        )
