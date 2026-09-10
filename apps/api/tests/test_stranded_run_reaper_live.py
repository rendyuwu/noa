"""The reaper's SQL, against a real Postgres.

What only Postgres can answer:

- **The two predicates.** `status = STARTED AND created_at <= cutoff` for runs;
  `status = APPROVED AND tool_run_id IS NULL AND decided_at <= cutoff` for the pair the
  decision path cannot produce. A double can be told what to return; only the database can be
  asked whether the `WHERE` really says that.
- **`<=`, matching both expiry doors** (check-on-read and the sweep). A row exactly on its cutoff
  has to be one
  thing, and this is the file that can put one there.
- **The join that decides which reaped run owes a receipt.** `action_receipts.action_request_id`
  is NOT NULL, and whether a run has a request is a fact about
  `action_requests.tool_run_id` — the same edge the card joins.
- **`APPROVED` with no run is reachable at all.** The decision endpoint writes the run and the link
  in one
  transaction, so nothing NOA does produces that pair — but `tool_run_id` is `SET NULL`,
  so deleting a run row does. Without this file the detector would be a predicate that has
  never once been true, which is a guard held by nothing — prose is not evidence.
- **The batch, and the ordering it cuts on**. That a pass stops at `batch_size` is
  visible from a double; that the rows beyond it are never *loaded*, that the total behind the
  cut is counted in the same statement, and that `(created_at, id)` decides which rows the cut
  keeps, are all claims about the `SELECT`. `created_at` is not unique, so the tiebreaker is
  the difference between a reproducible cut and one that falls wherever the scan yielded.

Rows come from the production writers wherever one exists: the CHANGE gate opens requests, the
decision approves them and opens their runs, and the tool-run audit path is stood in for by a direct
`SQLToolRunRepository` insert for the READ case, because a READ run's opening write is that
repository's and driving a whole MCP mount here would test the mount.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.approvals.reaper import (
    ERROR_RUN_ABANDONED,
    SQLStrandedRunRepository,
    StrandedRunReaperService,
)
from core.audit.receipts import SQLActionReceiptRepository
from core.audit.tool_runs import SQLToolRunRepository
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import ActionRequest, ToolRun
from support.action_decisions import (
    APPROVAL_CONTEXT,
    REASON,
    build_live_decision_service,
    insert_user,
    open_request,
    read_receipts,
    read_request,
    read_runs,
)
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_stranded_run_reaper_test"

OPERATOR_EMAIL = "operator@example.com"

# The deadline every test here judges against. Rows are aged by backdating `created_at`, not by
# waiting, so this is a fixed number rather than something a test tunes.
REAP_AFTER_SECONDS = 900

READ_TOOL = "whm_list_servers"

# Wide enough that a test which is not about the bound never reaches it. The bounded cases below
# pass their own.
BATCH_SIZE = 100


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


async def reap(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    batch_size: int = BATCH_SIZE,
):  # type: ignore[no-untyped-def]
    """One pass through the production SQL, in its own session."""
    async with factory() as session:
        service = StrandedRunReaperService(
            SQLStrandedRunRepository(session),
            reap_after_seconds=REAP_AFTER_SECONDS,
            batch_size=batch_size,
        )
        return await service.reap(now=now)


async def approved_change(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str = OPERATOR_EMAIL,
) -> tuple[UUID, UUID]:
    """A request opened by the gate and approved by the decision service; returns both ids."""
    user_id = await insert_user(factory, email)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    async with factory() as session:
        service, _ = build_live_decision_service(session)
        outcome = await service.approve(
            action_request_id=request_id,
            caller_user_id=user_id,
            reason=REASON,
        )
    return request_id, outcome.tool_run_id


async def insert_read_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    requested_by_user_id: UUID,
) -> UUID:
    """A `STARTED` READ run, as `ToolRunAuditMiddleware`'s opening write leaves one."""
    async with factory() as session:
        repository = SQLToolRunRepository(session)
        run_id = await repository.start_run(
            tool_name=READ_TOOL,
            requested_by_user_id=requested_by_user_id,
            risk=ToolRisk.READ,
            conversation_ref=None,
            args={},
        )
        await repository.commit()
        return run_id


