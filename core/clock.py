"""The one definition of "now, aware, UTC" (T37, T39, T56 — V32, V66).

Lived in `core.approvals.clock` until T56, when the table surface became a fourth reader
that compares a stored deadline against the present moment and had no business importing
the approval gate to do it. Moved rather than copied, for the reason the original module
gives: a second copy of these three lines is exactly the drift that makes a deadline
boundary hold at one door and not the next. `core.approvals.clock` re-exports both names,
so every caller written against it still reads correctly.

Deadlines compared here: `ActionDecisionService` against `decided_at` when an operator
reaches a stale card (T37), T39's sweep against every pending row, and T56's table surface
against `tool_result_tables.expires_at` on read.

`as_utc` exists because a *double* can hand back a naive datetime where the column cannot:
`DateTime(timezone=True)` always returns aware values, so this is a guard on the seam with
tests and callers, not on Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now_utc(value: datetime | None = None) -> datetime:
    """The caller's moment, or this one. A parameter so a test can pin it.

    Passing a moment in rather than reading the clock twice is what lets an expiry write
    `decided_at` equal to the instant it was judged against, instead of a few microseconds
    after it.
    """
    return as_utc(value) if value is not None else datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Aware UTC. A naive value is assumed UTC rather than rejected."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = ["as_utc", "now_utc"]
