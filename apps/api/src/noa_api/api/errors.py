"""One exception handler for every NOA error (T8, T9, T10, T11, T14, T15, T16).

Every `NoaError` subclass carries its own `error_code` and operator-facing `message` (see
`core.errors`, `core.auth.errors`, `core.auth.authorization_errors`,
`core.auth.mcp_token_errors`, `core.auth.mcp_auth_errors`, `core.secrets.errors`,
`core.integrations.whm.errors`), so routes raise and this decides the status code. Routes
that build their own `HTTPException` per failure are how two callers end up returning
different codes for the same condition — `noa-old`'s admin routes did exactly that, in
~40 lines per endpoint.

V8: the response body carries `error_code` + `message` only. `detail` is the internal
diagnostic — it names configuration faults, directory internals and the ids of rows that
vanished mid-request — and stays in the logs. A test asserts it never appears in a body.

T64 extends *this* handler with `request_id` in the body and `x-request-id` on the response
(V73). It is deliberately the single place that shapes an error response, so that stays a
one-file change and cannot drift per route.
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from core.auth.authorization_errors import (
    AdminAccessRequiredError,
    AuthorizationError,
    InternalRoleError,
    InvalidRoleNameError,
    LastActiveAdminError,
    ReservedRoleError,
    RoleNotFoundError,
    SelfDeactivateAdminError,
    SelfDeleteError,
    SelfRemoveAdminRoleError,
    UnknownRoleError,
    UnknownToolError,
    UserNotFoundError,
)
from core.auth.errors import (
    AuthAccountDisabledError,
    AuthConfigurationError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
    AuthRateLimitedError,
    AuthSessionExpiredError,
    AuthSessionInvalidError,
    LdapUnavailableError,
)
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
from core.auth.mcp_token_errors import (
    InvalidTokenLabelError,
    McpTokenError,
    McpTokenNotFoundError,
)
from core.errors import NoaError, RetryAfterMixin
from core.integrations.whm.errors import WHMFirewallCLIError
from core.remote_exec.errors import SSHExecutionError
from core.secrets.errors import SecretCryptoError, YopassError, YopassNotConfiguredError

# Lookup is by exact class with an MRO walk below, so ordering here is for reading only.
STATUS_BY_ERROR: Final[dict[type[NoaError], int]] = {
    # --- Authentication (T8) ---
    # Credentials rejected, or a session that no longer verifies. All 401: the remedy is
    # the same (sign in), and distinguishing them by status would leak which.
    AuthInvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    AuthSessionExpiredError: status.HTTP_401_UNAUTHORIZED,
    AuthSessionInvalidError: status.HTTP_401_UNAUTHORIZED,
    # Authenticated, but not permitted to be here. 403, not 401: re-authenticating changes
    # nothing, so a login redirect would loop.
    AuthPendingApprovalError: status.HTTP_403_FORBIDDEN,
    AuthAccountDisabledError: status.HTTP_403_FORBIDDEN,
    AuthRateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    # NOA's own fault, and the operator's credentials are fine (V8: the specific
    # misconfiguration stays in `detail`, out of the body).
    AuthConfigurationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    LdapUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    # --- Authorization (T9) ---
    # Known caller, refused. 403 for "not yours to do" (V13).
    AdminAccessRequiredError: status.HTTP_403_FORBIDDEN,
    ReservedRoleError: status.HTTP_403_FORBIDDEN,
    # Absent target.
    UserNotFoundError: status.HTTP_404_NOT_FOUND,
    RoleNotFoundError: status.HTTP_404_NOT_FOUND,
    # Malformed request: the caller sent something NOA will not accept at all.
    InvalidRoleNameError: status.HTTP_400_BAD_REQUEST,
    InternalRoleError: status.HTTP_400_BAD_REQUEST,
    UnknownToolError: status.HTTP_400_BAD_REQUEST,
    UnknownRoleError: status.HTTP_400_BAD_REQUEST,
    # Well-formed request refused because it would break an invariant (V12). 409, not 403:
    # the caller is allowed to do this in general, just not to this row right now.
    LastActiveAdminError: status.HTTP_409_CONFLICT,
    SelfDeactivateAdminError: status.HTTP_409_CONFLICT,
    # `SelfDeleteAdminError` inherits this through the MRO walk.
    SelfDeleteError: status.HTTP_409_CONFLICT,
    SelfRemoveAdminRoleError: status.HTTP_409_CONFLICT,
    # Bare `AuthorizationError` is still a refusal, so 403 rather than the 503 fallback. A
    # test asserts every subclass is mapped above, so reaching this line means a new class
    # arrived without a decision.
    AuthorizationError: status.HTTP_403_FORBIDDEN,
    # --- MCP tokens (T10) ---
    # 404 for a token that is absent *or* another user's: the lookup is scoped by user id,
    # so the two cases answer identically and the response is not an enumeration oracle
    # (V2, and the V27/V76 principle).
    McpTokenNotFoundError: status.HTTP_404_NOT_FOUND,
    # The label is longer than the column holds — a malformed request, not a server fault.
    InvalidTokenLabelError: status.HTTP_400_BAD_REQUEST,
    # Bare `McpTokenError`: a request problem, not an infrastructure answer. Same
    # subclass-tree test as above guards this from becoming the default.
    McpTokenError: status.HTTP_400_BAD_REQUEST,
    # --- MCP request-path authentication (T11, T12) ---
    # The credential did not authenticate the caller. 401 across all four: absent,
    # unknown, expired and wrong-binding share a remedy (present a valid token of your
    # own), and splitting them by status would let a caller probe which tokens are real.
    # `verify_token` returns `None` for every one of these (R2 gives it no body hook);
    # `noa_api.mcp_request_auth.McpAuthErrorMiddleware` is what renders them, and these
    # mappings are what it renders with (V3).
    McpTokenMissingError: status.HTTP_401_UNAUTHORIZED,
    McpTokenInvalidError: status.HTTP_401_UNAUTHORIZED,
    McpTokenExpiredError: status.HTTP_401_UNAUTHORIZED,
    LibreChatUserHeaderMissingError: status.HTTP_401_UNAUTHORIZED,
    LibreChatUserMismatchError: status.HTTP_401_UNAUTHORIZED,
    # Authenticated, still refused. 403 because re-presenting the token changes nothing:
    # a disabled operator needs an admin, and one the directory dropped needs IT.
    McpUserInactiveError: status.HTTP_403_FORBIDDEN,
    McpUserNotInDirectoryError: status.HTTP_403_FORBIDDEN,
    # Not a verdict about the credential at all: too many failed attempts for this
    # LibreChat account or this token (V9, T12). Carries `Retry-After` below.
    McpAuthRateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    # Bare `McpAuthError`: a request problem, not "NOA is down". Same subclass-tree test
    # guards this from becoming the default for a class added later.
    McpAuthError: status.HTTP_400_BAD_REQUEST,
    # --- Remote execution (T14) ---
    # 502: a host NOA depends on refused, timed out, or presented an unexpected host key.
    # Mapped now so it does not take `FALLBACK_STATUS` — 503 reads as "authentication is
    # unclassified and NOA may be down", which is the wrong answer for a working NOA and a
    # broken remote. One entry for the whole SSH surface because `SSHExecutionError` carries
    # the specific `error_code`; T54 owns the validate routes and refines per code there
    # (`ssh_timeout` → 504, `ssh_not_configured`/`ssh_host_key_not_validated` → 409).
    SSHExecutionError: status.HTTP_502_BAD_GATEWAY,
    # --- WHM firewall backends (T16) ---
    # 502, same reading as `SSHExecutionError`: NOA works, csf or imunify360-agent on the
    # remote did not answer usably. One entry for the tree — `CSFCLIError` and
    # `ImunifyCLIError` inherit via the MRO walk, and which backend failed is already in
    # `error_code`. T54's validate route is the HTTP caller; the tools sanitise per V19.
    WHMFirewallCLIError: status.HTTP_502_BAD_GATEWAY,
    # --- Secrets (T15) ---
    # 500: NOA cannot read or write its own ciphertext. The operator's request was fine and
    # retrying changes nothing — the key is absent, wrong, or the row was never encrypted.
    # Which of those it is stays in `detail` (V8). Subclasses inherit via the MRO walk.
    SecretCryptoError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # 502 for the delivery hop, same reading as `SSHExecutionError`: NOA works, the system it
    # depends on did not answer usably. C15's deliver-first ordering means nothing was
    # changed when this is raised.
    YopassError: status.HTTP_502_BAD_GATEWAY,
    # ...except when yopass was never configured. That is NOA's own gap, not the upstream's,
    # so it takes 500 rather than inheriting 502 from `YopassError`.
    YopassNotConfiguredError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

# Bare `AuthError` means "authentication failed and we did not classify why", which is an
# infrastructure answer, not a credential one — 401 would send the operator chasing their
# own password. Reached only by `NoaError` subclasses with no mapping at all.
FALLBACK_STATUS: Final = status.HTTP_503_SERVICE_UNAVAILABLE


def status_for(error: NoaError) -> int:
    """HTTP status for `error`, falling back for unmapped subclasses.

    `type(...)` lookup then MRO walk, so a future subclass of, say,
    `AuthInvalidCredentialsError` inherits 401 instead of silently becoming a 503, and
    `SelfDeleteAdminError` inherits its parent's 409 without a second entry.
    """
    for klass in type(error).__mro__:
        if klass in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[klass]
    return FALLBACK_STATUS


def error_body(error: NoaError) -> dict[str, str]:
    """Response body. `error_code` is what clients branch on; `message` is for humans."""
    return {"error_code": error.error_code, "message": error.message}


def error_headers(error: NoaError) -> dict[str, str]:
    """Response headers an error carries beyond the body.

    Only `Retry-After` today, keyed on `RetryAfterMixin` rather than on a list of classes:
    V9 requires a 429 to say when to retry, and the previous `isinstance(...,
    AuthRateLimitedError)` test would have silently dropped the header for T12's
    `McpAuthRateLimitedError`.

    A function beside `error_body` because the FastAPI handler is no longer the only thing
    that renders a `NoaError`: `noa_api.mcp_request_auth.McpAuthErrorMiddleware` writes a
    raw ASGI response for the mounted MCP app, which has no exception-handler graph, and it
    must not shape one differently (V73).
    """
    if isinstance(error, RetryAfterMixin):
        return {"Retry-After": str(error.retry_after_seconds)}
    return {}


def install_error_handler(app: FastAPI) -> None:
    """Register the `NoaError` handler on `app`.

    One registration on the base class, not one per taxonomy: Starlette dispatches by MRO,
    so `AuthError` and `AuthorizationError` both land here and a third taxonomy needs no
    change beyond its entries in `STATUS_BY_ERROR`.
    """

    @app.exception_handler(NoaError)
    async def handle_noa_error(_request: Request, exc: Exception) -> JSONResponse:
        # Starlette types handlers as taking `Exception`; registration guarantees the
        # narrower class.
        error = exc if isinstance(exc, NoaError) else NoaError(str(exc))

        return JSONResponse(
            status_code=status_for(error),
            content=error_body(error),
            headers=error_headers(error) or None,
        )


__all__ = [
    "FALLBACK_STATUS",
    "STATUS_BY_ERROR",
    "error_body",
    "error_headers",
    "install_error_handler",
    "status_for",
]
