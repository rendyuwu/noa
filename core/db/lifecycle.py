"""Lifecycle enums for the tool-call path (V20, T35).

V20 keeps these as *separate* enums rather than one flat set of states, because they
answer different questions about the same call:

- :class:`ToolRisk` classifies the tool — READ or CHANGE. It is not a status; it never
  advances, and it is decided before the call runs.
- :class:`ToolRunStatus` tracks the execution — STARTED, then COMPLETED or FAILED.

`tool_runs` carries both as separate columns, which is what makes a *failed READ*
representable. A single flat set would force `FAILED` and `READ` into one column and
lose one of them.

`ActionRequestStatus` — V20's third enum — deliberately does not live here yet. It is
T34's, and V20 adds an `EXPIRED` member that `noa-old` never had, so writing the member
set before the task that specifies it would be a guess (the same reason
`test_schema_v1.py` guards against a table landing ahead of its task).

`StrEnum` so a member compares equal to its stored string: the column is VARCHAR with a
CHECK constraint (`native_enum=False`), not a Postgres enum type, so reads come back as
plain strings and queries can be written against either form.

Ported from `noa-old` branch `MCP` (`storage/postgres/lifecycle.py`) per C13; values are
verbatim, so rows written by either codebase read the same.
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


__all__ = ["ToolRisk", "ToolRunStatus"]
