"""Resolving runs nobody ever finished.

`tool_runs.status` defaults to `STARTED` so that a process which dies mid-call leaves evidence
rather than nothing. That is only worth having if something eventually resolves those
rows — otherwise `STARTED` means both "running" and "abandoned", and the per-user in-flight cap,
which counts `STARTED` CHANGE runs, spends an operator's allowance on a crash nobody noticed.

Two populations, and the reaper **repairs one and only reports the other.**

**Runs stuck `STARTED`** past `APPROVAL_STRANDED_RUN_REAP_AFTER_SECONDS` are moved to `FAILED`
with a summary that says the outcome is *unknown*. Not "failed": a change interrupted between
its SSH round trip and its terminal write may well have applied on the remote host, and
`ToolRunStatus` offers no third terminal state, so the distinction lives in `result_summary`
where an operator and the audit surface can read it. READ runs are covered too — the tool-run
writer's audit middleware swallows a failed closing write and names this reaper as what resolves
the row.

A stranded run linked to an `action_requests` row also gets a **receipt**, because the
run-plus-receipt-plus-audit rule wants one per approved change and "we do not know how this
ended" is an outcome. It is written through `create_if_missing`, so the executor and the reaper
both reaching one finished run leaves one receipt — which is exactly why the receipt table
stated `UNIQUE (action_request_id)` rather than inheriting it.

**Requests `APPROVED` with no run** are logged and left alone. The decision endpoint writes the
run and the link inside the decision's own transaction, so the decision path cannot produce this
pair. It is still reachable: `action_requests.tool_run_id` is `SET NULL`, so deleting a
`tool_runs` row nulls the link — and in *that* case the change may well have completed. Opening
a `FAILED` run to fill the gap would put a claim in the audit trail that nothing observed, and
the executor's design reserves both the row's creation and the link to the decision endpoint. So
this half is a detector: it says the pair exists and names the request, and a human decides what
it means.

**A pass is BOUNDED, and says so.** Both populations are read under
`APPROVAL_STRANDED_RUN_REAP_BATCH_SIZE`, and a pass that hit the bound reports how many rows it
left behind — a capped pass logging like a complete one reads as "everything is
resolved". The bound is what keeps "a pass is one transaction" affordable: an unbounded pass
after a long outage loads every stranded row, each carrying its request's evidence, and holds
every row lock it takes plus its own xmin until the last of N updates and N receipt inserts
lands. Drain rate is the batch over the interval — 100 per 120s by default; the argument that
this beats the rate stranded rows appear at lives beside the setting in `core.config`.

**The loop is `core.tasks.periodic.PeriodicTask`**, shared with the expiry loop's sweeper — one
session per
pass, sleeps before the first one, survives a pass that raises, stopped by the lifespan before
the engine is disposed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Generic, Protocol, TypeVar
from uuid import UUID

import structlog
from sqlalchemy import ColumnElement, func, select
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

# A pass that reaped runs. Ids and counts, never the rows' contents.
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
    """The envelope a reaped run is recorded with.

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
    # point without a request (the receipt table made that FK NOT NULL).
    action_request_id: UUID | None
    # The gate-time preflight, for the receipt's before-state. `{}` when there is no request.
    evidence: dict[str, Any]


RowT = TypeVar("RowT")


@dataclass(frozen=True)
class BoundedRows(Generic[RowT]):
    """One capped page of a population, carrying the bound it was read under.

    `total` is how many rows matched the pass's predicate, counted in the *same* statement as
    the page through a `count(*) OVER ()` window — Postgres evaluates a window over everything
    `WHERE` admitted, before `ORDER BY` and `LIMIT`, so the page and its total describe one
    snapshot rather than two reads that nearly agree.
    """

    rows: tuple[RowT, ...]
    total: int

    @property
    def remaining(self) -> int:
        """How many the pass left behind. `0` when it saw its whole population.

        Clamped, because `total` is a count of what matched and `rows` is what came back: they
        are one statement apart from nothing today, but a negative backlog is not a number this
        should ever be able to report.
        """
        return max(0, self.total - len(self.rows))


@dataclass(frozen=True)
class ReapOutcome:
    """What one pass did *and what it left*, so a caller can log it or assert on it.

    The two `*_remaining` counts are not diagnostics: a bounded pass that reported only what it
    resolved would read as a pass that resolved everything.
    """

    reaped_run_ids: tuple[UUID, ...]
    receipt_ids: tuple[UUID, ...]
    approved_without_run_ids: tuple[UUID, ...]
    stranded_remaining: int = 0
    approved_without_run_remaining: int = 0

    @property
    def stranded_truncated(self) -> bool:
        """Whether the bound bit. What the row-cap rule says may not go unreported.

        Derived rather than stored, and derived *here* rather than on `BoundedRows`, so there is
        one definition of "this pass was capped" and the log line cannot disagree with the
        outcome an assertion reads.
        """
        return self.stranded_remaining > 0

    @property
    def approved_without_run_truncated(self) -> bool:
        return self.approved_without_run_remaining > 0


