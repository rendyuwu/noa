"""The reaper and its loop, without a database.

`tool_runs.status` defaults to `STARTED` so a process that dies mid-call leaves evidence rather
than nothing. This is what makes that worth having. Four claims here, none about SQL:

- **What a reaped run records.** `FAILED`, because the enum offers no third terminal state, with
  a summary that says the outcome is *unknown* — an interrupted change may well have applied on
  the remote host, and "failed" would be a claim nobody observed.
- **Which reaped runs owe a receipt.** Those belonging to an `action_requests` row, because
  `action_receipts.action_request_id` is NOT NULL and a READ has no approval to be the
  receipt of.
- **What the reaper will not repair.** `APPROVED`-with-no-run is detected and logged, never
  fixed. The decision path cannot produce that pair (T37 writes both in one transaction, V29);
  a deleted run row can, via `SET NULL` — and in *that* case the change may have completed, so
  opening a `FAILED` run to fill the gap would put a false claim in the audit trail.
- **How much one pass may do, and what it says about the rest.** Both populations are read
  under `batch_size`, and a pass that hit the bound reports what it left — in the outcome and
  in the log line, because a capped pass that logs like a complete one reads as "everything is
  resolved".

The predicates, the cutoff comparison and the ordering the batch cuts on are
`test_stranded_run_reaper_live.py`'s, against a real Postgres. The loop's four properties are
`test_periodic_task.py`'s, proven once for both background components.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from core.approvals.reaper import (
    ERROR_RUN_ABANDONED,
    LOG_APPROVED_WITHOUT_RUN,
    LOG_REAP_FAILED,
    LOG_RUNS_REAPED,
    REAP_TASK_NAME,
    StrandedRunReaper,
    StrandedRunReaperService,
)
from core.db.lifecycle import ToolRunStatus
from support.action_expiry import RecordingSessionFactory
from support.approved_change_execution import (
    EVIDENCE,
    FakeStrandedRunRepository,
    stranded_run,
)

REAP_AFTER_SECONDS = 900

INTERVAL_SECONDS = 0.02

WAIT_TIMEOUT_SECONDS = 2.0

# Wide enough that the tests which are not about the bound never reach it, so a batch assertion
# is only ever made by a test that asked for one.
BATCH_SIZE = 100


async def wait_for(predicate: Callable[[], bool], *, what: str) -> None:
    """Poll until `predicate` holds, or fail saying what never happened."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_TIMEOUT_SECONDS
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


def build_service(
    repository: FakeStrandedRunRepository,
    *,
    batch_size: int = BATCH_SIZE,
) -> StrandedRunReaperService:
    return StrandedRunReaperService(
        repository,
        reap_after_seconds=REAP_AFTER_SECONDS,
        batch_size=batch_size,
    )


def build_reaper(
    repository: FakeStrandedRunRepository,
    factory: RecordingSessionFactory,
    *,
    batch_size: int = BATCH_SIZE,
) -> StrandedRunReaper:
    """The production reaper over doubles — only the session and the repository are fake."""
    return StrandedRunReaper(
        session_factory=factory,
        interval_seconds=INTERVAL_SECONDS,
        reap_after_seconds=REAP_AFTER_SECONDS,
        batch_size=batch_size,
        repository_factory=lambda _session: repository,
    )


# --------------------------------------------------------------------------------------
# What a reaped run records
# --------------------------------------------------------------------------------------


async def test_a_stranded_run_is_moved_to_failed() -> None:
    """`STARTED` must not mean both "running" and "abandoned": V31's cap counts the former."""
    repository = FakeStrandedRunRepository()
    run = stranded_run()
    repository.runs.append(run)

    outcome = await build_service(repository).reap()

    assert outcome.reaped_run_ids == (run.tool_run_id,)
    finish = repository.finishes[0]
    assert finish.tool_run_id == run.tool_run_id
    assert finish.status is ToolRunStatus.FAILED


