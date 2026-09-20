"""MCP authentication denial taxonomy.

A fourth taxonomy alongside `core.auth.errors` (session login), `core.auth.errors`'
authorization sibling `core.auth.authorization_errors` (RBAC), and
`core.auth.mcp_token_errors` (token CRUD). The split against that last one is the one worth
justifying, because both modules are about `mcp_tokens`:

- `mcp_token_errors` answers *"does this row exist for this admin operation?"* — raised by
  mint/list/revoke, read by the admin panel.
- this module answers *"may this presented bearer act right now?"* — raised on the MCP
  request path, one class per distinguishable denial.

Folding them together would put a 404 about a revoke request in the same tree as a 401
about a stolen credential, and the tree table in `apps/api/tests/test_error_status_taxonomy.py`
(which pins every `McpTokenError` to 400 or 404) would have to be loosened to allow 401.

The named-401-body rule fixes two of these strings verbatim — `librechat_user_header_missing` and
`librechat_user_mismatch` — so a client can tell "you forgot the header" from "this token
belongs to someone else's LibreChat account". The rest follow the same one-code-per-cause
rule, because a single `mcp_unauthorized` would make an expired token and a mismatched
binding indistinguishable in the logs, and those two have completely different remedies.

Deliberately NOT here: `LdapUnavailableError` (`core.auth.errors`). The LDAP-revalidation rule
says a directory
outage fails closed *without* concluding anything about the user, and that class already
carries the retry-friendly message and the 503 mapping. Inventing an MCP-flavoured copy
would be two classes for one condition.

Each class carries its own `status_code`. 401 for "this credential does not authenticate you",
403 for "it does, and you still may not act" — re-presenting the token changes nothing for
a disabled operator or one the directory has dropped — and 429 for "stop asking", the one
answer that is about the caller's rate rather than their credential.

The token-scope and safe-payload rules throughout: no message and no `detail` in this module
carries a token plaintext, a digest, or a prefix. `detail` names ids and reasons, and stays
in the logs.

Who raises what: `core.auth.mcp_identity.McpIdentityResolver`, in gate order, plus
`McpAuthRateLimitedError` from `core.auth.mcp_auth_rate_limiter` before the gates run.
`verify_token` turns every one of these into `None`, because the fastmcp/SDK contract
has no body hook; `noa_api.mcp_request_auth.McpAuthErrorMiddleware` is what puts
the code in the response body.
"""

from __future__ import annotations

from core.errors import NoaError, RetryAfterMixin


class McpAuthError(NoaError):
    """Base for every MCP request-path authentication denial.

    Mapped to 400 rather than left to the 503 fallback: an unclassified denial is a problem
    with the request, not a report that NOA is down.
    `apps/api/tests/test_error_status_taxonomy.py` pins this tree to {400, 401, 403, 429}.
    """

    error_code: str = "mcp_auth_failed"
    message: str = "This MCP request could not be authenticated."
    # Bare `McpAuthError`: a request problem, not "NOA is down". The tree is pinned to
    # {400, 401, 403, 429}.
    status_code = 400


class McpTokenMissingError(McpAuthError):
    """No bearer token on the request, or a blank one.

    Distinct from `McpTokenInvalidError` because the remedy differs: this one means
    `NOA_MCP_TOKEN` is unset in LibreChat's `customUserVars`, not that the credential was
    rejected. It leaks nothing — the caller already knows they sent no token.
    """

    error_code: str = "mcp_token_missing"
    message: str = "No NOA MCP token was presented. Set `NOA_MCP_TOKEN` in LibreChat."
    status_code = 401


class McpTokenInvalidError(McpAuthError):
    """No `mcp_tokens` row for the presented digest.

    One message for "never existed", "mistyped" and "revoked": revocation is a row delete,
    so after the fact NOA genuinely cannot tell them apart, and a message that
    guessed would be wrong some of the time.
    """

    error_code: str = "mcp_token_invalid"
    message: str = "That NOA MCP token is not valid. Mint a new one in the NOA admin panel."
    status_code = 401


