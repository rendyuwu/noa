"""One error envelope for every failure the API answers with (T8-T18, T64 — V8, V73).

Every `NoaError` subclass carries its own `error_code` and operator-facing `message` (see
`core.errors`, `core.auth.errors`, `core.auth.authorization_errors`,
`core.auth.mcp_token_errors`, `core.auth.mcp_auth_errors`, `core.secrets.errors`,
`core.integrations.whm.errors`, `core.integrations.pmg.errors`, `core.approvals.errors`,
`core.servers.errors`, and
— for the one refusal no core service can reach — `noa_api.api.admin_errors`),
so routes raise and this
decides the status code. Routes
that build their own `HTTPException` per failure are how two callers end up returning
different codes for the same condition — `noa-old`'s admin routes did exactly that, in
~40 lines per endpoint.

V8: the response body carries `error_code`, `message` and `request_id` only. `detail` is the
internal diagnostic — it names configuration faults, directory internals and the ids of rows
that vanished mid-request — and stays in the logs. A test asserts it never appears in a body.

**T64 (V73): every error response, not only the ones NOA raises.** A `NoaError` handler
alone leaves three surfaces answering in Starlette's default shape — no `error_code`, no
`request_id`:

- `StarletteHTTPException` — the 404 for an unrouted path and the 405 for a wrong method.
  Registered on Starlette's class, which catches FastAPI's `HTTPException` too through
  Starlette's MRO lookup.
- `RequestValidationError` — a 422 for a malformed body. **The pydantic errors are not
  echoed.** `noa-old` returned `exc.errors()` whole, and every entry carries the value that
  failed: on `POST /auth/login` that is the submitted password, in the response body. V8
  forbids it. Only `loc` and `type` reach the log, for the same reason.
- `Exception` — the 500 for a bug. Registered here, but note it runs in Starlette's
  `ServerErrorMiddleware`, which sits *outside* the user middleware stack: its response
  never passes through `RequestContextMiddleware`'s `send` wrapper, so this file sets
  `x-request-id` on every error response itself rather than relying on that wrapper.

`install_error_handling` is the one seam that installs all of it — `noa_api.main` and both
test harnesses call it, so no surface can end up with a different envelope (V66, V73).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.approvals.errors import (
    ActionDecisionError,
    ActionRequestAlreadyDecidedError,
    ActionRequestExpiredError,
    ActionRequestNotFoundError,
    ChangeEvidenceRequiredError,
    ChangeExecutionLimitReachedError,
    ChangeGateBranchUnavailableError,
    ChangeGateError,
    ChangeGateUnavailableError,
    ChangeReasonForbiddenError,
    ChangeReasonRequiredError,
    DecisionCsrfInvalidError,
)
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
from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.whm.errors import WHMFirewallCLIError
from core.remote_exec.errors import SSHExecutionError
from core.results.errors import (
    ResultTableError,
    ResultTableNotFoundError,
    ResultTableUnavailableError,
)
from core.secrets.errors import SecretCryptoError, YopassError, YopassNotConfiguredError
from core.servers.errors import (
    PMGServerNameExistsError,
    PMGServerNotFoundError,
    ProxmoxServerNameExistsError,
    ProxmoxServerNotFoundError,
    ServerInventoryError,
    WHMServerNameExistsError,
    WHMServerNotFoundError,
)
from noa_api.api.admin_errors import DirectGrantsDisabledError
from noa_api.api.request_context import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    request_id_for,
)

logger = structlog.get_logger(__name__)

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
    # --- Withdrawn capabilities (T65) ---
    # 410, and it sits outside the authorization group on purpose: `DirectGrantsDisabledError`
    # is not an `AuthorizationError`, because that tree's own test pins its statuses to
    # {400, 403, 404, 409} and the pin is the assertion (see `noa_api.api.admin_errors`). 410
    # rather than 404 or 403: the route existed in `noa-old`, the capability is withdrawn
    # permanently, and neither "missing" nor "not allowed" says that.
    DirectGrantsDisabledError: status.HTTP_410_GONE,
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
    # --- Server inventory admin CRUD (T54) ---
    # 404 for an absent row on all three tables, and per-system codes rather than one shared
    # `server_not_found`: three panel pages over three tables, and "which inventory" is the
    # first question a log reader asks (`core.servers.errors` carries the reasoning).
    WHMServerNotFoundError: status.HTTP_404_NOT_FOUND,
    ProxmoxServerNotFoundError: status.HTTP_404_NOT_FOUND,
    PMGServerNotFoundError: status.HTTP_404_NOT_FOUND,
    # 409, not 422: the submitted name is a valid server name, and it is the state of the
    # table that refuses it. `name` is `unique=True` on all three (T4) *and* what
    # `resolve_*_server_ref` matches an operator's word against (V18).
    WHMServerNameExistsError: status.HTTP_409_CONFLICT,
    ProxmoxServerNameExistsError: status.HTTP_409_CONFLICT,
    PMGServerNameExistsError: status.HTTP_409_CONFLICT,
    # Bare `ServerInventoryError`: a refusal about one row, so 409 by decision rather than by
    # falling through to a 503 that would read as "NOA is down". The same subclass-tree test
    # every taxonomy above has guards this from becoming the default for a later class.
    ServerInventoryError: status.HTTP_409_CONFLICT,
    # --- Remote execution (T14) ---
    # 502: a host NOA depends on refused, timed out, or presented an unexpected host key.
    # Mapped now so it does not take `FALLBACK_STATUS` — 503 reads as "authentication is
    # unclassified and NOA may be down", which is the wrong answer for a working NOA and a
    # broken remote. One entry for the whole SSH surface because `SSHExecutionError` carries
    # the specific `error_code`.
    #
    # T54 does **not** refine this per code, contrary to what this comment predicted at T14.
    # Its validate route catches the whole tree and answers **200** with
    # `{ok:false, error_code, message}`: an operator pressing Validate asked "does this server
    # answer?", and "no, `ssh_timeout`" is that question's *answer*, not a failure of the
    # request. The panel reads `result.ok` for exactly that reason
    # (`apps/admin-web/.../whm-status.ts::deriveWhmValidationStatus`), so a 502 there would
    # throw at the transport and render as an unhandled error instead of a red status chip.
    # This 502 therefore covers the *tool* paths only, where the tools sanitise per V19.
    SSHExecutionError: status.HTTP_502_BAD_GATEWAY,
    # --- WHM firewall backends (T16) ---
    # 502, same reading as `SSHExecutionError`: NOA works, csf or imunify360-agent on the
    # remote did not answer usably. One entry for the tree — `CSFCLIError` and
    # `ImunifyCLIError` inherit via the MRO walk, and which backend failed is already in
    # `error_code`. T54's validate route answers 200 with `ok:false` instead (see above).
    WHMFirewallCLIError: status.HTTP_502_BAD_GATEWAY,
    # --- PMG `pmgsh` CLI (T18) ---
    # 502, same reading again: NOA works, `pmgsh`/`pmgconfig` on the PMG node did not answer
    # usably. One entry for the whole surface — `PMGSHCLIError` carries the specific
    # `error_code`, including the `SSHExecutionError` codes it converts. T54's validate route
    # answers 200 with `ok:false` instead (see above).
    PMGSHCLIError: status.HTTP_502_BAD_GATEWAY,
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
    # --- CHANGE approval gate (T33) ---
    # 503: NOA could not record a pending request, so it refused to run the change (V23).
    # The operator did nothing wrong and retrying is the remedy, which is what 503 says.
    ChangeGateUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    # 500 for both of the gate's other refusals. Neither is reachable from anything a client
    # sends: a reason-shaped argument means a CHANGE tool declared a parameter C8 forbids,
    # and missing evidence means it skipped its own in-process preflight (C9, V17). Those are
    # NOA's bugs, and answering 400 would blame the caller for one.
    ChangeReasonForbiddenError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ChangeEvidenceRequiredError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # 500 for the same reason, one step later (T32, V24): the branch that shapes the approval
    # surface is a module constant, never a tool argument, so a caller cannot have selected an
    # unbuilt one. Not 503 — the pending row already exists by then, and "try again" would open
    # a second request for one change.
    ChangeGateBranchUnavailableError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # Bare `ChangeGateError`: still "the change was not submitted and did not run", so 503
    # rather than the fallback by accident. A subclass-tree test asserts every member above
    # is mapped, so reaching this line means a new class arrived without a decision.
    ChangeGateError: status.HTTP_503_SERVICE_UNAVAILABLE,
    # --- Decisions on an existing request (T37) ---
    # 404 for absent *and* for another operator's, which is V27's whole point: a 403 would
    # confirm the request exists. `ActionDecisionError` sits at the end of this group, so
    # note the pairing — these two answer with different statuses and must not collapse.
    ActionRequestNotFoundError: status.HTTP_404_NOT_FOUND,
    # 409 for both terminal refusals. The caller may decide requests in general; this one is
    # past deciding. Two classes rather than one because the remedies differ — reload and
    # read the outcome (V28) versus ask for the change again (V32).
    ActionRequestAlreadyDecidedError: status.HTTP_409_CONFLICT,
    ActionRequestExpiredError: status.HTTP_409_CONFLICT,
    # 409, and the status is V15's own: a decision without a non-blank reason. Not a 422 —
    # `reason` is a well-formed string, it is the *decision* that is refused, and V15 names
    # both this code and this status.
    ChangeReasonRequiredError: status.HTTP_409_CONFLICT,
    # 409 for V31's cap (T38). Same reading as "already decided": the caller may approve changes
    # in general, just not one more right now. Not 429 — nothing is rate-limiting them, and
    # `Retry-After` would be a number NOA cannot honestly produce.
    ChangeExecutionLimitReachedError: status.HTTP_409_CONFLICT,
    # 403, not 401: the session authenticated fine and signing in again changes nothing
    # (V39). The remedy is a freshly minted token, which means reloading the card.
    DecisionCsrfInvalidError: status.HTTP_403_FORBIDDEN,
    # Bare `ActionDecisionError`: a refusal about one request, so 409 rather than the 503
    # fallback, which would read as "NOA is down" for something NOA decided. The same
    # subclass-tree test guards this from becoming the default for a class added later.
    ActionDecisionError: status.HTTP_409_CONFLICT,
    # --- Large READ tables (T56, V27, V64) ---
    # 404 for all four of its causes — unknown token, another operator's, one whose requester
    # was deleted, and one past its lifetime. V27's rule against an existence oracle, spelled
    # against another table: a status that varied by cause would say which tokens are real.
    ResultTableNotFoundError: status.HTTP_404_NOT_FOUND,
    # 503: the rows could not be parked, so the READ has no surface to point at. Retrying the
    # tool call is the remedy, and unlike a CHANGE there is nothing a retry could double —
    # a READ that ran twice changed nothing either time.
    ResultTableUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    # Bare `ResultTableError`: still "that table could not be read", so 503 by decision rather
    # than by falling through. The subclass-tree test covers this tree too.
    ResultTableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}

# Bare `AuthError` means "authentication failed and we did not classify why", which is an
# infrastructure answer, not a credential one — 401 would send the operator chasing their
# own password. Reached only by `NoaError` subclasses with no mapping at all.
FALLBACK_STATUS: Final = status.HTTP_503_SERVICE_UNAVAILABLE

# --- Shapes for the failures NOA does not raise (T64) ---
#
# `(error_code, message)` per status, rather than echoing `StarletteHTTPException.detail`.
# Nothing in this repo builds an `HTTPException` (see the module docstring), so the only
# ones that reach the handler are Starlette's own routing failures — and a fixed shape means
# a future raiser cannot put internal text in a body by accident (V8).
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
# rather than retyped, so the two paths cannot drift (V66).
INTERNAL_SHAPE: Final[tuple[str, str]] = (NoaError.error_code, NoaError.message)

LOG_VALIDATION_FAILED: Final = "request_validation_failed"
LOG_UNHANDLED: Final = "api_unhandled_exception"


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


def envelope(error_code: str, message: str, request_id: str | None = None) -> dict[str, str]:
    """The one error-body shape (V8, V73).

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

    `request_id` is keyword-optional because callers that only care about the V8 shape — the
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
    """Build an error response with the envelope and `x-request-id` (V73).

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


def redacted_validation_errors(errors: list[Any]) -> list[dict[str, str]]:
    """`loc` + `type` per failing field, for the log. Nothing else (V8).

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
    """Install the request-id middleware and every error handler on `app` (V73).

    One function, called by `noa_api.main` and by both test harnesses, because V73's "shared
    handler, not per-route" is only true if there is one place that decides. Four handlers:

    - `NoaError` — one registration on the base class, not one per taxonomy. Starlette
      dispatches by MRO, so `AuthError` and `AuthorizationError` both land here and a third
      taxonomy needs no change beyond its entries in `STATUS_BY_ERROR`.
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
            status_code=status_for(error),
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
        # The one place a stack trace is welcome. The body says nothing about it (V8): an
        # unhandled exception is by definition a case nobody decided was safe to describe.
        logger.exception(LOG_UNHANDLED, error_type=type(exc).__name__, exc_info=exc)

        error_code, message = INTERNAL_SHAPE
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=error_code,
            message=message,
            request_id=request_id_for(request.scope),
        )


__all__ = [
    "FALLBACK_HTTP_SHAPE",
    "FALLBACK_STATUS",
    "HTTP_ERROR_SHAPES",
    "INTERNAL_SHAPE",
    "STATUS_BY_ERROR",
    "VALIDATION_SHAPE",
    "envelope",
    "error_body",
    "error_headers",
    "error_response",
    "install_error_handling",
    "redacted_validation_errors",
    "status_for",
]