async def test_the_summary_says_the_outcome_is_unknown_not_that_it_failed() -> None:
    """A change interrupted between its round trip and its terminal write may have applied.

    `FAILED` is the only terminal state the enum offers, so the distinction lives in the summary
    — which is what an operator and the audit surface read. A code naming the *uncertainty*
    rather than a failure is the whole point.
    """
    repository = FakeStrandedRunRepository()
    repository.runs.append(stranded_run())

    await build_service(repository).reap()

    summary = json.loads(repository.finishes[0].result_summary or "")
    assert summary["ok"] is False
    assert summary["error_code"] == ERROR_RUN_ABANDONED
    assert "Check the target system" in summary["message"]


async def test_every_stranded_run_in_a_batch_is_reaped() -> None:
    """A pass that stopped at the first row would leave the rest for the next interval, and the
    cap they are spending with them. The batch is the only thing that stops it."""
    repository = FakeStrandedRunRepository()
    repository.runs.extend([stranded_run(), stranded_run(), stranded_run()])

    outcome = await build_service(repository).reap()

    assert len(outcome.reaped_run_ids) == 3
    assert len(repository.finishes) == 3


async def test_a_quiet_pass_writes_nothing_and_still_commits() -> None:
    """The common case. The commit costs a round trip and keeps the contract free of a branch."""
    repository = FakeStrandedRunRepository()

    outcome = await build_service(repository).reap()

    assert outcome.reaped_run_ids == ()
    assert repository.finishes == []
    assert repository.commits == 1


async def test_a_pass_is_one_transaction() -> None:
    """Per-row commits would leave a pass half-applied when the second row's write failed, and
    the next pass would see a different set than the one it was judging.

    Still true with the batch in place, and the batch is what makes it affordable: the promise
    costs one transaction's worth of row locks, and `batch_size` is the ceiling on that
    worth.
    """
    repository = FakeStrandedRunRepository()
    repository.runs.extend([stranded_run(), stranded_run()])

    await build_service(repository).reap()

    assert repository.commits == 1
    assert repository.journal[-1] == "commit"


# --------------------------------------------------------------------------------------
# The bound a pass is held to, and the bound it reports
# --------------------------------------------------------------------------------------


async def test_a_pass_stops_at_its_batch_size() -> None:
    """The whole fix: one pass writes a bounded number of rows.

    Unbounded, a pass after a long outage loaded every stranded row — each carrying its
    request's evidence — and issued an UPDATE plus a receipt INSERT for every one of them
    inside a single transaction.
    """
    repository = FakeStrandedRunRepository()
    repository.runs.extend(stranded_run() for _ in range(5))

    outcome = await build_service(repository, batch_size=2).reap()

    assert len(outcome.reaped_run_ids) == 2
    assert len(repository.finishes) == 2
    # The bound reached the read, rather than the service slicing a full result after loading
    # it — which is the version of this fix that fixes nothing.
    assert repository.limits == [2, 2]


async def test_a_truncated_pass_reports_how_many_it_left() -> None:
    """V85 one population over: the pass that capped says what the cap hid.

    Five stranded, two reaped, three still stranded — and the caller is told the three, not
    left to infer them from a count that looks complete.
    """
    repository = FakeStrandedRunRepository()
    repository.runs.extend(stranded_run() for _ in range(5))

    outcome = await build_service(repository, batch_size=2).reap()

    assert outcome.stranded_remaining == 3
    assert outcome.stranded_truncated is True


async def test_a_pass_that_saw_its_whole_population_reports_nothing_left() -> None:
    """The negative control for the pair above: `truncated` has to be able to be false,
    or "this pass was capped" is a claim that is always true and therefore says nothing."""
    repository = FakeStrandedRunRepository()
    repository.runs.extend(stranded_run() for _ in range(2))

    outcome = await build_service(repository, batch_size=2).reap()

    assert len(outcome.reaped_run_ids) == 2
    assert outcome.stranded_remaining == 0
    assert outcome.stranded_truncated is False


