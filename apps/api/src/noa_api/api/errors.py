"""One error envelope for every failure the API answers with.

Every `NoaError` subclass carries its own `error_code`, operator-facing `message` and
`status_code` (see `core.errors` and the taxonomies under `core.auth`, `core.audit`,
`core.approvals`, `core.secrets`, `core.integrations`, `core.remote_exec`, `core.results`,
`core.servers`, and — for the one refusal no core service can reach —
`noa_api.api.admin_errors`), so routes raise and this renders. A subclass with no reading of
its own inherits its parent's status by ordinary attribute lookup; a taxonomy test per tree
asserts every member declares one rather than taking `NoaError`'s 503. Routes
that build their own `HTTPException` per failure are how two callers end up returning
different codes for the same condition — `noa-old`'s admin routes did exactly that, in
~40 lines per endpoint.

The response body carries `error_code`, `message` and `request_id` only. `detail` is the
internal diagnostic — it names configuration faults, directory internals and the ids of rows
that vanished mid-request — and stays in the logs. A test asserts it never appears in a body.

**Every error response carries it, not only the ones NOA raises.** A `NoaError` handler
alone leaves three surfaces answering in Starlette's default shape — no `error_code`, no
`request_id`:

- `StarletteHTTPException` — the 404 for an unrouted path and the 405 for a wrong method.
  Registered on Starlette's class, which catches FastAPI's `HTTPException` too through
  Starlette's MRO lookup.
- `RequestValidationError` — a 422 for a malformed body. **The pydantic errors are not
  echoed.** `noa-old` returned `exc.errors()` whole, and every entry carries the value that
  failed: on `POST /auth/login` that is the submitted password, in the response body. The
  envelope shape forbids it. Only `loc` and `type` reach the log, for the same reason.
- `Exception` — the 500 for a bug. Registered here, but note it runs in Starlette's
  `ServerErrorMiddleware`, which sits *outside* the user middleware stack: its response
  never passes through `RequestContextMiddleware`'s `send` wrapper, so this file sets
  `x-request-id` on every error response itself rather than relying on that wrapper.

`install_error_handling` is the one seam that installs all of it — `noa_api.main` and both
test harnesses call it, so no surface can end up with a different envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.errors import NoaError, RetryAfterMixin
from noa_api.api.request_context import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    request_id_for,
)

logger = structlog.get_logger(__name__)

# --- Shapes for the failures NOA does not raise ---
#
# `(error_code, message)` per status, rather than echoing `StarletteHTTPException.detail`.
# Nothing in this repo builds an `HTTPException` (see the module docstring), so the only
# ones that reach the handler are Starlette's own routing failures — and a fixed shape means
# a future raiser cannot put internal text in a body by accident.
HTTP_ERROR_SHAPES: Final[dict[int, tuple[str, str]]] = {
    status.HTTP_404_NOT_FOUND: ("not_found", "That endpoint does not exist."),
    status.HTTP_405_METHOD_NOT_ALLOWED: (
        "method_not_allowed",
        "That method is not allowed on this endpoint.",
    ),
}
FALLBACK_HTTP_SHAPE: Final[tuple[str, str]] = (
    "http_error",
    "The request could not be completed.",
)

VALIDATION_SHAPE: Final[tuple[str, str]] = (
    "request_validation_error",
    "The request body or parameters are invalid.",
)

# The 500 answers exactly as a bare `NoaError` would. Same two strings, read off the class
# rather than retyped, so the two paths cannot drift.
INTERNAL_SHAPE: Final[tuple[str, str]] = (NoaError.error_code, NoaError.message)

LOG_VALIDATION_FAILED: Final = "request_validation_failed"
LOG_UNHANDLED: Final = "api_unhandled_exception"


def envelope(error_code: str, message: str, request_id: str | None = None) -> dict[str, str]:
    """The one error-body shape.

    `error_code` is what clients branch on, `message` is for humans, `request_id` is what an
    operator quotes when neither is enough — it matches `x-request-id` on the response and
    the `request_id` on every structlog line the request emitted.
    """
    body = {"error_code": error_code, "message": message}
    if request_id is not None:
        body["request_id"] = request_id
    return body


def error_body(error: NoaError, *, request_id: str | None = None) -> dict[str, str]:
    """Response body for a `NoaError`.

    `request_id` is keyword-optional because callers that only care about the envelope shape —
    the
    tests asserting `detail` never leaks — have no request to read one from. Every path that
    actually answers a client passes it; `error_response` below is how.
    """
    return envelope(error.error_code, error.message, request_id)


def error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an error response with the envelope and `x-request-id`.

    The header is set here rather than left to `RequestContextMiddleware`: the 500 path runs
    in `ServerErrorMiddleware`, outside that middleware, and would otherwise answer without
    it. Where both do set it, `MutableHeaders` replaces rather than appends, so there is
    never a duplicate.
    """
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code,
        content=envelope(error_code, message, request_id),
        headers=response_headers,
    )


