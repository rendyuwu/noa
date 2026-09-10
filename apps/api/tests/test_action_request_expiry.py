"""The expiry service and its loop, without a database.

Two claims live here and neither is about SQL:

- **The service** expires what is due, commits it, and judges every row against *one* moment.
  The last one matters more than it looks: two clock reads inside one pass would let a row
  land `EXPIRED` with a `decided_at` it was never actually judged against.
- **The loop** sleeps before its first pass, opens a fresh session per pass, survives a pass
  that raises, and stops when the app stops. A sweeper that dies on the first transient
  database error would leave the TTL's "terminality without traffic" true only while nothing
  ever went wrong — which is the state it exists to prevent.

The SQL itself — the predicate, the `RETURNING`, and the row lock the one-decision rule leans
on — is
`test_action_request_expiry_live.py`'s, against a real Postgres.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from core.approvals.expiry import (
    SWEEP_TASK_NAME,
    ActionRequestExpiryService,
    PendingExpirySweeper,
)
from core.db.lifecycle import ActionRequestStatus
from support.action_expiry import (
    FakeActionRequestExpiryRepository,
    RecordingSessionFactory,
    pending_row,
)

# Short enough that a test does not wait on it, long enough that "before the first pass" is
# not a coin flip on a loaded machine.
INTERVAL_SECONDS = 0.02

# Ceiling for the polling helper below. Never reached on a passing run; it exists so a broken
# loop fails with a message instead of hanging the suite.
WAIT_TIMEOUT_SECONDS = 2.0


async def wait_for(predicate: Callable[[], bool], *, what: str) -> None:
    """Poll until `predicate` holds, or fail saying what never happened.

    Polling rather than a fixed `asyncio.sleep(n * INTERVAL)`: the assertion is that the loop
    *gets there*, and a fixed wait turns a slow machine into a red suite — the flake habit of
    comparing what the clock stamps, one axis over.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_TIMEOUT_SECONDS
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


def sweep_tasks() -> set[asyncio.Task[None]]:
    """Every live sweep task, found by the name the sweeper gives its task."""
    return {task for task in asyncio.all_tasks() if task.get_name() == SWEEP_TASK_NAME}


def build_sweeper(
    repository: FakeActionRequestExpiryRepository,
    factory: RecordingSessionFactory,
    *,
    interval_seconds: float = INTERVAL_SECONDS,
) -> PendingExpirySweeper:
    """The production sweeper over doubles — only the session and the repository are fake."""
    return PendingExpirySweeper(
        session_factory=factory,
        interval_seconds=interval_seconds,
        repository_factory=lambda _session: repository,
    )


@asynccontextmanager
async def running(sweeper: PendingExpirySweeper) -> AsyncIterator[PendingExpirySweeper]:
    """Start the loop and always stop it, so no test leaks a task into the next."""
    await sweeper.start()
    try:
        yield sweeper
    finally:
        await sweeper.stop()


# --------------------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------------------


async def test_the_sweep_expires_every_due_request_and_reports_them() -> None:
    """Past the deadline is terminal, and the pass says which rows it moved."""
    repository = FakeActionRequestExpiryRepository()
    due = repository.add(pending_row(expires_in_seconds=-5))
    also_due = repository.add(pending_row(expires_in_seconds=-3600))
    live = repository.add(pending_row(expires_in_seconds=3600))

    expired = await ActionRequestExpiryService(repository).sweep()

    assert set(expired) == {due.action_request_id, also_due.action_request_id}
    assert due.status is ActionRequestStatus.EXPIRED
    assert also_due.status is ActionRequestStatus.EXPIRED
    assert live.status is ActionRequestStatus.PENDING


async def test_an_expiry_carries_no_reason() -> None:
    """An expiry is the *absence* of an answer, so the field that holds one stays NULL.

    The table's CHECK deliberately exempts EXPIRED — which is exactly why nothing at the
    database level would catch an expiry that carried a reason, and why this is asserted here.
    """
    repository = FakeActionRequestExpiryRepository()
    due = repository.add(pending_row(expires_in_seconds=-5))

    await ActionRequestExpiryService(repository).sweep()

    assert due.reason is None
    assert due.decided_at is not None


