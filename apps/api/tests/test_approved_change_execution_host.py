"""The asyncio host an approval hands its run to (T38 — V29, V30).

`ApprovedChangeExecutor` is the seam T37 wired; this is the real thing behind it, and V30 fixes
its shape. Four claims, and every one of them is a way the arrangement can fail quietly:

- **`start` returns before the change finishes** (V29). That is what makes the approve endpoint
  a 202 rather than a request held open across an SSH round trip.
- **One session per execution**, opened inside the task — not the approving request's, which is
  closed by then, and not a long-lived one, which would pin a connection for the life of the
  process.
- **Tasks are held**, because the event loop is free to garbage-collect a task nothing
  references, and a change collected mid-flight leaves a `STARTED` row and no explanation.
- **Shutdown cancels and waits** (V30), because the lifespan disposes the engine immediately
  after — and the runs it interrupts stay `STARTED` on purpose, which is the reaper's population.

What one execution *records* is `test_approved_change_execution.py`'s; the SQL is the live
file's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID, uuid4

from structlog.testing import capture_logs

from core.approvals.execution import ApprovedChangeExecutionRepository
from core.approvals.execution_host import (
    LOG_EXECUTION_CANCELLED,
    LOG_EXECUTION_TASK_FAILED,
    AsyncioApprovedChangeExecutor,
    execution_task_name,
)
from core.db.lifecycle import ToolRunStatus
from support.action_decisions import CHANGE_TOOL
from support.action_expiry import RecordingSessionFactory
from support.approved_change_execution import (
    FakeApprovedChangeExecutionRepository,
    RecordingChangeRunner,
    authorized_change,
)

WAIT_TIMEOUT_SECONDS = 2.0

# See `test_start_returns_before_the_change_has_run`: scheduling a task is instant, so any wait
# here means `start` is doing the change itself.
START_TIMEOUT_SECONDS = 1.0

# See `test_stop_cancels_outstanding_executions`: a cancelled change unwinds at once, so any wait
# means `stop` is waiting on something it never cancelled.
STOP_TIMEOUT_SECONDS = 1.0


async def wait_for(predicate: Callable[[], bool], *, what: str) -> None:
    """Poll until `predicate` holds, or fail saying what never happened."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_TIMEOUT_SECONDS
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


def live_tasks(tool_run_id: UUID) -> set[asyncio.Task[None]]:
    """Every execution task for one run, found by the name the host gives it."""
    name = execution_task_name(tool_run_id)
    return {task for task in asyncio.all_tasks() if task.get_name() == name}


class Host:
    """An executor over doubles, with the repositories it built recorded per session.

    `blocked=True` gives the runner a gate this object owns, so a test can hold a change open
    and release it by name instead of assembling a second executor.
    """

    def __init__(self, *, blocked: bool = False) -> None:
        self.runner = RecordingChangeRunner()
        self.gate = asyncio.Event()
        if blocked:
            self.runner.block = self.gate
        self.factory = RecordingSessionFactory()
        self.repositories: list[FakeApprovedChangeExecutionRepository] = []
        self.authorized = authorized_change()
        self.executor = AsyncioApprovedChangeExecutor(
            session_factory=self.factory,
            runners={CHANGE_TOOL: self.runner},
            repository_factory=self._repository_for,
        )

    def _repository_for(self, _session: object) -> ApprovedChangeExecutionRepository:
        repository = FakeApprovedChangeExecutionRepository(authorized=self.authorized)
        self.repositories.append(repository)
        return repository

    async def start(self) -> None:
        await self.executor.start(
            tool_run_id=self.authorized.tool_run_id,
            action_request_id=self.authorized.action_request_id,
        )

    def release(self) -> None:
        """Let a blocked change finish."""
        self.gate.set()

    @property
    def tasks(self) -> set[asyncio.Task[None]]:
        return live_tasks(self.authorized.tool_run_id)


# --- The handoff returns immediately (V29) ---


async def test_start_returns_before_the_change_has_run() -> None:
    """202, not 200: the decision is durable and the change has not happened yet.

    A blocked runner is how "before" is expressed: without the task, `start` could only return
    once the runner did.

    **Bounded**, because an `await self._execute(...)` in place of the `create_task` would make
    `start()` wait on a gate this test has not opened yet — a deadlock, and a hang names nothing
    (the same trap T39 recorded for a dropped `task.cancel()`). One second is four orders of
    magnitude more than scheduling a task needs.
    """
    host = Host(blocked=True)

    try:
        await asyncio.wait_for(host.start(), timeout=START_TIMEOUT_SECONDS)
    except TimeoutError:
        raise AssertionError("start() awaited the change instead of scheduling it") from None

    # Still in flight: `start` returned with the change unfinished, which is the whole claim.
    # Whether the task has already opened its session by now is the scheduler's business —
    # `test_the_session_is_opened_inside_the_task_not_by_start` is where that is pinned, without
    # a `wait_for` in the way.
    assert host.executor.outstanding == 1
    assert host.runner.calls != [] or host.repositories == []

    host.release()
    await wait_for(lambda: host.executor.outstanding == 0, what="the change to finish")


async def test_the_change_actually_runs() -> None:
    """The positive control: a scheduled task is not a change that ran."""
    host = Host()

    await host.start()
    await wait_for(lambda: host.runner.calls != [], what="the runner to be called")
    await wait_for(lambda: host.executor.outstanding == 0, what="the execution to finish")

    assert host.repositories[0].only_finish.status is ToolRunStatus.COMPLETED


# --- One session per execution (V30) ---


