"""The interval loop both background components run on (T38, T39 — V30, V51).

T39 proved these four properties against `PendingExpirySweeper`. T38 needed the same loop for
its reaper, so the loop moved to `core.tasks.periodic` and the properties are pinned here,
once, against `PeriodicTask` itself (V66). The two owners keep their own tests — what those
assert is that *their* pass does the right thing, and `test_action_request_expiry.py` remains
the sweeper's regression proof that the delegation did not change its behaviour.

Each property below is a decision that cost something to get right:

- **sleep before the first pass**, so starting the app touches no database (V51);
- **`except Exception`, not `BaseException`**, so `stop()` still stops the loop in 3.11+
  instead of the cancellation being logged as a failed pass;
- **`cancel()` and `await`**, so no pass outlives the engine it draws from;
- **idempotent `start()`**, because two loops would both be correct and `stop()` only knows
  about the one it holds.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from structlog.testing import capture_logs

from core.tasks.periodic import PeriodicTask

TASK_NAME = "noa-test-periodic-task"

FAILURE_EVENT = "noa_test_periodic_pass_failed"

# Short enough that a test does not wait on it, long enough that "before the first pass" is
# not a coin flip on a loaded machine. Same numbers `test_action_request_expiry.py` uses.
INTERVAL_SECONDS = 0.02

WAIT_TIMEOUT_SECONDS = 2.0


async def wait_for(predicate: Callable[[], bool], *, what: str) -> None:
    """Poll until `predicate` holds, or fail saying what never happened.

    Polling rather than a fixed sleep: the assertion is that the loop *gets there*, and a
    fixed wait turns a slow machine into a red suite.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_TIMEOUT_SECONDS
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


def live_tasks() -> set[asyncio.Task[None]]:
    """Every task this module's loops own, found by the name they are given."""
    return {task for task in asyncio.all_tasks() if task.get_name() == TASK_NAME}


class Passes:
    """A pass that counts itself, and can be made to fail or to block."""

    def __init__(self) -> None:
        self.count = 0
        self.fail: BaseException | None = None
        self.block: asyncio.Event | None = None

    async def __call__(self) -> None:
        self.count += 1
        if self.block is not None:
            await self.block.wait()
        if self.fail is not None:
            raise self.fail


def build(
    passes: Passes,
    *,
    interval_seconds: float = INTERVAL_SECONDS,
) -> PeriodicTask:
    return PeriodicTask(
        task_name=TASK_NAME,
        interval_seconds=interval_seconds,
        run_pass=passes,
        failure_event=FAILURE_EVENT,
    )


@asynccontextmanager
async def running(task: PeriodicTask) -> AsyncIterator[PeriodicTask]:
    """Start the loop and always stop it, so no test leaks a task into the next."""
    await task.start()
    try:
        yield task
    finally:
        await task.stop()


# --- Sleep first (V51) ---


async def test_the_first_pass_waits_one_interval() -> None:
    """No pass at boot: `/health` must answer with Postgres down (V51), and a startup pass
    would make every boot open a connection to find out whether it can."""
    passes = Passes()

    async with running(build(passes, interval_seconds=30)):
        # One turn of the loop is all the task needs to reach its first `sleep`; a pass
        # before that would already be visible here.
        await asyncio.sleep(0)

        assert passes.count == 0


async def test_the_loop_keeps_running_passes() -> None:
    """The positive control for the assertion above: with a short interval, passes happen."""
    passes = Passes()

    async with running(build(passes)):
        await wait_for(lambda: passes.count >= 3, what="three passes")


# --- Surviving a failure ---


async def test_a_failing_pass_does_not_end_the_loop() -> None:
    """A guarantee that ends the first time the database blinks is not a guarantee."""
    passes = Passes()
    passes.fail = RuntimeError("connection reset by peer")

    async with running(build(passes)) as task:
        await wait_for(lambda: passes.count >= 2, what="a second failed pass")

        passes.fail = None
        await wait_for(lambda: passes.count >= 3, what="a pass after the failures")

        assert task.running


