"""One error base for the whole API surface (V73, T9).

Every NOA failure that a client should see carries three fields, and the split is what
lets a single exception handler shape every response:

- `error_code` — stable machine string. Clients and tests branch on this, never on prose.
- `message` — what the operator reads. Says what happened and who can fix it, so nobody
  files a ticket for something they could clear themselves. Safe to render: no
  credential, no directory internals, no configuration state (V8).
- `detail` — optional internal cause, for logs only. Defaults to `message`, and
  `str(exc)` yields it, so tracebacks stay useful while response bodies stay clean.

Introduced with T9 because RBAC failures needed the same treatment as sign-in failures
and the alternative was a second parallel taxonomy plus a second handler. V73 requires
one shared handler rather than per-route shaping, and two bases would have made "one"
a lie the moment `request_id` lands in T64.

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


__all__ = ["NoaError"]