class StrandedRunRepository(Protocol):
    """The reads and writes one reap pass may make.

    Both reads take a `limit` and answer with the total behind it. A read that could answer
    with an unbounded tuple is the shape this Protocol exists to make unspellable.
    """

    async def stranded_runs(self, *, cutoff: datetime, limit: int) -> BoundedRows[StrandedRun]: ...

    async def approved_without_run(self, *, cutoff: datetime, limit: int) -> BoundedRows[UUID]: ...

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
    transaction.

    **It can write no status but `FAILED`, and no request column at all.** The status is not a
    parameter of the SQL below in the sense that matters: `ActionRequestExpiryRepository`
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

    async def stranded_runs(self, *, cutoff: datetime, limit: int) -> BoundedRows[StrandedRun]:
        """The oldest `limit` runs still `STARTED` that began at or before `cutoff`, and how
        many there were.

        One statement with an outer join to `action_requests`, so a CHANGE run arrives with the
        evidence its receipt needs and a READ run arrives with `None` — two queries would let a
        row be classified against a request read at a different moment.

        The join is from the request's `tool_run_id`, which is the direction the link is stored
        in, and it is the same edge `core.approvals.reads` joins for the card.

        `<=` on the deadline, matching both expiry doors (the expiry loop's own rule): a row
        exactly on its cutoff
        is one thing, not two.

        **Ordered by `(created_at, id)` before the cut**. Oldest first because the
        oldest stranded run has spent the most of its operator's in-flight-cap allowance, and
        `id` behind
        it because `created_at` is not unique — without a tiebreaker two passes over an
        untouched backlog could each take a different half of a tied group and neither would
        finish it.

        **No `FOR UPDATE`, deliberately** — see `StrandedRunReaperService.reap`.
        """
        result = await self._session.execute(
            select(ToolRun, ActionRequest, func.count().over().label("total"))
            .outerjoin(ActionRequest, ActionRequest.tool_run_id == ToolRun.id)
            .where(*_stranded_run_predicate(cutoff))
            .order_by(ToolRun.created_at, ToolRun.id)
            .limit(limit)
        )
        rows = result.all()
        return BoundedRows(
            rows=tuple(
                StrandedRun(
                    tool_run_id=run.id,
                    tool_name=run.tool_name,
                    action_request_id=None if request is None else request.id,
                    evidence=_evidence_of(request),
                )
                for run, request, _total in rows
            ),
            total=int(rows[0][2]) if rows else 0,
        )

    async def approved_without_run(self, *, cutoff: datetime, limit: int) -> BoundedRows[UUID]:
        """The oldest `limit` APPROVED requests with no run linked, decided at or before
        `cutoff`, and how many there were.

        Unrepresentable through the decision path (the decision endpoint writes both in one
        transaction, state kept in the database) and
        reachable through a deleted run row, whose `SET NULL` nulls this column. The `cutoff`
        keeps a decision committing right now out of the answer — not because the window is
        wide, but because a detector that reports a healthy in-flight approval as an anomaly
        gets ignored.

        **Bounded although it writes nothing**. Read-only buys less than it looks like it
        does: the ids it returns are logged, one line, and a detector that names every row of an
        arbitrarily large population produces a log entry no one can read and a list nothing
        bounds. Ordered `(decided_at, id)` before the cut for the same reason as the runs above.
        """
        result = await self._session.execute(
            select(ActionRequest.id, func.count().over().label("total"))
            .where(*_approved_without_run_predicate(cutoff))
            .order_by(ActionRequest.decided_at, ActionRequest.id)
            .limit(limit)
        )
        rows = result.all()
        return BoundedRows(
            rows=tuple(request_id for request_id, _total in rows),
            total=int(rows[0][1]) if rows else 0,
        )

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


def _stranded_run_predicate(cutoff: datetime) -> tuple[ColumnElement[bool], ...]:
    """What "stranded" means, in one place."""
    return (
        ToolRun.status == ToolRunStatus.STARTED,
        ToolRun.created_at <= cutoff,
    )


def _approved_without_run_predicate(cutoff: datetime) -> tuple[ColumnElement[bool], ...]:
    """What the pair the reaper will not repair looks like, in one place."""
    return (
        ActionRequest.status == ActionRequestStatus.APPROVED,
        ActionRequest.tool_run_id.is_(None),
        ActionRequest.decided_at <= cutoff,
    )


