"""The executor's SQL, against a real Postgres (T38 — V23, V29, V46, V47).

Three claims here are claims *about the database* and cannot be made anywhere else:

- **`status = APPROVED AND tool_run_id = :run` is in the statement**, so a row that is PENDING,
  DENIED, EXPIRED, or linked to a different run is never fetched (V23). A double can be told to
  answer `None`; only Postgres can be asked whether the predicate is really there.
- **One receipt per request**, enforced by T36's `UNIQUE (action_request_id)` through
  `ON CONFLICT DO NOTHING`. T36 stated that constraint naming *this* pair as the reason — the
  executor and the reaper can both reach one finished run — so the end-to-end version of that
  sentence belongs here, with the negative control V87 requires.
- **The run's terminal status and the receipt commit together** (V46), which is a property of one
  transaction and not of two calls.

The row under test is always produced by the production writers: T33's gate opens the request,
T37's decision approves it and opens the `STARTED` run. Nothing here hand-inserts an
`action_requests` row, so a change to either writer shows up in this file rather than being
papered over by a fixture that agrees with the test instead of with the code.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.approvals.execution import (
    ERROR_RUNNER_UNAVAILABLE,
    ApprovedChangeExecutionService,
    SQLApprovedChangeExecutionRepository,
)
from core.approvals.reaper import ERROR_RUN_ABANDONED, StrandedRunReaperService
from core.approvals.reaper import SQLStrandedRunRepository as SQLReaperRepository
from core.audit.receipts import RECEIPT_UNIQUE_CONSTRAINT
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from core.db.models import ActionRequest
from support.action_decisions import (
    APPROVAL_CONTEXT,
    CHANGE_TOOL,
    REASON,
    build_live_decision_service,
    insert_user,
    open_request,
    read_receipts,
    read_request,
    read_runs,
)
from support.approved_change_execution import RUNNER_OK, RecordingChangeRunner
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_approved_change_execution_test"

OPERATOR_EMAIL = "operator@example.com"

# Long enough that nothing in this file is reaped by accident; the reaper's own deadline
# behaviour is `test_stranded_run_reaper_live.py`'s.
REAP_AFTER_SECONDS = 900


@pytest.fixture(scope="module")
def database_url() -> AsyncIterator[str]:
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest_asyncio.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def approve(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    *,
    caller_user_id: UUID,
) -> UUID:
    """A real approval through the real decision service; returns the run it started."""
    async with factory() as session:
        service, _ = build_live_decision_service(session)
        outcome = await service.approve(
            action_request_id=action_request_id,
            caller_user_id=caller_user_id,
            reason=REASON,
        )
        return outcome.tool_run_id


async def execute(
    factory: async_sessionmaker[AsyncSession],
    *,
    action_request_id: UUID,
    tool_run_id: UUID,
    runner: RecordingChangeRunner | None = None,
) -> ToolRunStatus | None:
    """One execution, in its own session, through the production SQL."""
    async with factory() as session:
        service = ApprovedChangeExecutionService(
            repository=SQLApprovedChangeExecutionRepository(session),
            runners={} if runner is None else {CHANGE_TOOL: runner},
        )
        return await service.execute_approved_tool_run(
            action_request_id=action_request_id,
            tool_run_id=tool_run_id,
        )


async def reap(factory: async_sessionmaker[AsyncSession]) -> None:
    """One reap pass through the production SQL, for the two-writers case below."""
    async with factory() as session:
        service = StrandedRunReaperService(
            SQLReaperRepository(session),
            # Zero, so every `STARTED` row is past its cutoff: this file's subject is the
            # collision between the two writers, not the deadline.
            reap_after_seconds=0,
        )
        await service.reap()


async def approved_change(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID]:
    """An operator, a request they opened, and the run their approval started."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    run_id = await approve(factory, request_id, caller_user_id=user_id)
    return user_id, request_id, run_id