async def test_the_sweep_commits_after_the_write() -> None:
    """A write nobody committed is a row that still reads PENDING to everyone else."""
    repository = FakeActionRequestExpiryRepository()
    repository.add(pending_row(expires_in_seconds=-5))

    await ActionRequestExpiryService(repository).sweep()

    assert repository.journal == ["expire", "commit"]


async def test_every_row_in_a_pass_is_judged_against_one_moment() -> None:
    """One clock read per pass, passed down — never a second one inside the repository."""
    repository = FakeActionRequestExpiryRepository()
    repository.add(pending_row(expires_in_seconds=-5))
    moment = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

    await ActionRequestExpiryService(repository).sweep(now=moment)

    assert repository.judged_at == [moment]


async def test_a_pass_without_a_moment_uses_an_aware_utc_clock() -> None:
    """Asserted as a bound and a property, not an equality: this value *is* the clock."""
    repository = FakeActionRequestExpiryRepository()
    before = datetime.now(UTC)

    await ActionRequestExpiryService(repository).sweep()

    judged = repository.judged_at[0]
    assert judged.tzinfo is not None
    assert before <= judged <= datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Check-on-read (the action-result tool calls this today, the approval card next)
# --------------------------------------------------------------------------------------


async def test_check_on_read_expires_a_due_request_and_reports_it() -> None:
    """The card must not render a PENDING nobody may act on any more."""
    repository = FakeActionRequestExpiryRepository()
    due = repository.add(pending_row(expires_in_seconds=-5))

    expired = await ActionRequestExpiryService(repository).expire_if_due(
        action_request_id=due.action_request_id
    )

    assert expired is True
    assert due.status is ActionRequestStatus.EXPIRED


async def test_check_on_read_leaves_a_live_request_pending() -> None:
    repository = FakeActionRequestExpiryRepository()
    live = repository.add(pending_row(expires_in_seconds=3600))

    expired = await ActionRequestExpiryService(repository).expire_if_due(
        action_request_id=live.action_request_id
    )

    assert expired is False
    assert live.status is ActionRequestStatus.PENDING


async def test_check_on_read_touches_only_the_request_it_was_given() -> None:
    """The id narrows the sweep's statement; it must not widen it back out."""
    repository = FakeActionRequestExpiryRepository()
    asked_about = repository.add(pending_row(expires_in_seconds=-5))
    someone_elses = repository.add(pending_row(expires_in_seconds=-5))

    await ActionRequestExpiryService(repository).expire_if_due(
        action_request_id=asked_about.action_request_id
    )

    assert asked_about.status is ActionRequestStatus.EXPIRED
    assert someone_elses.status is ActionRequestStatus.PENDING


async def test_check_on_read_commits_even_when_nothing_was_due() -> None:
    """The contract is "what you read next is durable", with no branch to get wrong."""
    repository = FakeActionRequestExpiryRepository()

    expired = await ActionRequestExpiryService(repository).expire_if_due(action_request_id=uuid4())

    assert expired is False
    assert repository.commits == 1


async def test_check_on_read_cannot_expire_an_already_decided_request() -> None:
    """The predicate is `status = PENDING`, so a decision is not re-writable from here.

    A render path that could re-stamp a decided row would be a second door on the
    authorization — the thing this repository exists to *not* be.
    """
    repository = FakeActionRequestExpiryRepository()
    decided = repository.add(
        pending_row(expires_in_seconds=-5, status=ActionRequestStatus.APPROVED)
    )

    expired = await ActionRequestExpiryService(repository).expire_if_due(
        action_request_id=decided.action_request_id
    )

    assert expired is False
    assert decided.status is ActionRequestStatus.APPROVED


# --------------------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------------------


async def test_the_first_pass_waits_one_interval() -> None:
    """No sweep at boot: `/health` answers 200 with Postgres down, and a pass at
    startup would make every boot open a connection to find that out."""
    repository = FakeActionRequestExpiryRepository()
    factory = RecordingSessionFactory()

    async with running(build_sweeper(repository, factory, interval_seconds=30)):
        # One turn of the loop is all the task needs to reach its first `sleep`; a pass
        # before that would already be visible here.
        await asyncio.sleep(0)

        assert factory.opened == []
        assert repository.journal == []