async def test_a_truncated_pass_says_so_in_the_log() -> None:
    """Where an operator actually reads it.

    `count` alone answers "how many did this pass resolve", which reads as "how many were
    there" — so a capped pass and a complete one would produce the same line with different
    numbers on it, and nothing would say which was which.
    """
    repository = FakeStrandedRunRepository()
    repository.runs.extend(stranded_run() for _ in range(5))

    with capture_logs() as logs:
        await build_service(repository, batch_size=2).reap()

    entry = next(item for item in logs if item["event"] == LOG_RUNS_REAPED)
    assert entry["count"] == 2
    assert entry["truncated"] is True
    assert entry["remaining"] == 3


async def test_a_complete_pass_logs_that_nothing_is_left() -> None:
    """The negative control for the line above: the two passes have to read differently.

    A `truncated` that is always true, or a `remaining` that is always zero, would let the log
    of a capped pass be mistaken for the log of a finished one — the exact reading V85 exists
    to prevent.
    """
    repository = FakeStrandedRunRepository()
    repository.runs.extend(stranded_run() for _ in range(2))

    with capture_logs() as logs:
        await build_service(repository, batch_size=2).reap()

    entry = next(item for item in logs if item["event"] == LOG_RUNS_REAPED)
    assert entry["count"] == 2
    assert entry["truncated"] is False
    assert entry["remaining"] == 0


async def test_the_detector_is_bounded_and_reports_its_own_bound() -> None:
    """Read-only buys less than it looks like it does.

    Nothing is written for this population, but its ids are logged — one line naming every row
    of an arbitrarily large population is a log entry nobody can read, and the tuple behind it
    is still unbounded memory.
    """
    repository = FakeStrandedRunRepository()
    repository.orphans.extend(uuid4() for _ in range(4))

    with capture_logs() as logs:
        outcome = await build_service(repository, batch_size=2).reap()

    assert len(outcome.approved_without_run_ids) == 2
    assert outcome.approved_without_run_remaining == 2
    assert outcome.approved_without_run_truncated is True
    entry = next(item for item in logs if item["event"] == LOG_APPROVED_WITHOUT_RUN)
    assert entry["count"] == 2
    assert entry["truncated"] is True
    assert entry["remaining"] == 2


async def test_a_complete_detector_pass_reports_nothing_left() -> None:
    """The negative control for the detector's bound."""
    repository = FakeStrandedRunRepository()
    repository.orphans.extend(uuid4() for _ in range(2))

    with capture_logs() as logs:
        outcome = await build_service(repository, batch_size=2).reap()

    assert outcome.approved_without_run_remaining == 0
    entry = next(item for item in logs if item["event"] == LOG_APPROVED_WITHOUT_RUN)
    assert entry["truncated"] is False
    assert entry["remaining"] == 0


async def test_the_reaper_hands_its_batch_size_down_to_the_pass() -> None:
    """The setting is the bound, and it reaches the read through the loop's own construction —
    not through a default that a misconfiguration would quietly fall back to."""
    repository = FakeStrandedRunRepository()
    repository.runs.extend(stranded_run() for _ in range(3))

    outcome = await build_reaper(repository, RecordingSessionFactory(), batch_size=1).run_once()

    assert len(outcome.reaped_run_ids) == 1
    assert outcome.stranded_remaining == 2
    assert repository.limits == [1, 1]


async def test_both_populations_are_judged_against_one_moment() -> None:
    """One clock read per pass, handed down — the rule `ActionRequestExpiryService` follows.

    Two reads would let a run and a request be judged against cutoffs that disagree, which is
    the kind of gap `core.approvals.clock` exists to remove.
    """
    repository = FakeStrandedRunRepository()
    moment = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    await build_service(repository).reap(now=moment)

    assert repository.cutoffs == [
        moment - timedelta(seconds=REAP_AFTER_SECONDS),
        moment - timedelta(seconds=REAP_AFTER_SECONDS),
    ]


async def test_a_pass_without_a_moment_uses_an_aware_utc_clock() -> None:
    """Asserted as a bound and a property, not an equality: this value *is* the clock."""
    repository = FakeStrandedRunRepository()
    before = datetime.now(UTC)

    await build_service(repository).reap()

    cutoff = repository.cutoffs[0]
    assert cutoff.tzinfo is not None
    assert before - timedelta(seconds=REAP_AFTER_SECONDS) <= cutoff