async def age_run(
    factory: async_sessionmaker[AsyncSession],
    tool_run_id: UUID,
    *,
    created_at: datetime,
) -> None:
    """Backdate a run's `created_at`, which is how a deadline is reached without waiting."""
    async with factory() as session:
        await session.execute(
            sa.update(ToolRun).where(ToolRun.id == tool_run_id).values(created_at=created_at)
        )
        await session.commit()


async def age_decision(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    *,
    decided_at: datetime,
) -> None:
    async with factory() as session:
        await session.execute(
            sa.update(ActionRequest)
            .where(ActionRequest.id == action_request_id)
            .values(decided_at=decided_at)
        )
        await session.commit()


async def unlink_run(
    factory: async_sessionmaker[AsyncSession],
    tool_run_id: UUID,
) -> None:
    """Delete the run row, which nulls `action_requests.tool_run_id` through its `SET NULL` FK.

    This is how the `APPROVED`-with-no-run pair is reachable in production at all, and
    therefore the only honest way to seed the detector's population.
    """
    async with factory() as session:
        await session.execute(sa.delete(ToolRun).where(ToolRun.id == tool_run_id))
        await session.commit()


def moment() -> datetime:
    """A fixed 'now' so cutoff arithmetic in a test is not racing the clock."""
    return datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def cutoff() -> datetime:
    """The deadline a pass at `moment()` judges against."""
    return moment() - timedelta(seconds=REAP_AFTER_SECONDS)


async def stranded_reads(
    factory: async_sessionmaker[AsyncSession],
    *,
    count: int,
) -> list[UUID]:
    """`count` stranded READ runs, oldest first, one second apart.

    READ runs rather than approved changes because the batch is about how many rows a pass
    touches, and a READ run reaches the same predicate without spending an operator's
    in-flight allowance to get there.
    """
    user_id = await insert_user(factory, "reader@example.com")
    run_ids: list[UUID] = []
    for index in range(count):
        run_id = await insert_read_run(factory, requested_by_user_id=user_id)
        await age_run(
            factory,
            run_id,
            created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS + count - index),
        )
        run_ids.append(run_id)
    return run_ids


async def status_of(
    factory: async_sessionmaker[AsyncSession],
    tool_run_id: UUID,
) -> ToolRunStatus:
    return next(row for row in await read_runs(factory) if row.id == tool_run_id).status


# --------------------------------------------------------------------------------------
# The stranded-run predicate
# --------------------------------------------------------------------------------------


async def test_a_run_past_the_deadline_is_reaped(factory) -> None:  # type: ignore[no-untyped-def]
    """The whole point of `STARTED` existing: a process that died leaves a row, and something
    resolves it."""
    _request_id, run_id = await approved_change(factory)
    await age_run(factory, run_id, created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS + 1))

    outcome = await reap(factory, now=moment())

    assert outcome.reaped_run_ids == (run_id,)
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.FAILED
    assert ERROR_RUN_ABANDONED in (run.result_summary or "")
    assert run.completed_at is not None


async def test_a_run_inside_the_deadline_is_left_alone(factory) -> None:  # type: ignore[no-untyped-def]
    """The negative control for the predicate above: a change that is merely slow is not
    abandoned, and reaping it would report an outcome for something still running."""
    _request_id, run_id = await approved_change(factory)
    await age_run(factory, run_id, created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS - 1))

    outcome = await reap(factory, now=moment())

    assert outcome.reaped_run_ids == ()
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.STARTED
    assert await read_receipts(factory) == []


