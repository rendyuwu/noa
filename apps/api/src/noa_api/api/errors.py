"""One exception handler for every NOA error (T8, T9, T10).

Every `NoaError` subclass carries its own `error_code` and operator-facing `message` (see
`core.errors`, `core.auth.errors`, `core.auth.authorization_errors`,
`core.auth.mcp_token_errors`), so routes raise and this decides the status code. Routes
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
from core.auth.mcp_token_errors import (
    InvalidTokenLabelError,
    McpTokenError,
    McpTokenNotFoundError,
)
from core.errors import NoaError

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

        headers: dict[str, str] = {}
        if isinstance(error, AuthRateLimitedError):
            # V9: a 429 without this leaves the client guessing when to retry.
            headers["Retry-After"] = str(error.retry_after_seconds)

        return JSONResponse(
            status_code=status_for(error),
            content=error_body(error),
            headers=headers or None,
        )


__all__ = [
    "FALLBACK_STATUS",
    "STATUS_BY_ERROR",
    "error_body",
    "install_error_handler",
    "status_for",
]
