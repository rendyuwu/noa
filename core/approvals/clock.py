"""The one definition of "now, aware, UTC" for the approval gate (T37, T39 — V32, V66).

Two doors write the same terminal state for the same event. `ActionDecisionService`
compares `expires_at <= decided_at` when an operator reaches a stale card, and T39's sweep
compares `expires_at <= now` against every row without anyone reaching anything. A second
copy of these three lines is exactly the drift that makes a deadline boundary hold at one
door and not the other — the same argument the CHANGE gate makes for reading the TTL from
settings in one place rather than computing it per caller.

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