async def test_a_run_exactly_on_its_deadline_is_reaped(factory) -> None:  # type: ignore[no-untyped-def]
    """`<=`, matching both expiry doors (check-on-read and the sweep).

    A row exactly on its cutoff has to be one thing. With `<` it would be neither reaped nor
    running as far as any reader could tell, and the next pass would judge it against a later
    cutoff — so the boundary is only ever crossed by accident.
    """
    _request_id, run_id = await approved_change(factory)
    await age_run(factory, run_id, created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS))

    outcome = await reap(factory, now=moment())

    assert outcome.reaped_run_ids == (run_id,)


async def test_a_terminal_run_is_never_reaped(factory) -> None:  # type: ignore[no-untyped-def]
    """`status = STARTED` is in the predicate: a completed change must not be re-written as
    abandoned, however old it is."""
    _request_id, run_id = await approved_change(factory)
    async with factory() as session:
        repository = SQLToolRunRepository(session)
        await repository.finish_run(
            tool_run_id=run_id,
            status=ToolRunStatus.COMPLETED,
            result_summary='{"ok":true}',
        )
        await repository.commit()
    await age_run(factory, run_id, created_at=moment() - timedelta(days=30))

    outcome = await reap(factory, now=moment())

    assert outcome.reaped_run_ids == ()
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.COMPLETED
    assert run.result_summary == '{"ok":true}'


class InterleavingStrandedRunRepository(SQLStrandedRunRepository):
    """A reap pass with another writer's commit landing *inside* it.

    The window between the pass's `SELECT` and its `UPDATE` is the one that matters, and a
    test that opened it after the pass had finished would prove nothing about the predicate.
    So the competing write is triggered from the read itself, on its own session, and is
    committed before the reap's write is issued.
    """

    def __init__(self, session: AsyncSession, *, on_read) -> None:  # type: ignore[no-untyped-def]
        super().__init__(session)
        self._on_read = on_read

    async def stranded_runs(self, *, cutoff: datetime, limit: int):  # type: ignore[no-untyped-def]
        found = await super().stranded_runs(cutoff=cutoff, limit=limit)
        await self._on_read()
        return found


async def test_a_run_that_finishes_inside_the_pass_is_not_overwritten(factory) -> None:  # type: ignore[no-untyped-def]
    """The executor and the reaper can both reach one run, and the first answer wins.

    A change slower than the deadline is read as stranded, finishes while the pass is still
    open, and must not then be re-written as "outcome never observed" — the receipt its own
    commit made permanent says it completed, and the two would contradict each other.

    Held by `status = STARTED` in the terminal `UPDATE`'s predicate, which Postgres
    re-evaluates against the newest committed row version: drop it and this goes red while
    every single-writer test above stays green.
    """
    request_id, run_id = await approved_change(factory)
    await age_run(factory, run_id, created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS + 1))

    async def executor_finishes() -> None:
        async with factory() as session:
            await SQLToolRunRepository(session).finish_run(
                tool_run_id=run_id,
                status=ToolRunStatus.COMPLETED,
                result_summary='{"ok":true}',
            )
            await SQLActionReceiptRepository(session).create_if_missing(
                action_request_id=request_id,
                tool_run_id=run_id,
                receipt_data={"ok": True, "before": {}, "after": {"ok": True}},
            )
            await session.commit()

    async with factory() as session:
        service = StrandedRunReaperService(
            InterleavingStrandedRunRepository(session, on_read=executor_finishes),
            reap_after_seconds=REAP_AFTER_SECONDS,
            batch_size=BATCH_SIZE,
        )
        await service.reap(now=moment())

    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.COMPLETED
    assert run.result_summary == '{"ok":true}'
    receipts = await read_receipts(factory)
    assert [receipt.receipt_data["ok"] for receipt in receipts] == [True]


# --------------------------------------------------------------------------------------
# The bound one pass is held to
# --------------------------------------------------------------------------------------


