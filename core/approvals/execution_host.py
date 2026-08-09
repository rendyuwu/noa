"""The async host an approval hands its run to (T38 — V29, V30).

`ApprovedChangeExecutor` is the seam T37 wired and left filled with a placeholder. This is the
real implementation, and V30 fixes its shape: an **in-process asyncio task** with its **own
session**, not a worker process, not a queue, not a cron entry.

**Why in-process.** The alternative is a second deployable, and everything it would need is
already here: the session factory, the integration layer, the settings. What NOA gains from a
broker — durability across a restart — it already has in the database, because `action_requests`
is `APPROVED` and `tool_runs` is `STARTED` before this is ever called (V29), and the reaper is
what resolves the pair a dead process leaves behind. A queue would add an outage mode without
removing one.

**`start` returns as soon as the task is scheduled.** That is what makes the approve endpoint a
202: the decision is durable, the change has not finished, and the embed polls the run to a
terminal state (V29, T42). `start` deliberately does not await the execution — an approval that
waited for a slow SSH round trip would hold the operator's request open and time out in front
of them, for a change that is already authorised and recorded.

**Every task is tracked, and shutdown cancels them.** A bare `create_task` leaves the only
reference to the task with the event loop, which is free to garbage-collect it mid-flight; and
the app lifespan disposes the engine on shutdown, so an execution still running would be
holding a connection from a disposed pool. `stop()` therefore cancels what is outstanding and
awaits it, exactly as `PendingExpirySweeper.stop` does — and the runs it interrupts are left
`STARTED`, which is the state the reaper exists for. Cancelling mid-change is not a loss of
information: the row says a change was authorised and started, and the reaper says nobody ever
recorded how it ended.

**One session per execution, opened inside the task.** Not the approving request's — that one
is closed by the time this runs — and not a long-lived one, which would pin a connection for
the life of the process and answer every later execution through whatever state it was left in
(`PendingExpirySweeper`'s per-pass rule, same argument).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Final
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.execution import (
    ApprovedChangeExecutionRepository,
    ApprovedChangeExecutionService,
    ChangeRunner,
    SQLApprovedChangeExecutionRepository,
)
from core.db.session import SessionFactory

# Prefix of each execution task's name, so a dump of running tasks says which change is in
# flight rather than "Task-47".
EXECUTION_TASK_NAME_PREFIX: Final = "noa-approved-change"

# The execution task raised out of the service. Everything inside the service is already
# recorded on the row, so reaching this means the *recording* failed — the run is left STARTED
# and the reaper is what resolves it.
LOG_EXECUTION_TASK_FAILED: Final = "approved_change_execution_task_failed"

# Shutdown interrupted a change that was still running.
LOG_EXECUTION_CANCELLED: Final = "approved_change_execution_cancelled"

logger = structlog.get_logger(__name__)


def execution_task_name(tool_run_id: UUID) -> str:
    """The asyncio task name for one execution."""
    return f"{EXECUTION_TASK_NAME_PREFIX}:{tool_run_id}"


class AsyncioApprovedChangeExecutor:
    """`ApprovedChangeExecutor` over in-process asyncio tasks (V29, V30).

    One per app, held on `AppRuntime` — a per-request executor would mean a per-request set of
    outstanding tasks, and nothing would be left to cancel them at shutdown.

    `runners` is the tool-name → `ChangeRunner` map (`noa_api.mcp_tools.change_runners`). It is
    empty until T22-T29 land, and an unknown name is a named terminal failure rather than a
    silent one — see `core.approvals.execution`.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        runners: Mapping[str, ChangeRunner],
        repository_factory: Callable[
            [AsyncSession], ApprovedChangeExecutionRepository
        ] = SQLApprovedChangeExecutionRepository,
    ) -> None:
        self._session_factory = session_factory
        self._runners = dict(runners)
        # The same seam every other component here uses: production gets the real repository,
        # and a test can drive the host — its tasks, its one-session-per-execution rule, its
        # shutdown — without a database, while the SQL stays pinned against a real one.
        self._repository_factory = repository_factory
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def outstanding(self) -> int:
        """How many executions are in flight. Read by tests and by shutdown."""
        return len(self._tasks)

    async def start(self, *, tool_run_id: UUID, action_request_id: UUID) -> None:
        """Schedule the execution and return (V29).

        Returns once the task exists, never once the change is done. A failure to *schedule*
        propagates to the caller, where `ActionDecisionService._hand_off` logs and swallows it
        — the approval is committed either way, and the pair it leaves is the reaper's.
        """
        task = asyncio.create_task(
            self._execute(tool_run_id=tool_run_id, action_request_id=action_request_id),
            name=execution_task_name(tool_run_id),
        )
        # Held so the loop is not the only reference (a garbage-collected task stops
        # mid-change) and so `stop()` has something to cancel. Discarded on completion, so a
        # long-lived process does not accumulate finished tasks.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        """Cancel every outstanding execution and wait for them (V30).

        Awaited rather than fired and forgotten: the lifespan disposes the engine right after
        this returns, and an execution still in flight would then be running against a
        disposed pool.

        The runs left behind stay `STARTED` on purpose. Writing a terminal status here would
        claim an outcome nobody observed — a change interrupted mid-flight may well have
        applied on the remote host, and that is precisely the distinction the reaper's summary
        makes.
        """
        outstanding, self._tasks = self._tasks, set()
        if not outstanding:
            return

        for task in outstanding:
            task.cancel()
        results = await asyncio.gather(*outstanding, return_exceptions=True)
        cancelled = sum(1 for result in results if isinstance(result, asyncio.CancelledError))
        if cancelled:
            logger.warning(LOG_EXECUTION_CANCELLED, count=cancelled)

    async def _execute(self, *, tool_run_id: UUID, action_request_id: UUID) -> None:
        """One execution, in its own session (V30).

        Swallows what the service raises, because there is no caller left to answer: the
        approve request returned a 202 long ago and the state lives on the row. What is
        recoverable is recorded there by the service; what is not, this line names, and the run
        it leaves `STARTED` is the reaper's to resolve.

        `BaseException` is not caught — `stop()` cancels these tasks, and swallowing that would
        turn a shutdown into a hang.
        """
        try:
            async with self._session_factory() as session:
                service = ApprovedChangeExecutionService(
                    repository=self._repository_factory(session),
                    runners=self._runners,
                )
                await service.execute_approved_tool_run(
                    action_request_id=action_request_id,
                    tool_run_id=tool_run_id,
                )
        except Exception as exc:
            logger.error(
                LOG_EXECUTION_TASK_FAILED,
                action_request_id=str(action_request_id),
                tool_run_id=str(tool_run_id),
                cause=type(exc).__name__,
                detail=str(exc),
            )


__all__ = [
    "EXECUTION_TASK_NAME_PREFIX",
    "LOG_EXECUTION_CANCELLED",
    "LOG_EXECUTION_TASK_FAILED",
    "AsyncioApprovedChangeExecutor",
    "execution_task_name",
]