async def test_a_failing_pass_is_logged_under_the_owners_event() -> None:
    """The operator reading the log is looking for "the expiry sweep failed", not "a
    periodic task failed" — which is why the event name belongs to the owner.

    The cause is named too: a loop that swallows without recording what it swallowed turns a
    recurring outage into silence.
    """
    passes = Passes()
    passes.fail = RuntimeError("connection reset by peer")

    with capture_logs() as logs:
        async with running(build(passes)):
            await wait_for(lambda: passes.count >= 1, what="the first failed pass")

    entry = next(item for item in logs if item["event"] == FAILURE_EVENT)
    assert entry["cause"] == "RuntimeError"
    assert entry["detail"] == "connection reset by peer"


# --- Stopping ---


async def test_stop_cancels_the_task() -> None:
    """The app lifespan disposes the engine right after this returns."""
    task = build(Passes())

    await task.start()
    assert len(live_tasks()) == 1

    await task.stop()

    assert live_tasks() == set()
    assert not task.running


async def test_a_cancelled_pass_ends_the_loop_instead_of_being_logged() -> None:
    """`CancelledError` is a `BaseException` in 3.11+, and that is load-bearing (V30).

    Caught by `except Exception`, a cancellation would be logged as an ordinary failed pass
    and the loop would come back round to `sleep` — so `stop()` would cancel a task that
    refuses to end, `await task` would never return, and the engine would be disposed under a
    live loop.

    **Asserted without calling `stop()`, on purpose.** `stop()` awaits the task it cancelled,
    so a loop that swallows its own cancellation makes *any* test that stops it hang, and a
    hang names nothing (T39 recorded the same trap for a dropped `cancel()`). Raising the
    cancellation from inside the pass reaches the same branch and comes back as a named red.
    """
    passes = Passes()
    passes.fail = asyncio.CancelledError()
    task = build(passes)

    try:
        with capture_logs() as logs:
            await task.start()
            await wait_for(lambda: passes.count >= 1, what="the pass that raises cancellation")
            await wait_for(
                lambda: live_tasks() == set(),
                what="the loop to end on the cancellation rather than swallow it",
            )

        assert [item for item in logs if item["event"] == FAILURE_EVENT] == []
        # One pass, not a stream of them: the loop did not resume after the cancellation.
        assert passes.count == 1
    finally:
        # Cancel without awaiting: on a failing run the loop is exactly the thing that will
        # not end, so awaiting it here would turn the red above into a hang.
        for leaked in live_tasks():
            leaked.cancel()


async def test_stopping_a_task_that_never_started_is_a_no_op() -> None:
    """A failed startup unwinds through the same `finally` a successful one does."""
    await build(Passes()).stop()


# --- Idempotent start ---


async def test_starting_twice_runs_one_loop() -> None:
    """Two loops would both be correct and `stop()` would only know about one of them."""
    task = build(Passes())

    await task.start()
    await task.start()

    try:
        assert len(live_tasks()) == 1
    finally:
        await task.stop()

    assert live_tasks() == set()


async def test_a_stopped_task_can_be_started_again() -> None:
    """`stop()` releases the slot rather than latching it shut — a lifespan that restarts
    the app in one process (the test suite does) must get a working loop back."""
    passes = Passes()
    task = build(passes)

    await task.start()
    await task.stop()
    await task.start()
    try:
        await wait_for(lambda: passes.count >= 1, what="a pass after the restart")
    finally:
        await task.stop()


async def test_the_task_carries_its_owners_name() -> None:
    """A dump of running tasks has to say which loop it is looking at — which is also what
    `live_tasks()` above finds this module's loops by, so every assertion here depends on it."""
    task = build(Passes())

    await task.start()
    try:
        assert {asyncio.current_task()} != live_tasks()
        assert {found.get_name() for found in live_tasks()} == {TASK_NAME}
    finally:
        await task.stop()