async def test_a_pass_reaps_its_batch_and_leaves_the_rest_started(factory) -> None:  # type: ignore[no-untyped-def]
    """More stranded rows than one batch: what the pass did, and what it left behind.

    The `LIMIT` is in the statement, so the rows beyond it are never loaded — which is the
    difference between a bounded pass and a pass that loads everything and then writes some of
    it. What proves the `LIMIT` is real from out here is that the third row is still `STARTED`
    afterwards *and* the pass knew it was there.
    """
    run_ids = await stranded_reads(factory, count=3)

    outcome = await reap(factory, now=moment(), batch_size=2)

    assert len(outcome.reaped_run_ids) == 2
    assert outcome.stranded_remaining == 1
    assert [await status_of(factory, run_id) for run_id in run_ids] == [
        ToolRunStatus.FAILED,
        ToolRunStatus.FAILED,
        ToolRunStatus.STARTED,
    ]


async def test_a_pass_whose_batch_covers_everything_reports_nothing_left(factory) -> None:  # type: ignore[no-untyped-def]
    """The negative control for the test above.

    Same three rows, a batch that fits them: every row terminal and `remaining` zero. Without
    this, "the pass left one behind" could be a count that is simply always non-zero.
    """
    run_ids = await stranded_reads(factory, count=3)

    outcome = await reap(factory, now=moment(), batch_size=3)

    assert len(outcome.reaped_run_ids) == 3
    assert outcome.stranded_remaining == 0
    assert [await status_of(factory, run_id) for run_id in run_ids] == [ToolRunStatus.FAILED] * 3


async def test_the_next_pass_takes_what_the_last_one_left(factory) -> None:  # type: ignore[no-untyped-def]
    """The backlog drains. A bound that stopped the pass without the next one resuming would
    be a reaper that gives up on row `batch_size + 1` forever."""
    run_ids = await stranded_reads(factory, count=3)

    first = await reap(factory, now=moment(), batch_size=2)
    second = await reap(factory, now=moment(), batch_size=2)

    assert set(first.reaped_run_ids) | set(second.reaped_run_ids) == set(run_ids)
    assert first.reaped_run_ids != second.reaped_run_ids
    assert second.stranded_remaining == 0
    assert [await status_of(factory, run_id) for run_id in run_ids] == [ToolRunStatus.FAILED] * 3


async def test_the_batch_takes_the_oldest_runs_first(factory) -> None:  # type: ignore[no-untyped-def]
    """Ordered before the cut, oldest first.

    Oldest first because a stranded run spends its operator's in-flight allowance until it is
    reaped, so the row that has waited longest is the one worth the batch's slot. An unordered cut
    would also make two identical passes over an untouched backlog take different subsets.
    """
    run_ids = await stranded_reads(factory, count=3)

    outcome = await reap(factory, now=moment(), batch_size=1)

    assert outcome.reaped_run_ids == (run_ids[0],)
    assert outcome.stranded_remaining == 2