async def test_each_pass_opens_its_own_session() -> None:
    """A session held across passes pins one connection for the life of the process."""
    repository = FakeActionRequestExpiryRepository()
    factory = RecordingSessionFactory()

    async with running(build_sweeper(repository, factory)):
        await wait_for(lambda: len(factory.opened) >= 2, what="a second sweep pass")

    assert len({id(session) for session in factory.opened}) == len(factory.opened)
    # Every session opened was also closed — a pass that leaked one would show up here
    # rather than as a pool exhaustion much later.
    assert factory.closed == factory.opened


async def test_a_pass_commits_inside_the_session_it_opened() -> None:
    """Ordering, not just occurrence: a commit after the close is a commit on nothing."""
    repository = FakeActionRequestExpiryRepository(journal := [])
    factory = RecordingSessionFactory(journal=journal)

    async with running(build_sweeper(repository, factory)):
        await wait_for(lambda: "commit" in journal, what="the first committed pass")

    assert journal[:4] == ["session:open", "expire", "commit", "session:close"]


async def test_a_failing_pass_does_not_end_the_loop() -> None:
    """The TTL without this is a guarantee that lasts until Postgres first blinks."""
    repository = FakeActionRequestExpiryRepository()
    repository.fail = RuntimeError("connection reset by peer")
    factory = RecordingSessionFactory()

    async with running(build_sweeper(repository, factory)) as sweeper:
        await wait_for(lambda: len(repository.judged_at) >= 2, what="a second failed pass")

        repository.fail = None
        repository.add(pending_row(expires_in_seconds=-5))
        await wait_for(lambda: repository.commits >= 1, what="a pass after the failures")

        assert sweeper.running


async def test_a_session_that_cannot_be_opened_does_not_end_the_loop() -> None:
    """The other half of the same failure: the database is unreachable, not merely angry."""
    repository = FakeActionRequestExpiryRepository()
    factory = RecordingSessionFactory()
    factory.fail = RuntimeError("could not connect")

    async with running(build_sweeper(repository, factory)) as sweeper:
        await asyncio.sleep(INTERVAL_SECONDS * 3)
        assert sweeper.running

        factory.fail = None
        await wait_for(lambda: len(factory.opened) >= 1, what="a pass after the outage")


async def test_run_once_raises_rather_than_swallowing() -> None:
    """The loop is what swallows. A caller driving one pass must see what went wrong."""
    repository = FakeActionRequestExpiryRepository()
    repository.fail = RuntimeError("connection reset by peer")

    with pytest.raises(RuntimeError):
        await build_sweeper(repository, RecordingSessionFactory()).run_once()


async def test_stop_cancels_the_task() -> None:
    """The lifespan disposes the engine right after this returns — the expiry loop's wiring in
    `main`."""
    sweeper = build_sweeper(FakeActionRequestExpiryRepository(), RecordingSessionFactory())

    await sweeper.start()
    assert len(sweep_tasks()) == 1

    await sweeper.stop()

    assert sweep_tasks() == set()
    assert not sweeper.running


async def test_starting_twice_runs_one_loop() -> None:
    """Two loops would both be correct and `stop()` would only know about one of them."""
    sweeper = build_sweeper(FakeActionRequestExpiryRepository(), RecordingSessionFactory())

    await sweeper.start()
    await sweeper.start()

    try:
        assert len(sweep_tasks()) == 1
    finally:
        await sweeper.stop()

    assert sweep_tasks() == set()


async def test_stopping_a_sweeper_that_never_started_is_a_no_op() -> None:
    """A failed startup unwinds through the same `finally` a successful one does."""
    await build_sweeper(FakeActionRequestExpiryRepository(), RecordingSessionFactory()).stop()


async def test_the_loop_keeps_sweeping_after_a_pass_that_expired_nothing() -> None:
    """The common case: most passes find nothing, and that is not a reason to stop."""
    repository = FakeActionRequestExpiryRepository()
    factory = RecordingSessionFactory()

    async with running(build_sweeper(repository, factory)):
        await wait_for(lambda: len(repository.judged_at) >= 3, what="three quiet passes")

        due = repository.add(pending_row(expires_in_seconds=-1))
        await wait_for(
            lambda: due.status is ActionRequestStatus.EXPIRED,
            what="the row to be expired by a later pass",
        )

    # Passes are judged in order, so a later one never looks at an earlier moment.
    assert repository.judged_at == sorted(repository.judged_at)
    assert repository.judged_at[-1] - repository.judged_at[0] < timedelta(seconds=30)
