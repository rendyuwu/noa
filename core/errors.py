"""One error base for the whole API surface.

Every NOA failure that a client should see carries three fields, and the split is what
lets a single exception handler shape every response:

- `error_code` — stable machine string. Clients and tests branch on this, never on prose.
- `message` — what the operator reads. Says what happened and who can fix it, so nobody
  files a ticket for something they could clear themselves. Safe to render: no
  credential, no directory internals, no configuration state.
- `detail` — optional internal cause, for logs only. Defaults to `message`, and
  `str(exc)` yields it, so tracebacks stay useful while response bodies stay clean.

Introduced with the RBAC engine because RBAC failures needed the same treatment as sign-in failures
and the alternative was a second parallel taxonomy plus a second handler. The request-id contract
requires one shared handler rather than per-route shaping, and two bases would have made "one"
a lie the moment `request_id` lands in that handler.

Subclass this, not `Exception`, for anything a route may raise. The handler in
`noa_api.api.errors` maps class → status, so an unmapped subclass is a visible test
failure rather than a wrong status code in production.
"""

from __future__ import annotations


class NoaError(Exception):
    """Base for every client-visible NOA failure.

    Subclasses override `error_code` and `message`. Callers pass `detail` when there is
    internal context worth logging.
    """

    error_code: str = "internal_error"
    message: str = "Something went wrong. Contact an administrator if this continues."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.message
        super().__init__(self.detail)


class RetryAfterMixin:
    """Marker for a refusal that owes the client a `Retry-After` header.

    A mixin rather than a `NoaError` subclass because the two errors that carry it live in
    different taxonomies — `AuthRateLimitedError` under `AuthError` (login) and
    `McpAuthRateLimitedError` under `McpAuthError` (the MCP request path) — and neither
    tree may absorb the other (see `core.auth.mcp_auth_errors`).

    It exists so the shared handler branches on a declared property instead of listing
    classes: `noa_api.api.errors` used to test `isinstance(error, AuthRateLimitedError)`,
    which silently drops the header for any second rate-limited class. A 429 without
    `Retry-After` leaves the client guessing when to retry.

    Implementors set `retry_after_seconds` in `__init__`, floored at 1 — `Retry-After: 0`
    tells the client to retry immediately, the opposite of a block.
    """

    retry_after_seconds: int
