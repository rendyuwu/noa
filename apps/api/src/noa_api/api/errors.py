"""One exception handler for the whole auth taxonomy (T8).

Every `AuthError` subclass carries its own `error_code` and operator-facing `message`
(see `core.auth.errors`), so routes raise and this decides the status code. Routes
that build their own `HTTPException` per failure are how two callers end up returning
different codes for the same condition.

V8: the response body carries `error_code` + `message` only. `detail` is the internal
diagnostic — it names configuration faults and directory internals — and stays in the
logs. A test asserts it never appears in a body.

T64 extends *this* handler with `request_id` in the body and `x-request-id` on the
response (V73). It is deliberately the single place that shapes an error response, so
that stays a one-file change and cannot drift per route.
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from core.auth.errors import (
    AuthAccountDisabledError,
    AuthConfigurationError,
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
    AuthRateLimitedError,
    AuthSessionExpiredError,
    AuthSessionInvalidError,
    LdapUnavailableError,
)

# Most specific first is irrelevant here — the lookup is by exact class, with the base
# `AuthError` as the fallback below.
STATUS_BY_ERROR: Final[dict[type[AuthError], int]] = {
    # Credentials rejected, or a session that no longer verifies. All 401: the remedy
    # is the same (sign in), and distinguishing them by status would leak which.
    AuthInvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    AuthSessionExpiredError: status.HTTP_401_UNAUTHORIZED,
    AuthSessionInvalidError: status.HTTP_401_UNAUTHORIZED,
    # Authenticated, but not permitted to be here. 403, not 401: re-authenticating
    # changes nothing, so a login redirect would loop.
    AuthPendingApprovalError: status.HTTP_403_FORBIDDEN,
    AuthAccountDisabledError: status.HTTP_403_FORBIDDEN,
    AuthRateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    # NOA's own fault, and the operator's credentials are fine (V8: the specific
    # misconfiguration stays in `detail`, out of the body).
    AuthConfigurationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    LdapUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}

# Bare `AuthError` means "authentication failed and we did not classify why", which is
# an infrastructure answer, not a credential one — 401 would send the operator chasing
# their own password.
FALLBACK_STATUS: Final = status.HTTP_503_SERVICE_UNAVAILABLE


def status_for(error: AuthError) -> int:
    """HTTP status for `error`, falling back for unmapped subclasses.

    `type(...)` lookup then MRO walk, so a future subclass of, say,
    `AuthInvalidCredentialsError` inherits 401 instead of silently becoming a 503.
    """
    for klass in type(error).__mro__:
        if klass in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[klass]
    return FALLBACK_STATUS


def error_body(error: AuthError) -> dict[str, str]:
    """Response body. `error_code` is what clients branch on; `message` is for humans."""
    return {"error_code": error.error_code, "message": error.message}


def install_auth_error_handler(app: FastAPI) -> None:
    """Register the `AuthError` handler on `app`."""

    @app.exception_handler(AuthError)
    async def handle_auth_error(_request: Request, exc: Exception) -> JSONResponse:
        # Starlette types handlers as taking `Exception`; registration guarantees the
        # narrower class.
        error = exc if isinstance(exc, AuthError) else AuthError(str(exc))

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
    "install_auth_error_handler",
    "status_for",
]
