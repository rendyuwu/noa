"""Refusals from the admin audit surface.

Two classes, and the split is what the caller can do about it: one says "no such run", the other
says "that continuation token is not usable".

`ToolRunNotFoundError` answers **two** causes with one body — no such id, and an id that is not a
UUID at all. Not because either is sensitive (this surface is admin-only) but because a
second, more talkative judge in front of the lookup would be describing what the *validator*
accepts rather than what exists, and a 422 for a malformed id would say that well-formed ids are
the ones worth guessing. The same design choice was made for `noa_get_action_result`,
and the requester-match rule makes it
for the embed; this is the admin spelling of it.

`InvalidAuditCursorError` is a 400: the caller sent a token NOA cannot read, and the remedy is to
re-read from the first page. See `core.audit.cursor` for why it is a `NoaError` rather than
FastAPI's `RequestValidationError`, which is what `noa-old` raised here.

`NoaError` subclasses so `noa_api.api.errors` maps each to a status and the routes raise instead
of building a response — one envelope for the whole app, diagnostics in `detail` and never
in the body.
"""

from __future__ import annotations

from core.errors import NoaError


class ToolRunAuditError(NoaError):
    """Base: something about the audit trail could not be read."""

    error_code: str = "tool_run_audit_failed"
    message: str = "That audit record could not be read."


class ToolRunNotFoundError(ToolRunAuditError):
    """No `tool_runs` row under that id — or the id was not an id (see the module docstring)."""

    error_code: str = "tool_run_not_found"
    message: str = "No tool run with that id."


class InvalidAuditCursorError(ToolRunAuditError):
    """The page token does not decode. One code for every malformed shape."""

    error_code: str = "invalid_audit_cursor"
    message: str = "That page cursor is not valid. Reload the list from the first page."


__all__ = [
    "InvalidAuditCursorError",
    "ToolRunAuditError",
    "ToolRunNotFoundError",
]