async def set_status(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    status: ActionRequestStatus,
) -> None:
    """Force a terminal status by hand, to reach V23's refusal for a row that *exists*."""
    async with factory() as session:
        await session.execute(
            sa.update(ActionRequest)
            .where(ActionRequest.id == action_request_id)
            .values(status=status)
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# The happy path (V29, V46, V47)
# --------------------------------------------------------------------------------------


async def test_an_approved_run_reaches_completed_with_a_receipt(factory) -> None:  # type: ignore[no-untyped-def]
    """V29 end to end: the gate opens a request, a decision approves it and opens the run, the
    executor finishes it — and V46's three artifacts all exist.

    Before T38 this run sat `STARTED` for as long as the database existed, which is what made
    `noa_get_action_result` and the approval card answer "started" forever.
    """
    _, request_id, run_id = await approved_change(factory)
    runner = RecordingChangeRunner()

    status = await execute(factory, action_request_id=request_id, tool_run_id=run_id, runner=runner)

    assert status is ToolRunStatus.COMPLETED
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.COMPLETED
    assert json.loads(run.result_summary or "") == RUNNER_OK
    # V47's timing pair: `completed_at` is what makes a duration derivable on read.
    assert run.completed_at is not None
    assert run.completed_at >= run.created_at

    receipts = await read_receipts(factory)
    assert len(receipts) == 1
    assert receipts[0].action_request_id == request_id
    assert receipts[0].tool_run_id == run_id


async def test_the_receipt_carries_the_gates_evidence_as_its_before_state(factory) -> None:  # type: ignore[no-untyped-def]
    """C9/V17/V33: the before-state on the receipt is the preflight the *gate* persisted, so the
    operator's authorisation and the record of what happened describe one moment."""
    _, request_id, run_id = await approved_change(factory)

    await execute(
        factory,
        action_request_id=request_id,
        tool_run_id=run_id,
        runner=RecordingChangeRunner(),
    )

    receipt = (await read_receipts(factory))[0]
    assert receipt.receipt_data["before"] == APPROVAL_CONTEXT["evidence"]
    assert receipt.receipt_data["after"] == RUNNER_OK


async def test_the_approval_row_is_untouched_by_the_execution(factory) -> None:  # type: ignore[no-untyped-def]
    """V23/V28: the row *is* the authorization, and running the change is not a decision.

    The executor holds no writer that can touch `action_requests` at all — the same split that
    keeps T39's expiry repository off the decision path, one table over.
    """
    _, request_id, run_id = await approved_change(factory)
    before = await read_request(factory, request_id)
    reason_before, decided_before = before.reason, before.decided_at

    await execute(
        factory,
        action_request_id=request_id,
        tool_run_id=run_id,
        runner=RecordingChangeRunner(),
    )

    after = await read_request(factory, request_id)
    assert after.status is ActionRequestStatus.APPROVED
    assert after.tool_run_id == run_id
    assert after.reason == reason_before
    assert after.decided_at == decided_before


async def test_a_failed_change_records_failed_and_still_writes_its_receipt(factory) -> None:  # type: ignore[no-untyped-def]
    """A change that refused is a change that happened as far as the audit trail goes."""
    _, request_id, run_id = await approved_change(factory)
    runner = RecordingChangeRunner({"ok": False, "error_code": "ssh_sudo_required"})

    status = await execute(factory, action_request_id=request_id, tool_run_id=run_id, runner=runner)

    assert status is ToolRunStatus.FAILED
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.FAILED
    receipt = (await read_receipts(factory))[0]
    assert receipt.receipt_data["error_code"] == "ssh_sudo_required"


async def test_a_change_with_no_runner_is_terminal_rather_than_stuck(factory) -> None:  # type: ignore[no-untyped-def]
    """Today's reachable path (T22-T29 unbuilt), and the reason it is safe to ship the executor
    first: the operator gets a named answer instead of a run that never moves."""
    _, request_id, run_id = await approved_change(factory)

    status = await execute(factory, action_request_id=request_id, tool_run_id=run_id)

    assert status is ToolRunStatus.FAILED
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert ERROR_RUNNER_UNAVAILABLE in (run.result_summary or "")


# --------------------------------------------------------------------------------------
# The authorization predicate (V23)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [ActionRequestStatus.PENDING, ActionRequestStatus.DENIED, ActionRequestStatus.EXPIRED],
)
async def test_a_request_that_is_not_approved_is_not_executed(factory, status) -> None:  # type: ignore[no-untyped-def]
    """V23: "may this run?" is the row's `status`, and the predicate is in the statement.

    Parameterized over all three non-APPROVED states rather than one, because the guard is a
    single `==` and a mutation to `!=` would still refuse whichever one a single case picked.
    """
    _, request_id, run_id = await approved_change(factory)
    await set_status(factory, request_id, status)
    runner = RecordingChangeRunner()

    outcome = await execute(
        factory, action_request_id=request_id, tool_run_id=run_id, runner=runner
    )

    assert outcome is None
    assert runner.calls == []
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.STARTED
    assert await read_receipts(factory) == []


async def test_a_run_that_is_not_the_requests_own_is_not_executed(factory) -> None:  # type: ignore[no-untyped-def]
    """The pair is the predicate. An id from another request must not unlock this one.

    Reachable without malice: the handoff crosses a task boundary, and a retry written later
    against the wrong column would look exactly like this.
    """
    _, first_request, first_run = await approved_change(factory)
    second_user = await insert_user(factory, "second@example.com")
    second_request = await open_request(factory, requested_by_user_id=second_user)
    second_run = await approve(factory, second_request, caller_user_id=second_user)
    runner = RecordingChangeRunner()

    outcome = await execute(
        factory,
        action_request_id=first_request,
        tool_run_id=second_run,
        runner=runner,
    )

    assert outcome is None
    assert runner.calls == []
    assert {row.id: row.status for row in await read_runs(factory)} == {
        first_run: ToolRunStatus.STARTED,
        second_run: ToolRunStatus.STARTED,
    }


