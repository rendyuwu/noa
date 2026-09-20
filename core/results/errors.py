"""Refusals from the large-READ table surface.

Two classes, and the split is the same one `core.approvals.errors` makes: what the caller
can do about it.

`ResultTableNotFoundError` is the only one an operator sees, and it answers **four** causes
with one body — no such token, another operator's token, one whose requester was deleted, and
one past its deadline. That is the requester-match rule stated one table over: a code that
varied by cause would be a 403 spelled differently, and would make this origin an oracle for
which tokens exist.

`ResultTableUnavailableError` is the write side. Parking a table is what makes a large READ's
answer readable at all, so a failure there is refused rather than papered over — the
alternative is a tool result carrying a URL to a table that was never stored, which is a dead
link in a transcript that persists.

`NoaError` subclasses for two reasons: `sanitize_tool_errors` passes a `NoaError`'s own
`error_code` through to the model instead of collapsing it, and each class carries its own
`status_code`, so the route raises rather than building a response.
"""

from __future__ import annotations

from core.errors import NoaError


class ResultTableError(NoaError):
    """Base: something about a parked table could not be done."""

    error_code: str = "result_table_failed"
    message: str = "That table could not be read. Try the tool call again."
    # Bare `ResultTableError`: still "that table could not be read", so 503 by decision rather
    # than by falling through, which would answer the same number for a different reason.
    status_code = 503


class ResultTableNotFoundError(ResultTableError):
    """No table this caller may read under that token.

    One code, one message, four causes — see the module docstring. The message says what to
    do next, because the most likely cause is the benign one: the tables expire, and the
    remedy is to run the READ again rather than to ask anybody for access.
    """

    error_code: str = "result_table_not_found"
    message: str = "That table is not available. It may have expired — run the tool again."
    # 404 for all four of its causes — unknown token, another operator's, one whose requester
    # was deleted, and one past its lifetime. The requester-match rule against an existence
    # oracle, spelled
    # against another table: a status that varied by cause would say which tokens are real.
    status_code = 404


class ResultTableUnavailableError(ResultTableError):
    """The table could not be parked, so the READ has no surface to point at.

    Fail-closed, the same shape the CHANGE gate's write failure has: a result whose
    URL leads nowhere is worse than a refusal, because the refusal is visible at the moment
    it happens and the dead link is not.
    """

    error_code: str = "result_table_unavailable"
    message: str = "That result could not be prepared for viewing. Try the tool call again."
    # 503: the rows could not be parked, so the READ has no surface to point at. Retrying the
    # tool call is the remedy, and unlike a CHANGE there is nothing a retry could double —
    # a READ that ran twice changed nothing either time.
    status_code = 503