# --------------------------------------------------------------------------------------
# The receipt
# --------------------------------------------------------------------------------------


async def test_a_reaped_change_gets_a_receipt() -> None:
    """V46 wants one per approved change, and "we do not know how this ended" is an outcome.

    Its before-state is the gate's evidence — the same half the executor writes — so the two
    writers of this table produce one shape.
    """
    repository = FakeStrandedRunRepository()
    run = stranded_run(action_request_id=uuid4())
    repository.runs.append(run)

    outcome = await build_service(repository).reap()

    assert len(outcome.receipt_ids) == 1
    receipt = repository.receipts[0]
    assert receipt.action_request_id == run.action_request_id
    assert receipt.tool_run_id == run.tool_run_id
    assert receipt.receipt_data["before"] == EVIDENCE
    assert receipt.receipt_data["ok"] is False
    assert receipt.receipt_data["error_code"] == ERROR_RUN_ABANDONED


async def test_a_reaped_read_run_gets_no_receipt() -> None:
    """`action_receipts.action_request_id` is NOT NULL: a READ has no approval to be the
    receipt of, and a receipt has nowhere to point without one.

    READ rows are still reaped — T73's audit middleware swallows a failed closing write and
    names this reaper as what resolves the row.
    """
    repository = FakeStrandedRunRepository()
    repository.runs.append(stranded_run(tool_name="whm_list_servers", action_request_id=None))

    outcome = await build_service(repository).reap()

    assert len(outcome.reaped_run_ids) == 1
    assert outcome.receipt_ids == ()
    assert repository.receipts == []


async def test_a_mixed_pass_reaps_both_and_writes_one_receipt() -> None:
    """The negative control for the two tests above: one pass, both kinds of row, and the
    receipt goes to the one that has a request."""
    repository = FakeStrandedRunRepository()
    change = stranded_run(action_request_id=uuid4())
    read = stranded_run(tool_name="whm_list_servers", action_request_id=None)
    repository.runs.extend([change, read])

    outcome = await build_service(repository).reap()

    assert set(outcome.reaped_run_ids) == {change.tool_run_id, read.tool_run_id}
    assert [receipt.tool_run_id for receipt in repository.receipts] == [change.tool_run_id]


async def test_the_terminal_write_precedes_the_receipt_for_each_run() -> None:
    """Ordering within a row: a receipt written before the status it describes would be durable
    on its own if the pass failed between them — except that it cannot be, because there is one
    commit. Asserted anyway, because the ordering is what makes that true."""
    repository = FakeStrandedRunRepository()
    repository.runs.append(stranded_run(action_request_id=uuid4()))

    await build_service(repository).reap()

    assert repository.journal == [
        "stranded",
        "finish:FAILED",
        "receipt",
        "orphans",
        "commit",
    ]


# --------------------------------------------------------------------------------------
# The pair it will not repair
# --------------------------------------------------------------------------------------


async def test_an_approved_request_with_no_run_is_reported_and_left_alone() -> None:
    """Detected, never repaired — see the module docstring.

    `action_requests.tool_run_id` is `SET NULL`, so this pair is reachable by deleting a run row,
    and then the change may well have completed. Opening a `FAILED` run to fill the gap would be
    a claim nothing observed; §T.38 also reserves the run's creation and the link to T37.
    """
    repository = FakeStrandedRunRepository()
    orphan = uuid4()
    repository.orphans.append(orphan)

    with capture_logs() as logs:
        outcome = await build_service(repository).reap()

    assert outcome.approved_without_run_ids == (orphan,)
    assert repository.finishes == []
    assert repository.receipts == []
    entry = next(item for item in logs if item["event"] == LOG_APPROVED_WITHOUT_RUN)
    assert entry["action_request_ids"] == [str(orphan)]


async def test_a_pass_with_nothing_in_either_population_logs_nothing() -> None:
    """A quiet pass is the common case, and a line per pass would bury the ones that matter."""
    repository = FakeStrandedRunRepository()

    with capture_logs() as logs:
        await build_service(repository).reap()

    assert [
        item for item in logs if item["event"] in {LOG_RUNS_REAPED, LOG_APPROVED_WITHOUT_RUN}
    ] == []


