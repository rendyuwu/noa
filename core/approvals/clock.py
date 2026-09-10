"""The approval gate's clock — now `core.clock`, re-exported here.

Two doors write the same terminal state for the same event. `ActionDecisionService`
compares `expires_at <= decided_at` when an operator reaches a stale card, and T39's sweep
compares `expires_at <= now` against every row without anyone reaching anything. A second
copy of those three lines is exactly the drift that makes a deadline boundary hold at one
door and not the other — the same argument the CHANGE gate makes for reading the TTL from
settings in one place rather than computing it per caller.

T56 added a fourth reader outside this package — the table surface refuses a parked table
past its own deadline — so the definition moved to `core.clock` and this module became its
re-export. Nothing about the rule changed; what changed is that a module with no business
importing the approval gate can reach the same `now`.
"""

from __future__ import annotations

from core.clock import as_utc, now_utc

__all__ = ["as_utc", "now_utc"]