async def test_runs_sharing_a_timestamp_are_cut_reproducibly(factory) -> None:  # type: ignore[no-untyped-def]
    """`created_at` is not unique, so it cannot be the whole sort key.

    Two rows stamped identically and a batch of one: without a tiebreaker the cut falls
    wherever the scan happened to yield, so two identical calls can answer differently and a
    tied group can be split between passes that never finish it. The ids are chosen rather than
    generated because the claim *is* about ordering on `id` — with random ones the assertion
    would only sometimes separate.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    stale = moment() - timedelta(seconds=REAP_AFTER_SECONDS + 1)
    ids = [UUID(int=index) for index in range(1, 7)]
    async with factory() as session:
        # Inserted highest id first, so insertion order and id order disagree at every position.
        for run_id in reversed(ids):
            session.add(
                ToolRun(
                    id=run_id,
                    tool_name=READ_TOOL,
                    requested_by_user_id=user_id,
                    risk=ToolRisk.READ,
                    status=ToolRunStatus.STARTED,
                    conversation_ref=None,
                    args={},
                    created_at=stale,
                )
            )
        await session.commit()

    async with factory() as session:
        page = await SQLStrandedRunRepository(session).stranded_runs(cutoff=cutoff(), limit=3)

    assert [run.tool_run_id for run in page.rows] == ids[:3]
    assert page.total == 6


async def test_a_batched_pass_writes_a_receipt_only_for_the_changes_it_reaped(factory) -> None:  # type: ignore[no-untyped-def]
    """The receipt half is inside the bound too.

    A pass writes an UPDATE *and* a receipt insert per reaped change, so a bound that capped
    the rows it read but not the receipts it wrote would not be a bound on the transaction. The
    request left behind keeps no receipt until a later pass reaps its run.
    """
    first_request, first_run = await approved_change(factory, email="first@example.com")
    second_request, second_run = await approved_change(factory, email="second@example.com")
    await age_run(factory, first_run, created_at=cutoff() - timedelta(seconds=2))
    await age_run(factory, second_run, created_at=cutoff() - timedelta(seconds=1))

    outcome = await reap(factory, now=moment(), batch_size=1)

    assert outcome.reaped_run_ids == (first_run,)
    assert outcome.stranded_remaining == 1
    receipts = await read_receipts(factory)
    assert [receipt.action_request_id for receipt in receipts] == [first_request]
    assert await status_of(factory, second_run) is ToolRunStatus.STARTED
    assert second_request not in {receipt.action_request_id for receipt in receipts}


async def test_the_detector_is_bounded_and_counts_what_it_did_not_name(factory) -> None:  # type: ignore[no-untyped-def]
    """The read-only half takes the same treatment.

    Nothing is repaired here, but every id it finds goes into one log line — so an unbounded
    detector is an unbounded log entry and an unbounded tuple behind it. Bounded, it names a
    batch and counts the rest.
    """
    for index in range(3):
        request_id, run_id = await approved_change(factory, email=f"orphan{index}@example.com")
        await age_decision(factory, request_id, decided_at=moment() - timedelta(hours=2))
        await unlink_run(factory, run_id)

    outcome = await reap(factory, now=moment(), batch_size=2)

    assert len(outcome.approved_without_run_ids) == 2
    assert outcome.approved_without_run_remaining == 1


# --------------------------------------------------------------------------------------
# Which reaped runs owe a receipt
# --------------------------------------------------------------------------------------


async def test_a_reaped_change_gets_a_receipt_pointing_at_its_request(factory) -> None:  # type: ignore[no-untyped-def]
    """The join is from `action_requests.tool_run_id` — the direction the link is stored in."""
    request_id, run_id = await approved_change(factory)
    await age_run(factory, run_id, created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS + 1))

    await reap(factory, now=moment())

    receipts = await read_receipts(factory)
    assert len(receipts) == 1
    assert receipts[0].action_request_id == request_id
    assert receipts[0].tool_run_id == run_id
    # The before-state is the gate's own evidence, read off `approval_context` through the join.
    assert receipts[0].receipt_data["before"] == APPROVAL_CONTEXT["evidence"]
    assert receipts[0].receipt_data["error_code"] == ERROR_RUN_ABANDONED


async def test_a_reaped_read_run_gets_no_receipt(factory) -> None:  # type: ignore[no-untyped-def]
    """A READ has no approval to be the receipt of, and the FK is NOT NULL.

    It is still reaped: the tool-run middleware swallows a failed closing write and names this
    reaper as what resolves the row, so a stranded READ has to become terminal too.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    run_id = await insert_read_run(factory, requested_by_user_id=user_id)
    await age_run(factory, run_id, created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS + 1))

    outcome = await reap(factory, now=moment())

    assert outcome.reaped_run_ids == (run_id,)
    assert outcome.receipt_ids == ()
    assert await read_receipts(factory) == []
    run = next(row for row in await read_runs(factory) if row.id == run_id)
    assert run.status is ToolRunStatus.FAILED