async def test_a_reaping_pass_names_what_it_reaped() -> None:
    """V8's bound on it: ids, counts and tool names — never a row's contents."""
    repository = FakeStrandedRunRepository()
    run = stranded_run(action_request_id=uuid4())
    repository.runs.append(run)

    with capture_logs() as logs:
        await build_service(repository).reap()

    entry = next(item for item in logs if item["event"] == LOG_RUNS_REAPED)
    assert entry["count"] == 1
    assert entry["tool_run_ids"] == [str(run.tool_run_id)]
    assert entry["receipts_written"] == 1
    assert "acmeco" not in repr(entry)


# --------------------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------------------


async def test_each_pass_opens_its_own_session() -> None:
    """A session held across passes pins one connection for the life of the process."""
    repository = FakeStrandedRunRepository()
    factory = RecordingSessionFactory()
    reaper = build_reaper(repository, factory)

    await reaper.start()
    try:
        await wait_for(lambda: len(factory.opened) >= 2, what="a second reap pass")
    finally:
        await reaper.stop()

    assert len({id(session) for session in factory.opened}) == len(factory.opened)
    assert factory.closed == factory.opened


async def test_a_pass_commits_inside_the_session_it_opened() -> None:
    """Ordering, not just occurrence: a commit after the close is a commit on nothing."""
    repository = FakeStrandedRunRepository(journal := [])
    factory = RecordingSessionFactory(journal=journal)
    reaper = build_reaper(repository, factory)

    await reaper.start()
    try:
        await wait_for(lambda: "commit" in journal, what="the first committed pass")
    finally:
        await reaper.stop()

    assert journal[:5] == ["session:open", "stranded", "orphans", "commit", "session:close"]


async def test_run_once_raises_rather_than_swallowing() -> None:
    """The loop is what swallows. A caller driving one pass must see what went wrong."""
    repository = FakeStrandedRunRepository()
    repository.fail = RuntimeError("connection reset by peer")

    with pytest.raises(RuntimeError):
        await build_reaper(repository, RecordingSessionFactory()).run_once()


async def test_a_failing_pass_does_not_end_the_loop() -> None:
    """A reaper that dies on a transient error leaves V30 true only while nothing went wrong.

    The loop's own guarantee is `test_periodic_task.py`'s; what this pins is that the reaper is
    on that loop rather than a copy of it, and that its failure event is the one an operator
    would grep for.
    """
    repository = FakeStrandedRunRepository()
    repository.fail = RuntimeError("connection reset by peer")
    factory = RecordingSessionFactory()
    reaper = build_reaper(repository, factory)

    with capture_logs() as logs:
        await reaper.start()
        try:
            await wait_for(lambda: len(factory.opened) >= 2, what="a second failed pass")
            repository.fail = None
            await wait_for(lambda: repository.commits >= 1, what="a pass after the failures")
            assert reaper.running
        finally:
            await reaper.stop()

    assert any(entry["event"] == LOG_REAP_FAILED for entry in logs)


async def test_the_first_pass_waits_one_interval() -> None:
    """No pass at boot: `/health` must answer with Postgres down."""
    repository = FakeStrandedRunRepository()
    factory = RecordingSessionFactory()
    reaper = StrandedRunReaper(
        session_factory=factory,
        interval_seconds=30,
        reap_after_seconds=REAP_AFTER_SECONDS,
        batch_size=BATCH_SIZE,
        repository_factory=lambda _session: repository,
    )

    await reaper.start()
    try:
        await asyncio.sleep(0)
        assert factory.opened == []
    finally:
        await reaper.stop()


async def test_stop_cancels_the_task() -> None:
    """The lifespan disposes the engine right after this returns."""
    reaper = build_reaper(FakeStrandedRunRepository(), RecordingSessionFactory())

    await reaper.start()
    assert {task for task in asyncio.all_tasks() if task.get_name() == REAP_TASK_NAME}

    await reaper.stop()

    assert not {task for task in asyncio.all_tasks() if task.get_name() == REAP_TASK_NAME}
    assert not reaper.running
