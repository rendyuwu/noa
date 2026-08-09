"""Resolving runs nobody ever finished (T38 — V20, V30, V46, V47).

`tool_runs.status` defaults to `STARTED` so that a process which dies mid-call leaves evidence
rather than nothing (T35). That is only worth having if something eventually resolves those
rows — otherwise `STARTED` means both "running" and "abandoned", and V31's cap, which counts
`STARTED` CHANGE runs, spends an operator's allowance on a crash nobody noticed.

Two populations, and the reaper **repairs one and only reports the other.**

**Runs stuck `STARTED`** past `APPROVAL_STRANDED_RUN_REAP_AFTER_SECONDS` are moved to `FAILED`
with a summary that says the outcome is *unknown*. Not "failed": a change interrupted between
its SSH round trip and its terminal write may well have applied on the remote host, and
`ToolRunStatus` offers no third terminal state, so the distinction lives in `result_summary`
where an operator and the audit surface can read it. READ runs are covered too — T73's audit
middleware swallows a failed closing write and names this reaper as what resolves the row.

A stranded run linked to an `action_requests` row also gets a **receipt**, because V46 wants one
per approved change and "we do not know how this ended" is an outcome. It is written through
`create_if_missing`, so the executor and the reaper both reaching one finished run leaves one
receipt — which is exactly why T36 stated `UNIQUE (action_request_id)` rather than inheriting it.

**Requests `APPROVED` with no run** are logged and left alone. T37 writes the run and the link
inside the decision's own transaction (V29), so the decision path cannot produce this pair. It
is still reachable: `action_requests.tool_run_id` is `SET NULL` (T34), so deleting a
`tool_runs` row nulls the link — and in *that* case the change may well have completed. Opening
a `FAILED` run to fill the gap would put a claim in the audit trail that nothing observed, and
`§T.38` reserves both the row's creation and the link to T37. So this half is a detector: it
says the pair exists and names the request, and a human decides what it means.

**The loop is `core.tasks.periodic.PeriodicTask`**, shared with T39's sweeper — one session per
pass, sleeps before the first one, survives a pass that raises, stopped by the lifespan before
the engine is disposed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import now_utc
from core.approvals.context import evidence_from_context
from core.approvals.execution import (
    RECEIPT_AFTER_KEY,
    RECEIPT_BEFORE_KEY,
    RECEIPT_ERROR_CODE_KEY,
    RECEIPT_OK_KEY,
)
from core.audit.receipts import ActionReceiptRepository, SQLActionReceiptRepository
from core.audit.summaries import result_summary
from core.audit.tool_runs import SQLToolRunRepository, ToolRunRepository
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from core.db.models import ActionRequest, ToolRun
from core.db.session import SessionFactory
from core.tasks.periodic import PeriodicTask

# The asyncio task's name, so a dump of running tasks says what this is.
REAP_TASK_NAME: Final = "noa-stranded-run-reaper"

# A pass that reaped runs. Ids and counts, never the rows' contents (V8).
LOG_RUNS_REAPED: Final = "tool_runs_reaped_as_abandoned"

# A pass that found an APPROVED request with no run. Error level: the pair the decision path
# cannot produce, so its presence is worth a human's attention even though nothing is repaired.
LOG_APPROVED_WITHOUT_RUN: Final = "action_request_approved_without_run"

# A pass raised. Logged and swallowed by the loop; the next pass is the remedy.
LOG_REAP_FAILED: Final = "stranded_run_reap_failed"

# What a reaped run's summary says. `ok: False` so it classifies as a failure everywhere the
# envelope is read, and a code that names the *uncertainty* rather than claiming a failure —
# an interrupted change may have applied on the remote host.
ERROR_RUN_ABANDONED: Final = "tool_run_abandoned"

MESSAGE_RUN_ABANDONED: Final = (
    "NOA stopped tracking this run before it reported an outcome. Check the target system "
    "before asking for the change again."
)

logger = structlog.get_logger(__name__)


def abandoned_payload() -> dict[str, Any]:
    """The envelope a reaped run is recorded with (V20, V47).

    Built as the same `{"ok": ..., "error_code": ..., "message": ...}` shape a tool answers
    with, so `result_summary` bounds and redacts it exactly as it does a real result and the
    audit surface has one shape to read.
    """
    return {
        "ok": False,
        RECEIPT_ERROR_CODE_KEY: ERROR_RUN_ABANDONED,
        "message": MESSAGE_RUN_ABANDONED,
    }


@dataclass(frozen=True)
class StrandedRun:
    """A run left `STARTED` past the deadline, with the request it belongs to if it has one."""

    tool_run_id: UUID
    tool_name: str
    # `None` for a READ run, and for a CHANGE run whose `action_requests` row was deleted.
    # Whether a receipt is owed is read off this and nothing else: a receipt has nowhere to
    # point without a request (T36 made that FK NOT NULL).
    action_request_id: UUID | None
    # The gate-time preflight, for the receipt's before-state. `{}` when there is no request.
    evidence: dict[str, Any]


@dataclass(frozen=True)
class ReapOutcome:
    """What one pass did, so a caller can log it or assert on it."""

    reaped_run_ids: tuple[UUID, ...]
    receipt_ids: tuple[UUID, ...]
    approved_without_run_ids: tuple[UUID, ...]


class StrandedRunRepository(Protocol):
    """The reads and writes one reap pass may make (V30, V46)."""

    async def stranded_runs(self, *, cutoff: datetime) -> tuple[StrandedRun, ...]: ...

    async def approved_without_run(self, *, cutoff: datetime) -> tuple[UUID, ...]: ...

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None: ...

    async def record_receipt(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None: ...

    async def commit(self) -> None: ...


class SQLStrandedRunRepository:
    """`StrandedRunRepository` over one `AsyncSession`.

    Composes the two table writers rather than issuing their statements again
    (`core.audit.tool_runs`, `core.audit.receipts`) — one writer per table, and constructing
    both on this session is what puts a reaped run's terminal status and its receipt in one
    transaction (V66).

    **It can write no status but `FAILED`, and no request column at all.** The status is not a
    parameter of the SQL below in the sense that matters: `ActionRequestExpiryRepository` (T39)
    exists because a background loop with no operator behind it must not hold a writer that can
    grant an authorization, and the same applies here — this class never touches
    `action_requests`, so the pair it detects is a read and nothing more.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        runs: ToolRunRepository | None = None,
        receipts: ActionReceiptRepository | None = None,
    ) -> None:
        self._session = session
        self._runs = runs or SQLToolRunRepository(session)
        self._receipts = receipts or SQLActionReceiptRepository(session)

    async def stranded_runs(self, *, cutoff: datetime) -> tuple[StrandedRun, ...]:
        """Every run still `STARTED` that began at or before `cutoff`.

        One statement with an outer join to `action_requests`, so a CHANGE run arrives with the
        evidence its receipt needs and a READ run arrives with `None` — two queries would let a
        row be classified against a request read at a different moment.

        The join is from the request's `tool_run_id`, which is the direction the link is stored
        in (T34), and it is the same edge `core.approvals.reads` joins for the card.

        `<=` on the deadline, matching both expiry doors (T39(a)): a row exactly on its cutoff
        is one thing, not two.
        """
        result = await self._session.execute(
            select(ToolRun, ActionRequest)
            .outerjoin(ActionRequest, ActionRequest.tool_run_id == ToolRun.id)
            .where(
                ToolRun.status == ToolRunStatus.STARTED,
                ToolRun.created_at <= cutoff,
            )
            .order_by(ToolRun.created_at)
        )
        return tuple(
            StrandedRun(
                tool_run_id=run.id,
                tool_name=run.tool_name,
                action_request_id=None if request is None else request.id,
                evidence=_evidence_of(request),
            )
            for run, request in result.all()
        )

    async def approved_without_run(self, *, cutoff: datetime) -> tuple[UUID, ...]:
        """APPROVED requests with no run linked, decided at or before `cutoff`.

        Unrepresentable through the decision path (T37 writes both in one transaction, V29) and
        reachable through a deleted run row, whose `SET NULL` nulls this column. The `cutoff`
        keeps a decision committing right now out of the answer — not because the window is
        wide, but because a detector that reports a healthy in-flight approval as an anomaly
        gets ignored.
        """
        result = await self._session.execute(
            select(ActionRequest.id)
            .where(
                ActionRequest.status == ActionRequestStatus.APPROVED,
                ActionRequest.tool_run_id.is_(None),
                ActionRequest.decided_at <= cutoff,
            )
            .order_by(ActionRequest.decided_at)
        )
        return tuple(result.scalars())

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None:
        await self._runs.finish_run(
            tool_run_id=tool_run_id,
            status=status,
            result_summary=result_summary,
        )

    async def record_receipt(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None:
        return await self._receipts.create_if_missing(
            action_request_id=action_request_id,
            tool_run_id=tool_run_id,
            receipt_data=receipt_data,
        )

    async def commit(self) -> None:
        await self._session.commit()


def _evidence_of(request: ActionRequest | None) -> dict[str, Any]:
    """The gate-time preflight off a joined request row, or `{}`.

    Read through `core.approvals.context` rather than by key — that module owns the spelling,
    and a misspelt key in JSONB reads as an absent one (V66).
    """
    if request is None:
        return {}
    return evidence_from_context(dict(request.approval_context or {}))


class StrandedRunReaperService:
    """One reap pass: resolve abandoned runs, report the pair that cannot be resolved."""

    def __init__(
        self,
        repository: StrandedRunRepository,
        *,
        reap_after_seconds: float,
    ) -> None:
        self._repository = repository
        self._reap_after_seconds = reap_after_seconds

    async def reap(self, *, now: datetime | None = None) -> ReapOutcome:
        """Move every abandoned run to `FAILED`, with its receipt, and commit once.

        One clock read for the whole pass, handed to both predicates, so a run and a request
        are judged against the same moment — the rule `ActionRequestExpiryService.sweep`
        follows, and the reason `core.approvals.clock` exists.

        The commit runs after both writes for every row, not per row: a pass is one
        transaction, so a failure halfway leaves nothing half-reaped, and the next pass sees
        the same rows it would have seen.
        """
        moment = now_utc(now)
        cutoff = moment - timedelta(seconds=self._reap_after_seconds)

        stranded = await self._repository.stranded_runs(cutoff=cutoff)
        receipts: list[UUID] = []
        for run in stranded:
            await self._repository.finish_run(
                tool_run_id=run.tool_run_id,
                status=ToolRunStatus.FAILED,
                result_summary=result_summary(abandoned_payload()),
            )
            receipt_id = await self._record_receipt(run)
            if receipt_id is not None:
                receipts.append(receipt_id)

        orphaned = await self._repository.approved_without_run(cutoff=cutoff)
        await self._repository.commit()

        outcome = ReapOutcome(
            reaped_run_ids=tuple(run.tool_run_id for run in stranded),
            receipt_ids=tuple(receipts),
            approved_without_run_ids=orphaned,
        )
        _log(outcome, stranded=stranded, cutoff=cutoff)
        return outcome

    async def _record_receipt(self, run: StrandedRun) -> UUID | None:
        """The receipt for an abandoned *change*, if it belongs to a request (V46).

        `None` for a READ run: `action_receipts.action_request_id` is NOT NULL (T36), and a
        receipt is a record of what an approved change did — a READ has no approval to be the
        receipt of.

        The before-state is the gate's evidence, the same half the executor writes, so the two
        writers produce one shape. The after-state is the abandonment, which is the honest
        answer: NOA knows the change started and does not know how it ended.
        """
        if run.action_request_id is None:
            return None

        payload = abandoned_payload()
        return await self._repository.record_receipt(
            action_request_id=run.action_request_id,
            tool_run_id=run.tool_run_id,
            receipt_data={
                RECEIPT_OK_KEY: False,
                RECEIPT_BEFORE_KEY: dict(run.evidence),
                RECEIPT_AFTER_KEY: payload,
                RECEIPT_ERROR_CODE_KEY: ERROR_RUN_ABANDONED,
            },
        )


def _log(outcome: ReapOutcome, *, stranded: Sequence[StrandedRun], cutoff: datetime) -> None:
    """One line per population that had anything in it, and silence otherwise.

    A quiet pass is the common case, and a log line per pass would bury the ones that matter.
    """
    if outcome.reaped_run_ids:
        logger.warning(
            LOG_RUNS_REAPED,
            count=len(outcome.reaped_run_ids),
            cutoff=cutoff.isoformat(),
            tool_run_ids=[str(run_id) for run_id in outcome.reaped_run_ids],
            tools=sorted({run.tool_name for run in stranded}),
            receipts_written=len(outcome.receipt_ids),
        )
    if outcome.approved_without_run_ids:
        logger.error(
            LOG_APPROVED_WITHOUT_RUN,
            count=len(outcome.approved_without_run_ids),
            action_request_ids=[str(request_id) for request_id in outcome.approved_without_run_ids],
        )


class StrandedRunReaper:
    """The background half of V30: an asyncio task that resolves abandoned runs.

    Same shape as `PendingExpirySweeper` and for the same reasons — one task, its own session
    per pass, started and stopped by the app lifespan that owns the engine it draws from — and
    literally the same loop (`core.tasks.periodic`), so the four properties T39 proved hold here
    without a second copy of them (V66).
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        interval_seconds: float,
        reap_after_seconds: float,
        repository_factory: Callable[
            [AsyncSession], StrandedRunRepository
        ] = SQLStrandedRunRepository,
    ) -> None:
        self._session_factory = session_factory
        self._reap_after_seconds = reap_after_seconds
        self._repository_factory = repository_factory
        self._loop = PeriodicTask(
            task_name=REAP_TASK_NAME,
            interval_seconds=interval_seconds,
            run_pass=self.run_once,
            failure_event=LOG_REAP_FAILED,
        )

    @property
    def running(self) -> bool:
        """Whether a reap task is currently owned by this reaper."""
        return self._loop.running

    async def start(self) -> None:
        """Begin reaping. Idempotent — see `PeriodicTask.start`."""
        await self._loop.start()

    async def stop(self) -> None:
        """Cancel the loop and wait for it, so no pass outlives the engine it draws from."""
        await self._loop.stop()

    async def run_once(self) -> ReapOutcome:
        """One pass, in its own session and its own transaction. Raises on failure.

        The loop is what swallows; this does not, so a caller driving a single pass (a test, or
        an operator tool later) sees what went wrong.
        """
        async with self._session_factory() as session:
            service = StrandedRunReaperService(
                self._repository_factory(session),
                reap_after_seconds=self._reap_after_seconds,
            )
            return await service.reap()


__all__ = [
    "ERROR_RUN_ABANDONED",
    "LOG_APPROVED_WITHOUT_RUN",
    "LOG_REAP_FAILED",
    "LOG_RUNS_REAPED",
    "MESSAGE_RUN_ABANDONED",
    "REAP_TASK_NAME",
    "ReapOutcome",
    "SQLStrandedRunRepository",
    "StrandedRun",
    "StrandedRunReaper",
    "StrandedRunReaperService",
    "StrandedRunRepository",
    "abandoned_payload",
]