class McpTokenExpiredError(McpAuthError):
    """The row exists but `expires_at` has passed.

    Split from `McpTokenInvalidError` so an operator whose token simply aged out is told to
    re-mint rather than sent hunting for a typo. Only reachable when
    `MCP_TOKEN_TTL_SECONDS` is configured — the default mints non-expiring tokens.
    """

    error_code: str = "mcp_token_expired"
    message: str = "That NOA MCP token has expired. Mint a new one in the NOA admin panel."
    status_code = 401


class LibreChatUserHeaderMissingError(McpAuthError):
    """`X-Noa-LibreChat-User` absent.

    Required for bound *and* unbound tokens: LibreChat being the sole MCP client means a
    request without the header is not a client NOA supports rather than a client that has
    not bound yet. Code string fixed by the named-401-body rule.
    """

    error_code: str = "librechat_user_header_missing"
    message: str = (
        "This MCP token may only be used from LibreChat. The identifying header was absent."
    )
    status_code = 401


class LibreChatUserMismatchError(McpAuthError):
    """Token bound to a different `librechat_user_id`.

    The TOFU failure that matters: the token authenticated, but it is pinned to another
    LibreChat account, which is what a copied credential looks like. Code string fixed by
    the named-401-body rule. The message names neither the bound value nor the presented
    one — that comparison
    belongs in the logs, not in an answer to whoever presented it.
    """

    error_code: str = "librechat_user_mismatch"
    message: str = (
        "This MCP token is bound to a different LibreChat account. Mint your own in the "
        "NOA admin panel."
    )
    status_code = 401


class McpUserInactiveError(McpAuthError):
    """`users.is_active` is False.

    403, not 401: the credential is genuine and re-presenting it changes nothing. Same
    condition the session path reports as `user_pending_approval`, but with its own code —
    an MCP client cannot act on advice about signing in to the admin panel, and collapsing
    the two would make the audit trail unable to say which surface was refused.
    """

    error_code: str = "mcp_user_inactive"
    message: str = "This NOA account is not active. Ask a NOA admin to enable it."
    status_code = 403


class McpUserNotInDirectoryError(McpAuthError):
    """LDAP no longer has the operator, or has them disabled.

    Employment ended, so this is terminal and NOA cannot override it. Raised only *after*
    the cascade revoke has committed, so the message is accurate: by the time anyone reads
    it, the tokens really are gone.
    """

    error_code: str = "mcp_user_not_in_directory"
    message: str = (
        "This account is no longer active in the company directory, so its NOA MCP tokens "
        "were revoked. Contact IT if you believe this is a mistake."
    )
    status_code = 403


class McpAuthRateLimitedError(RetryAfterMixin, McpAuthError):
    """Too many failed MCP authentications for this client or token.

    429, and the only class here that is not a verdict about the credential: the token
    presented may well be fine, and the caller is being told to stop asking for a while.
    Distinct from `AuthRateLimitedError` because that one's message sends the reader to a
    sign-in form, and an MCP client has no sign-in form to visit.

    Says nothing about which bucket blocked, or whether any token was ever valid — the
    limiter counts attempts, not outcomes, so this text cannot become a probe. `detail`
    names the scope, for the logs.

    `retry_after_seconds` is what the shared handler turns into a `Retry-After` header
    (`RetryAfterMixin`); a 429 without it leaves the client guessing.
    """

    error_code: str = "mcp_auth_rate_limited"
    message: str = (
        "Too many failed NOA MCP authentication attempts. Wait a few minutes and try again."
    )
    # Not a verdict about the credential at all: too many failed attempts for this
    # LibreChat account or this token. Carries `Retry-After` below.
    status_code = 429

    def __init__(self, retry_after_seconds: int, detail: str | None = None) -> None:
        # Floored at 1 for the reason `AuthRateLimitedError` floors it: `Retry-After: 0`
        # tells the client to retry immediately, which is the opposite of a block.
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(detail)