async def test_each_execution_opens_its_own_session() -> None:
    """A session held across executions pins one connection for the life of the process."""
    host = Host()

    await host.start()
    await wait_for(lambda: host.executor.outstanding == 0, what="the first execution")
    host.authorized = authorized_change()
    await host.start()
    await wait_for(lambda: host.executor.outstanding == 0, what="the second execution")

    assert len(host.factory.opened) == 2
    assert len({id(session) for session in host.factory.opened}) == 2
    # Every session opened was also closed — a leak shows up here rather than as pool
    # exhaustion much later.
    assert host.factory.closed == host.factory.opened


async def test_the_session_is_opened_inside_the_task_not_by_start() -> None:
    """The approving request's session is closed by the time this runs, so the execution has to
    open its own — and it must not open one on the request's thread of control either, which is
    what "inside the task" means."""
    host = Host()

    await host.start()

    assert host.factory.opened == []
    await wait_for(lambda: host.factory.opened != [], what="the task to open its session")


async def test_a_pass_closes_its_session_even_when_the_execution_fails() -> None:
    """The failure path leaks nothing: the session is a context manager, and the swallow is
    outside it."""
    host = Host()
    host.runner.fail = RuntimeError("boom")

    with capture_logs() as logs:
        await host.start()
        await wait_for(lambda: host.executor.outstanding == 0, what="the failed execution")

    assert host.factory.closed == host.factory.opened
    # A runner that raises is recorded by the service, so the *task* did not fail.
    assert [entry for entry in logs if entry["event"] == LOG_EXECUTION_TASK_FAILED] == []
    assert host.repositories[0].only_finish.status is ToolRunStatus.FAILED


async def test_a_session_that_cannot_be_opened_is_logged_and_not_raised() -> None:
    """There is no caller left to answer: the approve request returned 202 long ago.

    The run stays `STARTED`, which the reaper resolves, and the log line is what names it.
    """
    host = Host()
    host.factory.fail = RuntimeError("could not connect")

    with capture_logs() as logs:
        await host.start()
        await wait_for(lambda: host.executor.outstanding == 0, what="the execution to give up")

    entry = next(item for item in logs if item["event"] == LOG_EXECUTION_TASK_FAILED)
    assert entry["cause"] == "RuntimeError"
    assert entry["tool_run_id"] == str(host.authorized.tool_run_id)


# --- Tasks are held, and shutdown ends them (V30) ---


async def test_the_task_is_named_after_the_run_it_is_executing() -> None:
    """A dump of running tasks has to say which change is in flight, not "Task-47"."""
    host = Host(blocked=True)

    await host.start()
    try:
        assert len(host.tasks) == 1
    finally:
        host.release()
        await wait_for(lambda: host.executor.outstanding == 0, what="the change to finish")


async def test_a_finished_execution_is_no_longer_held() -> None:
    """A long-lived process must not accumulate completed tasks."""
    host = Host()

    await host.start()
    await wait_for(lambda: host.executor.outstanding == 0, what="the execution to finish")

    assert host.tasks == set()


async def test_stop_cancels_outstanding_executions() -> None:
    """The lifespan disposes the engine right after this returns (V30).

    The interrupted run is left `STARTED` deliberately: writing a terminal status here would
    claim an outcome nobody observed, and a change cancelled mid-flight may well have applied on
    the remote host.
    """
    host = Host(blocked=True)
    await host.start()
    await wait_for(lambda: host.runner.calls != [], what="the execution to reach its runner")

    with capture_logs() as logs:
        # Bounded: `stop()` gathers the tasks it cancelled, so dropping the `cancel()` while
        # keeping the gather makes it wait on a change that never ends. A hang names nothing
        # (T39's recorded trap), and unlike the loop's `stop()` there is no `suppress` here for
        # the timeout to be swallowed by.
        try:
            await asyncio.wait_for(host.executor.stop(), timeout=STOP_TIMEOUT_SECONDS)
        except TimeoutError:
            raise AssertionError("stop() did not cancel the change it was waiting for") from None

    assert host.tasks == set()
    assert host.executor.outstanding == 0
    assert host.repositories[0].finishes == []
    assert host.repositories[0].commits == 0
    assert any(entry["event"] == LOG_EXECUTION_CANCELLED for entry in logs)


async def test_stop_waits_for_the_cancellation_to_land() -> None:
    """Awaited rather than fired and forgotten: a pass still unwinding when `dispose()` runs is
    a pass running against a disposed pool.

    The session's `finally` is the observable: if `stop()` returned before the task unwound, the
    session would still be open here.
    """
    host = Host(blocked=True)
    await host.start()
    await wait_for(lambda: host.runner.calls != [], what="the execution to reach its runner")
    assert host.factory.closed == []

    await host.executor.stop()

    assert host.factory.closed == host.factory.opened


async def test_stopping_a_host_with_nothing_in_flight_is_a_no_op() -> None:
    """A failed startup unwinds through the same `finally` a successful one does."""
    host = Host()

    with capture_logs() as logs:
        await host.executor.stop()

    assert [entry for entry in logs if entry["event"] == LOG_EXECUTION_CANCELLED] == []


async def test_a_stopped_host_still_accepts_a_later_approval() -> None:
    """`stop()` empties the set rather than latching the host shut — the test suite builds and
    tears down apps in one process, and a latched executor would break the next one."""
    host = Host()

    await host.executor.stop()
    await host.start()
    await wait_for(lambda: host.executor.outstanding == 0, what="an execution after the stop")

    assert host.repositories[0].only_finish.status is ToolRunStatus.COMPLETED


def test_the_task_name_is_derived_from_the_run() -> None:
    """Two concurrent changes must be distinguishable in a task dump."""
    first, second = uuid4(), uuid4()

    assert execution_task_name(first) != execution_task_name(second)
    assert str(first) in execution_task_name(first)