def _evidence_of(request: ActionRequest | None) -> dict[str, Any]:
    """The gate-time preflight off a joined request row, or `{}`.

    Read through `core.approvals.context` rather than by key — that module owns the spelling,
    and a misspelt key in JSONB reads as an absent one.
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
        batch_size: int,
    ) -> None:
        self._repository = repository
        self._reap_after_seconds = reap_after_seconds
        self._batch_size = batch_size

    async def reap(self, *, now: datetime | None = None) -> ReapOutcome:
        """Move up to `batch_size` abandoned runs to `FAILED`, with their receipts, and commit
        once — reporting what the batch left behind.

        One clock read for the whole pass, handed to both predicates, so a run and a request
        are judged against the same moment — the rule `ActionRequestExpiryService.sweep`
        follows, and the reason `core.approvals.clock` exists.

        **A pass is still one transaction, and the batch is what keeps that affordable**.
        The commit runs after both writes for every row in the batch, not per row: a failure
        halfway leaves nothing half-reaped, and the next pass sees the same rows it would have
        seen. Unbounded, that promise was the problem — one pass after a long outage held every
        row lock it took, and its own xmin, across N updates and N receipt inserts, and a
        failure near the end threw the whole pass away for the next one to repeat. Bounded, the
        worst case is `batch_size` rows re-done. Per-batch or per-row commits would buy a
        faster drain and cost the promise; the drain the batch already gives beats the rate
        rows appear at (see `core.config`), so the promise is the better half of that trade.

        **What the pass leaves is part of what it reports.** `stranded_remaining` and
        `approved_without_run_remaining` ride out on the outcome and into the log line, because
        a capped pass reporting only what it resolved reads as one that resolved everything
        (the row-cap rule, the bounded-write-reports-what-it-hid rule).

        **The read takes no row locks, on purpose.** `FOR UPDATE ... SKIP LOCKED` would make
        two reapers' batches disjoint, and it would also make this pass hold each run from the
        moment it read it until the batch commits — so the executor's terminal write for a
        change that *did* complete would queue behind the reaper and then find its own
        `status = STARTED` predicate false. That converts "a change finished a little
        late" into "reported abandoned", deterministically, in the reaper's favour. A repair
        loop must not outrank the worker, so the two writers keep racing on commit order, which
        is the race the first-wins UPDATE predicate already answers. Two replicas therefore both
        reap the same rows: the second's updates match nothing and its receipts conflict away,
        so the *outcome* is right and the cost is duplicated work — a known limit recorded with
        the approved-change executor's design, whose remedy if it ever bites is one advisory
        lock around the pass (that same design's own instrument), not a lock on the rows.
        """
        moment = now_utc(now)
        cutoff = moment - timedelta(seconds=self._reap_after_seconds)

        stranded = await self._repository.stranded_runs(cutoff=cutoff, limit=self._batch_size)
        receipts: list[UUID] = []
        for run in stranded.rows:
            await self._repository.finish_run(
                tool_run_id=run.tool_run_id,
                status=ToolRunStatus.FAILED,
                result_summary=result_summary(abandoned_payload()),
            )
            receipt_id = await self._record_receipt(run)
            if receipt_id is not None:
                receipts.append(receipt_id)

        orphaned = await self._repository.approved_without_run(
            cutoff=cutoff,
            limit=self._batch_size,
        )
        await self._repository.commit()

        outcome = ReapOutcome(
            reaped_run_ids=tuple(run.tool_run_id for run in stranded.rows),
            receipt_ids=tuple(receipts),
            approved_without_run_ids=orphaned.rows,
            stranded_remaining=stranded.remaining,
            approved_without_run_remaining=orphaned.remaining,
        )
        _log(outcome, stranded=stranded.rows, cutoff=cutoff)
        return outcome

    async def _record_receipt(self, run: StrandedRun) -> UUID | None:
        """The receipt for an abandoned *change*, if it belongs to a request.

        `None` for a READ run: `action_receipts.action_request_id` is NOT NULL, and a
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

    **`truncated` and `remaining` ride on both lines**. `count` alone answers "how
    many did this pass resolve", which an operator reads as "how many were there" — the one
    reading a capped pass must not be allowed to make.
    """
    if outcome.reaped_run_ids:
        logger.warning(
            LOG_RUNS_REAPED,
            count=len(outcome.reaped_run_ids),
            cutoff=cutoff.isoformat(),
            tool_run_ids=[str(run_id) for run_id in outcome.reaped_run_ids],
            tools=sorted({run.tool_name for run in stranded}),
            receipts_written=len(outcome.receipt_ids),
            truncated=outcome.stranded_truncated,
            remaining=outcome.stranded_remaining,
        )
    if outcome.approved_without_run_ids:
        logger.error(
            LOG_APPROVED_WITHOUT_RUN,
            count=len(outcome.approved_without_run_ids),
            action_request_ids=[str(request_id) for request_id in outcome.approved_without_run_ids],
            truncated=outcome.approved_without_run_truncated,
            remaining=outcome.approved_without_run_remaining,
        )


class StrandedRunReaper:
    """The background half of the in-process reaper: an asyncio task that resolves abandoned
    runs.

    Same shape as `PendingExpirySweeper` and for the same reasons — one task, its own session
    per pass, started and stopped by the app lifespan that owns the engine it draws from — and
    literally the same loop (`core.tasks.periodic`), so the four properties the expiry loop
    proved hold here
    without a second copy of them.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        interval_seconds: float,
        reap_after_seconds: float,
        batch_size: int,
        repository_factory: Callable[
            [AsyncSession], StrandedRunRepository
        ] = SQLStrandedRunRepository,
    ) -> None:
        self._session_factory = session_factory
        self._reap_after_seconds = reap_after_seconds
        self._batch_size = batch_size
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
                batch_size=self._batch_size,
            )
            return await service.reap()
