"""One asyncio task that runs a pass on an interval, forever.

The loop the expiry sweep built for `PendingExpirySweeper`, hoisted so the stranded-run reaper
inherits it instead
of carrying a second copy. The package docstring lists the four properties; this
module is where each of them lives.

**What this is not.** It is not a scheduler and it holds no state about what a pass does:
`run_pass` is an awaitable-returning callable the owner supplies, and the owner is what
decides where the session comes from and what a pass means. That split is why the sweeper
keeps `run_once()` — the loop swallows, a caller driving a single pass does not.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

import structlog

logger = structlog.get_logger(__name__)


class PeriodicTask:
    """Sleep, run one pass, repeat — and survive a pass that raises.

    `task_name` names the asyncio task, so a dump of running tasks says what this is.
    `failure_event` is the structured log event a failed pass is recorded under; it belongs
    to the owner rather than to this module, because the operator reading it is looking for
    "the expiry sweep failed", not "a periodic task failed".
    """

    def __init__(
        self,
        *,
        task_name: str,
        interval_seconds: float,
        run_pass: Callable[[], Awaitable[object]],
        failure_event: str,
    ) -> None:
        self._task_name = task_name
        self._interval_seconds = interval_seconds
        self._run_pass = run_pass
        self._failure_event = failure_event
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        """Whether a loop task is currently owned by this object."""
        return self._task is not None

    async def start(self) -> None:
        """Begin looping. Idempotent: a second call does not start a second loop.

        Idempotent rather than an error because two loops would both be correct and one of
        them would never be stopped — `stop()` only knows about the task it holds.
        """
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name=self._task_name)

    async def stop(self) -> None:
        """Cancel the loop and wait for it, so no pass outlives what it draws from.

        Awaited rather than fired and forgotten: the app lifespan disposes the engine
        immediately after stopping its background components, and a pass still in flight
        would then be running against a disposed pool.

        Dropping the `cancel()` while keeping the `await` makes this wait on an infinite
        task, which hangs a suite instead of failing it — recorded in the expiry sweep's tests
        because a mutation
        that hangs proves nothing.
        """
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        """The loop itself.

        **Sleep first.** A pass at startup would make every boot touch the database, and
        the health-check rule says `/health` must answer with Postgres down; one interval
        against a deadline
        measured in minutes or hours buys nothing worth that.

        `except Exception` and not `BaseException`: `CancelledError` is a `BaseException` in
        3.11+, so `stop()` still stops this rather than being caught and logged as a failed
        pass. A guarantee that ends the first time the database blinks is not a guarantee.
        """
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self._run_pass()
            except Exception as exc:
                logger.error(
                    self._failure_event,
                    cause=type(exc).__name__,
                    detail=str(exc),
                )