def error_headers(error: NoaError) -> dict[str, str]:
    """Response headers an error carries beyond the body.

    Only `Retry-After` today, keyed on `RetryAfterMixin` rather than on a list of classes:
    The rate-limiter's 429 rule requires it to say when to retry, and the previous
    `isinstance(..., AuthRateLimitedError)` test would have silently dropped the header for the
    MCP identity resolver's `McpAuthRateLimitedError`.

    A function beside `error_body` because the FastAPI handler is no longer the only thing
    that renders a `NoaError`: `noa_api.mcp_request_auth.McpAuthErrorMiddleware` writes a
    raw ASGI response for the mounted MCP app, which has no exception-handler graph, and it
    must not shape one differently.
    """
    if isinstance(error, RetryAfterMixin):
        return {"Retry-After": str(error.retry_after_seconds)}
    return {}


def redacted_validation_errors(errors: list[Any]) -> list[dict[str, str]]:
    """`loc` + `type` per failing field, for the log. Nothing else.

    pydantic's error entries carry `input` — the value that failed validation, which on a
    login is the submitted password — and `ctx`, which can carry an exception. Neither is
    something to write to a log store, so this keeps the two fields that say *where* and
    *what kind* and drops the rest.
    """
    redacted: list[dict[str, str]] = []
    for error in errors:
        if not isinstance(error, Mapping):
            continue
        location = error.get("loc") or ()
        parts = location if isinstance(location, (list, tuple)) else (location,)
        redacted.append(
            {
                "loc": ".".join(str(part) for part in parts),
                "type": str(error.get("type", "unknown")),
            }
        )
    return redacted


def install_error_handling(app: FastAPI) -> None:
    """Install the request-id middleware and every error handler on `app`.

    One function, called by `noa_api.main` and by both test harnesses, because the shared
    request-id rule's "shared handler, not per-route" is only true if there is one place that
    decides. Four handlers:

    - `NoaError` — one registration on the base class, not one per taxonomy. Starlette
      dispatches by MRO, so `AuthError` and `AuthorizationError` both land here and a third
      taxonomy needs no change beyond a `status_code` on each of its classes.
    - `StarletteHTTPException`, `RequestValidationError`, `Exception` — the surfaces FastAPI
      would otherwise answer for, in its own shape. See the module docstring.
    """
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(NoaError)
    async def handle_noa_error(request: Request, exc: Exception) -> JSONResponse:
        # Starlette types handlers as taking `Exception`; registration guarantees the
        # narrower class.
        error = exc if isinstance(exc, NoaError) else NoaError(str(exc))

        return error_response(
            status_code=error.status_code,
            error_code=error.error_code,
            message=error.message,
            request_id=request_id_for(request.scope),
            headers=error_headers(error),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        status_code = (
            exc.status_code
            if isinstance(exc, StarletteHTTPException)
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        error_code, message = HTTP_ERROR_SHAPES.get(status_code, FALLBACK_HTTP_SHAPE)
        headers = getattr(exc, "headers", None)

        return error_response(
            status_code=status_code,
            error_code=error_code,
            message=message,
            request_id=request_id_for(request.scope),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        errors = exc.errors() if isinstance(exc, RequestValidationError) else []
        logger.warning(LOG_VALIDATION_FAILED, errors=redacted_validation_errors(errors))

        error_code, message = VALIDATION_SHAPE
        return error_response(
            # `HTTP_422_UNPROCESSABLE_ENTITY` is deprecated in the pinned Starlette; the
            # value is the same 422.
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error_code=error_code,
            message=message,
            request_id=request_id_for(request.scope),
        )

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # The one place a stack trace is welcome. The body says nothing about it: an
        # unhandled exception is by definition a case nobody decided was safe to describe.
        logger.exception(LOG_UNHANDLED, error_type=type(exc).__name__, exc_info=exc)

        error_code, message = INTERNAL_SHAPE
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=error_code,
            message=message,
            request_id=request_id_for(request.scope),
        )
