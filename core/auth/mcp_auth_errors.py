"""MCP authentication denial taxonomy (T11, C5, C20, V2, V3, V4).

A fourth taxonomy alongside `core.auth.errors` (session login), `core.auth.errors`'
authorization sibling `core.auth.authorization_errors` (RBAC), and
`core.auth.mcp_token_errors` (token CRUD). The split against that last one is the one worth
justifying, because both modules are about `mcp_tokens`:

- `mcp_token_errors` answers *"does this row exist for this admin operation?"* — raised by
  mint/list/revoke (T10), read by the admin panel.
- this module answers *"may this presented bearer act right now?"* — raised on the MCP
  request path, one class per distinguishable denial.

Folding them together would put a 404 about a revoke request in the same tree as a 401
about a stolen credential, and the subclass-tree test in `test_mcp_token_service.py` (which
asserts every `McpTokenError` maps to 400 or 404) would have to be loosened to allow 401.

V3 fixes two of these strings verbatim — `librechat_user_header_missing` and
`librechat_user_mismatch` — so a client can tell "you forgot the header" from "this token
belongs to someone else's LibreChat account". The rest follow the same one-code-per-cause
rule, because a single `mcp_unauthorized` would make an expired token and a mismatched
binding indistinguishable in the logs, and those two have completely different remedies.

Deliberately NOT here: `LdapUnavailableError` (`core.auth.errors`). V4 says a directory
outage fails closed *without* concluding anything about the user, and that class already
carries the retry-friendly message and the 503 mapping. Inventing an MCP-flavoured copy
would be two classes for one condition (V66).

Statuses live in `noa_api.api.errors`. 401 for "this credential does not authenticate you",
403 for "it does, and you still may not act" — re-presenting the token changes nothing for
a disabled operator or one the directory has dropped.

V2/V8 throughout: no message and no `detail` in this module carries a token plaintext, a
digest, or a prefix. `detail` names ids and reasons, and stays in the logs.

Who raises what: `core.auth.mcp_identity.McpIdentityResolver`, in gate order. Who renders
it: nobody yet — `verify_token` (T11) turns every one of these into `None`, because the
fastmcp/SDK contract has no body hook (R2). T12's `resolve_mcp_identity` is the row that
puts these codes in a 401 body; the classes exist now so the causes are already
distinguishable rather than collapsed at the point they are decided.
"""

from __future__ import annotations

from core.errors import NoaError


class McpAuthError(NoaError):
    """Base for every MCP request-path authentication denial.

    Mapped to 400 rather than left to the 503 fallback: an unclassified denial is a problem
    with the request, not a report that NOA is down. A test asserts every subclass is mapped
    explicitly, so reaching this entry means a new class arrived without a decision.
    """

    error_code: str = "mcp_auth_failed"
    message: str = "This MCP request could not be authenticated."


class McpTokenMissingError(McpAuthError):
    """No bearer token on the request, or a blank one.

    Distinct from `McpTokenInvalidError` because the remedy differs: this one means
    `NOA_MCP_TOKEN` is unset in LibreChat's `customUserVars`, not that the credential was
    rejected. It leaks nothing — the caller already knows they sent no token.
    """

    error_code: str = "mcp_token_missing"
    message: str = "No NOA MCP token was presented. Set `NOA_MCP_TOKEN` in LibreChat."


class McpTokenInvalidError(McpAuthError):
    """No `mcp_tokens` row for the presented digest (V2).

    One message for "never existed", "mistyped" and "revoked": revocation is a row delete
    (V2), so after the fact NOA genuinely cannot tell them apart, and a message that
    guessed would be wrong some of the time.
    """

    error_code: str = "mcp_token_invalid"
    message: str = "That NOA MCP token is not valid. Mint a new one in the NOA admin panel."


class McpTokenExpiredError(McpAuthError):
    """The row exists but `expires_at` has passed (C5).

    Split from `McpTokenInvalidError` so an operator whose token simply aged out is told to
    re-mint rather than sent hunting for a typo. Only reachable when
    `MCP_TOKEN_TTL_SECONDS` is configured — the default mints non-expiring tokens (V2).
    """

    error_code: str = "mcp_token_expired"
    message: str = "That NOA MCP token has expired. Mint a new one in the NOA admin panel."


class LibreChatUserHeaderMissingError(McpAuthError):
    """`X-Noa-LibreChat-User` absent (C20, C24, V3).

    Required for bound *and* unbound tokens: C24 makes LibreChat the sole client, so a
    request without the header is not a client NOA supports rather than a client that has
    not bound yet. Code string fixed by V3.
    """

    error_code: str = "librechat_user_header_missing"
    message: str = (
        "This MCP token may only be used from LibreChat. The identifying header was absent."
    )


class LibreChatUserMismatchError(McpAuthError):
    """Token bound to a different `librechat_user_id` (C20, V3).

    The TOFU failure that matters: the token authenticated, but it is pinned to another
    LibreChat account, which is what a copied credential looks like. Code string fixed by
    V3. The message names neither the bound value nor the presented one — that comparison
    belongs in the logs, not in an answer to whoever presented it.
    """

    error_code: str = "librechat_user_mismatch"
    message: str = (
        "This MCP token is bound to a different LibreChat account. Mint your own in the "
        "NOA admin panel."
    )


class McpUserInactiveError(McpAuthError):
    """`users.is_active` is False (V1, V11).

    403, not 401: the credential is genuine and re-presenting it changes nothing. Same
    condition the session path reports as `user_pending_approval`, but with its own code —
    an MCP client cannot act on advice about signing in to the admin panel, and collapsing
    the two would make the audit trail unable to say which surface was refused.
    """

    error_code: str = "mcp_user_inactive"
    message: str = "This NOA account is not active. Ask a NOA admin to enable it."


class McpUserNotInDirectoryError(McpAuthError):
    """LDAP no longer has the operator, or has them disabled (C4, V4).

    Employment ended, so this is terminal and NOA cannot override it. Raised only *after*
    the cascade revoke has committed, so the message is accurate: by the time anyone reads
    it, the tokens really are gone.
    """

    error_code: str = "mcp_user_not_in_directory"
    message: str = (
        "This account is no longer active in the company directory, so its NOA MCP tokens "
        "were revoked. Contact IT if you believe this is a mistake."
    )


__all__ = [
    "LibreChatUserHeaderMissingError",
    "LibreChatUserMismatchError",
    "McpAuthError",
    "McpTokenExpiredError",
    "McpTokenInvalidError",
    "McpTokenMissingError",
    "McpUserInactiveError",
    "McpUserNotInDirectoryError",
]