async def test_an_unknown_request_id_is_not_executed(factory) -> None:  # type: ignore[no-untyped-def]
    """The absent case, so the refusal is not only about a status this file wrote by hand."""
    runner = RecordingChangeRunner()

    outcome = await execute(factory, action_request_id=uuid4(), tool_run_id=uuid4(), runner=runner)

    assert outcome is None
    assert runner.calls == []


# --------------------------------------------------------------------------------------
# One receipt (V46, T36's UNIQUE)
# --------------------------------------------------------------------------------------


async def test_executor_and_reaper_together_leave_one_receipt(factory) -> None:  # type: ignore[no-untyped-def]
    """T36 stated `UNIQUE (action_request_id)` naming exactly this pair as the reason.

    A second receipt turns "the receipt" into "some receipt" and a reader picks one arbitrarily.
    The reaper runs first here with a zero deadline, so it finds the `STARTED` row and writes the
    abandonment receipt; the executor then finishes the same run — and the first receipt stands,
    because whichever writer arrives second is a no-op.
    """
    _, request_id, run_id = await approved_change(factory)

    await reap(factory)
    await execute(
        factory,
        action_request_id=request_id,
        tool_run_id=run_id,
        runner=RecordingChangeRunner(),
    )

    receipts = await read_receipts(factory)
    assert len(receipts) == 1
    # The reaper's, because it got there first — and it says the outcome was never observed,
    # which is what it knew at the time.
    assert receipts[0].receipt_data["error_code"] == ERROR_RUN_ABANDONED


async def test_a_second_execution_of_one_run_does_not_double_the_receipt(factory) -> None:  # type: ignore[no-untyped-def]
    """The same collision from the other side: two executions of one authorised run.

    The second is still authorised — nothing here moves `action_requests.status` — so it runs and
    records again, and the receipt it would write is refused by the constraint rather than
    appended.
    """
    _, request_id, run_id = await approved_change(factory)
    runner = RecordingChangeRunner()

    await execute(factory, action_request_id=request_id, tool_run_id=run_id, runner=runner)
    await execute(factory, action_request_id=request_id, tool_run_id=run_id, runner=runner)

    assert len(runner.calls) == 2
    assert len(await read_receipts(factory)) == 1


async def test_two_requests_get_two_receipts(factory) -> None:  # type: ignore[no-untyped-def]
    """The negative control (V87): "the database refuses a second receipt" is a claim about
    nothing if it refuses *every* second receipt.

    Proven to separate rather than reasoned about — the constraint is on `action_request_id`, so
    two requests must each keep their own.
    """
    _, first_request, first_run = await approved_change(factory)
    second_user = await insert_user(factory, "second@example.com")
    second_request = await open_request(factory, requested_by_user_id=second_user)
    second_run = await approve(factory, second_request, caller_user_id=second_user)
    runner = RecordingChangeRunner()

    await execute(factory, action_request_id=first_request, tool_run_id=first_run, runner=runner)
    await execute(factory, action_request_id=second_request, tool_run_id=second_run, runner=runner)

    receipts = await read_receipts(factory)
    assert {receipt.action_request_id for receipt in receipts} == {first_request, second_request}


# --------------------------------------------------------------------------------------
# One transaction (V46)
# --------------------------------------------------------------------------------------


async def test_the_conflict_target_names_a_constraint_the_schema_has(factory) -> None:  # type: ignore[no-untyped-def]
    """`create_if_missing` conflicts on a *named* constraint, so the name has to be real.

    Named rather than inferred on purpose: a bare `ON CONFLICT DO NOTHING` swallows every
    conflict, including one from some future index that has nothing to do with V46's
    one-receipt-per-request rule. The cost of naming it is that a rename in the model or the
    migration breaks every receipt insert — at runtime, loudly, but at runtime. This binds the
    reference instead.
    """
    async with factory() as session:
        result = await session.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'action_receipts'::regclass AND contype = 'u'"
            )
        )
        names = set(result.scalars())

    assert RECEIPT_UNIQUE_CONSTRAINT in names


async def test_a_failing_receipt_write_rolls_back_the_terminal_status(factory) -> None:  # type: ignore[no-untyped-def]
    """A `COMPLETED` run whose receipt never landed is V46 held by nothing.

    The receipt write is broken by handing the repository a receipts writer that raises, so what
    is exercised is the real transaction boundary rather than a claim about it: the run must
    still read `STARTED`, which leaves it to the reaper.
    """

    class ExplodingReceipts:
        async def create_if_missing(self, **_: Any) -> UUID | None:
            raise RuntimeError("receipt insert failed")

    _, request_id, run_id = await approved_change(factory)

    async with factory() as session:
        service = ApprovedChangeExecutionService(
            repository=SQLApprovedChangeExecutionRepository(
                session,
                receipts=ExplodingReceipts(),  # type: ignore[arg-type]
            ),
            runners={CHANGE_TOOL: RecordingChangeRunner()},
        )
        with pytest.raises(RuntimeError):
            await service.execute_approved_tool_run(
                action_request_id=request_id,
                tool_run_id=run_id,
            )

    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.STARTED
    assert run.completed_at is None
    assert await read_receipts(factory) == []
