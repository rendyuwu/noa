"""Lifecycle enums for the tool-call path (V20, T35, T34).

V20 keeps these as *separate* enums rather than one flat set of states, because they
answer different questions about the same call:

- :class:`ToolRisk` classifies the tool — READ or CHANGE. It is not a status; it never
  advances, and it is decided before the call runs.
- :class:`ToolRunStatus` tracks the execution — STARTED, then COMPLETED or FAILED.
- :class:`ActionRequestStatus` tracks the *decision* — PENDING, then APPROVED, DENIED
  or EXPIRED. A decision is not an execution: an APPROVED request whose run then fails
  is two facts, and one column could only hold one of them.

`tool_runs` carries risk and run status as separate columns, which is what makes a
*failed READ* representable. A single flat set would force `FAILED` and `READ` into one
column and lose one of them.

Values are disjoint across all three, so a query written against one column cannot match
rows in another.

`StrEnum` so a member compares equal to its stored string: the column is VARCHAR with a
CHECK constraint (`native_enum=False`), not a Postgres enum type, so reads come back as
plain strings and queries can be written against either form.

Ported from `noa-old` branch `MCP` (`storage/postgres/lifecycle.py`) per C13; values are
verbatim, so rows written by either codebase read the same — except `EXPIRED`, which V20
adds and `noa-old` never had.
"""

from __future__ import annotations

from enum import StrEnum


class ToolRisk(StrEnum):
    """What kind of tool ran (V20).

    READ executes immediately; CHANGE goes through the approval gate first (V16). This
    is a classification of the tool, not a state of the run — see the module docstring.
    """

    READ = "READ"
    CHANGE = "CHANGE"


class ToolRunStatus(StrEnum):
    """How far the execution got (V20).

    STARTED is the row's initial state, written before the tool body runs, so a process
    that dies mid-call leaves evidence rather than nothing (T38's reaper sweeps those).
    COMPLETED and FAILED are terminal.
    """

    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ActionRequestStatus(StrEnum):
    """Whether a CHANGE may run (V20, V23, T34).

    PENDING is the initial state, written by the gate (T33). The three terminal states
    are reached exactly once, under a row lock (V28).

    `EXPIRED` is new here — `noa-old` had only the first three, so a request nobody
    answered stayed PENDING forever and "may this run?" had no truthful answer after the
    TTL passed. V32 makes the expiry terminal; it is written by the decision door on read
    (T37) and by T39's background sweep, which is what makes it true without traffic. The
    same sweep's predicate serves the render path, so a stale PENDING is never served.

    DENIED and EXPIRED are deliberately separate members rather than one "not approved":
    a denial is an operator's answer and carries their reason (V15), while an expiry is
    the absence of one.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


__all__ = ["ActionRequestStatus", "ToolRisk", "ToolRunStatus"]
