"""One id per request, on every response and in every log line.

V73 wants a `request_id` in every error body and an `x-request-id` on every error response.
That needs something upstream of the exception handlers to decide what the id *is*, because
by the time a handler runs there is no other place the id could come from that both the
FastAPI surface and the mounted MCP app can read. `RequestContextMiddleware` is that
something, and it publishes the id three ways because three readers need it and none of
them can reach the others:

- **`scope["state"]["request_id"]`** — for anything holding the raw ASGI scope, which is
  `noa_api.mcp_request_auth.McpAuthErrorMiddleware` (the mounted MCP app has no
  exception-handler graph, so it writes its refusal itself) and for Starlette's
  `ServerErrorMiddleware`, which builds a fresh `Request` over the same scope.
- **a `ContextVar`** — for code with neither a request nor a scope in hand.
- **structlog's contextvars** — so the id an operator reads off a failed response is the id
  on the log line that recorded it. Without this the envelope is a number that correlates
  with nothing. structlog's default processor chain already begins with `merge_contextvars`,
  so no logging configuration is required for it to appear.

**Why the id is sanitised rather than echoed.** `noa-old` (`core/request_context.py`,
`api/error_handling.py`) takes an inbound `X-Request-Id` verbatim. That value goes straight
back out as a response header and into structured log output, so an unbounded or
newline-carrying one is a log-forging surface at best. Inbound is still honoured — a proxy
that mints ids is exactly the correlation V73 is for — but only when it looks like an
id: bounded length, and characters that cover UUIDs, hex digests and W3C trace ids.

That rule is not this header's alone, which is why `sanitize_header_label` is separate from
`sanitize_request_id`: T73 reads `X-Noa-Conversation-Ref` and writes it into `tool_runs` and
into log output, the same exposure under a different name and a different bound. The
two callers differ only in what they do with a rejection — an id is minted, a label goes
`None` — and that choice stays with each caller rather than in here.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Final
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Lowercase: ASGI header names are compared lowercased, and Starlette's `Headers` /
# `MutableHeaders` normalise on both read and write, so one spelling serves both directions.
REQUEST_ID_HEADER: Final = "x-request-id"

# Key inside `scope["state"]`, which Starlette hands to every `Request` built over the scope
# — including the one `ServerErrorMiddleware` builds for the 500 path (see `errors.py`).
SCOPE_STATE_REQUEST_ID: Final = "request_id"

# The structlog key. Same spelling as the body field so a log query and a response body use
# one name.
LOG_REQUEST_ID: Final = "request_id"

# Long enough for a UUID (36), a hex digest (64) or a `traceparent` (55) with room to spare;
# short enough that a header cannot be used to pump a log store.
MAX_REQUEST_ID_LENGTH: Final = 200

# Hex, dashes, dots, underscores and colons — UUIDs, digests, `traceparent`, and the
# `service:id` forms proxies emit. Deliberately excludes whitespace and control characters.
_HEADER_LABEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9._:-]+$")

_request_id: ContextVar[str | None] = ContextVar("noa_request_id", default=None)


def new_request_id() -> str:
    """A fresh id. `uuid4` rather than a counter: app instances do not share state."""
    return str(uuid4())


def current_request_id() -> str | None:
    """The id of the request being served, or `None` off-request.

    Returns `None` rather than minting, because a caller outside a request has nothing to
    correlate and a fresh id would only look like one.
    """
    return _request_id.get()


def sanitize_header_label(value: str | None, *, max_length: int) -> str | None:
    """A client-supplied header value if it is usable as an identifier, else `None`.

    See the module docstring: any header NOA re-emits or writes into log output is a
    log-forging surface, so it is accepted only bounded and on an explicit character
    allowlist. Rejection is `None` here for every caller; what to do about it is theirs.
    """
    if value is None:
        return None

    candidate = value.strip()
    if not candidate or len(candidate) > max_length:
        return None
    if not _HEADER_LABEL_PATTERN.match(candidate):
        return None
    return candidate


def sanitize_request_id(value: str | None) -> str | None:
    """An inbound `x-request-id` if it is usable as an id, else `None`."""
    return sanitize_header_label(value, max_length=MAX_REQUEST_ID_LENGTH)


def request_id_for(scope: Scope) -> str:
    """The id for `scope`, minting and storing one if the middleware has not run.

    Idempotent, and that is the point: `RequestContextMiddleware` writes the id on the way
    in, but Starlette's `ServerErrorMiddleware` sits *outside* the user middleware stack, so
    a 500 raised before this middleware ran would otherwise have no id at all. Every reader
    goes through here, so they cannot disagree about which id this request has.
    """
    state = scope.setdefault("state", {})
    stored = state.get(SCOPE_STATE_REQUEST_ID)
    if isinstance(stored, str) and stored:
        return stored

    request_id = new_request_id()
    state[SCOPE_STATE_REQUEST_ID] = request_id
    return request_id


class RequestContextMiddleware:
    """Assign a request id, publish it, and put it on the response.

    Raw ASGI rather than `BaseHTTPMiddleware`, matching `McpAuthErrorMiddleware`: the header
    is written by wrapping `send`, so it lands on *every* response, including the streaming
    SSE replies the MCP transport sends and any response a route builds itself.

    Installed by `noa_api.api.errors.install_error_handling`, which is the single seam both
    `noa_api.main` and the test harnesses call — so the envelope cannot be present on one
    surface and absent on another.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = sanitize_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        request_id = inbound or new_request_id()
        scope.setdefault("state", {})[SCOPE_STATE_REQUEST_ID] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                # `MutableHeaders.__setitem__` replaces any existing value, so a handler
                # that set the header itself (the 500 path does) does not end up with two.
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        token = _request_id.set(request_id)
        structlog.contextvars.bind_contextvars(**{LOG_REQUEST_ID: request_id})
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            structlog.contextvars.unbind_contextvars(LOG_REQUEST_ID)
            _request_id.reset(token)


__all__ = [
    "LOG_REQUEST_ID",
    "MAX_REQUEST_ID_LENGTH",
    "REQUEST_ID_HEADER",
    "SCOPE_STATE_REQUEST_ID",
    "RequestContextMiddleware",
    "current_request_id",
    "new_request_id",
    "request_id_for",
    "sanitize_header_label",
    "sanitize_request_id",
]