async def test_one_pass_reaps_a_read_and_a_change_and_writes_one_receipt(factory) -> None:  # type: ignore[no-untyped-def]
    """The negative control for the pair above: the join is what decides, not the order the
    rows happen to be in."""
    _request_id, change_run = await approved_change(factory)
    user_id = await insert_user(factory, "reader@example.com")
    read_run = await insert_read_run(factory, requested_by_user_id=user_id)
    stale = moment() - timedelta(seconds=REAP_AFTER_SECONDS + 1)
    await age_run(factory, change_run, created_at=stale)
    await age_run(factory, read_run, created_at=stale)

    outcome = await reap(factory, now=moment())

    assert set(outcome.reaped_run_ids) == {change_run, read_run}
    receipts = await read_receipts(factory)
    assert [receipt.tool_run_id for receipt in receipts] == [change_run]


async def test_a_second_pass_does_not_double_the_receipt(factory) -> None:  # type: ignore[no-untyped-def]
    """The run is terminal after the first pass, so the second finds nothing — and even if the
    predicate changed, the receipt table's UNIQUE is what keeps "the receipt" singular."""
    _request_id, run_id = await approved_change(factory)
    await age_run(factory, run_id, created_at=moment() - timedelta(seconds=REAP_AFTER_SECONDS + 1))

    await reap(factory, now=moment())
    second = await reap(factory, now=moment())

    assert second.reaped_run_ids == ()
    assert len(await read_receipts(factory)) == 1


# --------------------------------------------------------------------------------------
# The pair it detects and will not repair
# --------------------------------------------------------------------------------------


async def test_an_approved_request_whose_run_was_deleted_is_detected(factory) -> None:  # type: ignore[no-untyped-def]
    """The detector's population, seeded the only way production can reach it.

    `action_requests.tool_run_id` is `SET NULL`, so deleting the run row leaves an
    `APPROVED` request pointing at nothing. Without this test the predicate would be one that has
    never once been true — a guard asserted by prose.
    """
    request_id, run_id = await approved_change(factory)
    await age_decision(factory, request_id, decided_at=moment() - timedelta(hours=2))
    await unlink_run(factory, run_id)

    outcome = await reap(factory, now=moment())

    assert outcome.approved_without_run_ids == (request_id,)
    # Not repaired: no run invented, no link written, no receipt. The change may well have
    # completed before the row was deleted, and a `FAILED` run would say otherwise.
    assert await read_runs(factory) == []
    assert await read_receipts(factory) == []
    request = await read_request(factory, request_id)
    assert request.status is ActionRequestStatus.APPROVED
    assert request.tool_run_id is None


async def test_an_approval_with_its_run_intact_is_not_reported(factory) -> None:  # type: ignore[no-untyped-def]
    """The negative control: the healthy shape the decision endpoint produces must not read as an
    anomaly.

    This is the case that matters most — a detector that fires on every approval is one an
    operator learns to ignore.
    """
    request_id, run_id = await approved_change(factory)
    await age_decision(factory, request_id, decided_at=moment() - timedelta(hours=2))
    await age_run(factory, run_id, created_at=moment() - timedelta(hours=2))

    outcome = await reap(factory, now=moment())

    assert outcome.approved_without_run_ids == ()
    # It is reaped as a stranded run, which is the *other* population — and the point is that
    # the two are told apart.
    assert outcome.reaped_run_ids == (run_id,)


async def test_a_freshly_approved_request_is_not_reported_as_an_anomaly(factory) -> None:  # type: ignore[no-untyped-def]
    """The cutoff on `decided_at`: an approval committing right now is in flight, not broken."""
    _request_id, run_id = await approved_change(factory)
    await unlink_run(factory, run_id)

    outcome = await reap(factory, now=moment())

    assert outcome.approved_without_run_ids == ()


async def test_a_pending_request_is_never_reported(factory) -> None:  # type: ignore[no-untyped-def]
    """`status = APPROVED` is in the predicate. A request nobody answered is the expiry sweep's
    population,
    and the two writers are deliberately different classes."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)

    outcome = await reap(factory, now=moment())

    assert outcome.approved_without_run_ids == ()
    assert (await read_request(factory, request_id)).status is ActionRequestStatus.PENDING
