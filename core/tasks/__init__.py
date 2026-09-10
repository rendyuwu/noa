"""In-process background tasks the app lifespan owns.

One module, one shape: `periodic` holds the interval loop that both of NOA's background
components run on — the expiry loop's `PendingExpirySweeper` (terminality without traffic,
off the TTL) and the stuck-run reaper's `StrandedRunReaper` (the runs a died-mid-call process
left `STARTED`).

Extracted at the second instance rather than the third, unlike `core.servers`' per-system
ref resolvers: those differ at both ends (the row shapes are not the same), while these two
loops differ only in their name, their interval and what one pass does. What is shared is
four properties that are each a decision, and each cost something to get right:

- **it sleeps before its first pass**, so starting the app touches no database;
- it catches `Exception` and not `BaseException`, so `CancelledError` still stops it in 3.11+
  rather than being logged as a failed pass;
- `stop()` cancels **and** awaits, so no pass outlives the engine it draws from;
- `start()` is idempotent, because two loops would both be correct and only one of them
  would ever be stopped.

A second copy of those is a second thing that can drift, and the drift would be silent —
the loop that stopped sleeping first still works.
"""

from core.tasks.periodic import PeriodicTask

__all__ = ["PeriodicTask"]
