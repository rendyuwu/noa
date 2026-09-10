"""The decision gate against a real Postgres: one answer per request.

`test_action_request_decision_routes.py` drives the same service over an in-memory
repository, which proves the ordering and every refusal but cannot prove the claim that is
*about the database*:

- **V28's row lock.** "Exactly one `pending -> decided` transition" is a statement about what
  happens when two transactions race. A double cannot lose that race, so a double cannot
  demonstrate the invariant — only `SELECT ... FOR UPDATE` on real rows can.

The refusals sit beside it because they are the same question asked without contention: who
may answer this request, and until when. They are asserted against real rows for their own
reason — V27's requester match has to survive a `SET NULL` foreign key the metadata only
describes, and V32's check-on-read has to *commit* the terminal status it discovers.

What an accepted decision then records is `test_action_request_decision_records_live.py`, and
V31's per-user cap is `test_action_request_change_cap_live.py`. Both were split out of this
file when it passed the 900-line limit; all three share the row helpers in
`support.action_decisions` and the scratch-database fixtures in `support.database`.

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.

`SQLActionDecisionRepository` runs for real throughout; only the executor is a recorder,
because T38 is what makes it do anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.approvals.errors import (
    ActionRequestAlreadyDecidedError,
    ActionRequestExpiredError,
    ActionRequestNotFoundError,
    ChangeReasonRequiredError,
)
from core.approvals.repository import SQLActionRequestRepository
from core.db.lifecycle import ActionRequestStatus
from core.db.models import ActionRequest, User
from support.action_decisions import (
    HANDOVER_GRACE_SECONDS,
    REASON,
    ObservedDecisionRepository,
    build_decision_service,
    build_live_decision_service,
    insert_user,
    open_request,
    read_request,
    read_runs,
)
from support.database import migrated_database, session_factory

SCRATCH_DB = "noa_action_decisions_test"

OPERATOR_EMAIL = "operator@example.com"
OTHER_EMAIL = "second-operator@example.com"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory over a freshly emptied database (`support.database`).

    Function-scoped because an asyncpg connection belongs to the loop that opened it.
    """
    async with session_factory(database_url) as sessions:
        yield sessions


# The row helpers — `insert_user`, `open_request`, `read_request`, `read_runs` — moved to
# `support.action_decisions` at T39, when the expiry sweep's live file needed the same four
# . They still write through the gate's own repository; see their docstrings.


# --------------------------------------------------------------------------------------
# V28 — the row lock, which only two real transactions can demonstrate
# --------------------------------------------------------------------------------------


# `ObservedDecisionRepository` and `HANDOVER_GRACE_SECONDS` moved to
# `support.action_decisions` at T39: the expiry sweep races the same window and must hold it
# open the same way.


async def test_concurrent_approves_produce_one_decision_and_one_run(factory) -> None:
    """V28: a second decision cannot pass a row another transaction is deciding.

    **The race is arranged, and the arrangement is the point.** An `asyncio.gather` of two
    approvals looks like a race and is not one — the first completes before the second
    reaches its read, so that version passes with `FOR UPDATE` *deleted*. A test that cannot
    fail is a tautology in the passing direction, which is V69's shape: a control asserted by
    prose and held by nothing. Here the first transaction is held open between its locked
    read and its commit, and the second is started inside that window.

    **The ordering assertion is what carries the invariant**, not the win count. `second:read`
    must land *after* `first:committed`: that is what "the second read waited for the lock"
    means, and it is false the instant `FOR UPDATE` is removed. The outcomes are asserted too,
    but a win count alone can come out right for the wrong reason.

    **Which transaction reads first is arranged, not raced.** `second` waits for `first`'s lock
    before it reads, and `first` holds until `second` has queued — see `hold_the_lock`. Gating
    only the second half, as this test did until §T78's review, left `asyncio.gather` to decide
    who locked first, and the losing order made the assertion raise instead of fail.

    The `tool_runs` count is the harm this prevents: two rows would mean the same production
    change was authorised, and handed to the executor, twice.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    journal: list[str] = []
    first_locked = asyncio.Event()
    second_reading = asyncio.Event()

    async def hold_the_lock() -> None:
        """Announce the lock, then stay inside the first transaction until the second queues.

        Two handshakes, and the first one is what B5 was missing. `asyncio.gather` starts both
        coroutines and orders nothing: each one's first await is a pool checkout, so if `first`
        needed a new connection while `second`'s was already pooled, `second` took the row lock
        first, committed unopposed, and `first` raised `AlreadyDecided` and never journalled
        `first:committed` — at which point the ordering assertion below raised `ValueError` from
        `list.index` rather than failing. A test whose arrangement is a race cannot assert an
        order (V89, and the reason a §B row exists for it).
        """
        first_locked.set()
        await second_reading.wait()
        await asyncio.sleep(HANDOVER_GRACE_SECONDS)

    async def announce_read() -> None:
        """Wait until the first transaction holds the row, and only then queue behind it."""
        await first_locked.wait()
        second_reading.set()

    async def first() -> Exception | None:
        async with factory() as session:
            service = build_decision_service(
                ObservedDecisionRepository(
                    session, label="first", journal=journal, after_read=hold_the_lock
                )
            )
            try:
                await service.approve(
                    action_request_id=action_request_id,
                    caller_user_id=user_id,
                    reason=f"{REASON} (first)",
                )
            except Exception as exc:
                return exc
            return None

    async def second() -> Exception | None:
        async with factory() as session:
            service = build_decision_service(
                ObservedDecisionRepository(
                    session, label="second", journal=journal, before_read=announce_read
                )
            )
            try:
                await service.approve(
                    action_request_id=action_request_id,
                    caller_user_id=user_id,
                    reason=f"{REASON} (second)",
                )
            except Exception as exc:
                return exc
            return None

    outcomes = await asyncio.gather(first(), second())

    assert journal.index("second:read") > journal.index("first:committed"), (
        f"the second read did not wait for the first transaction: {journal}"
    )

    winners = [outcome for outcome in outcomes if outcome is None]
    losers = [outcome for outcome in outcomes if outcome is not None]

    assert len(winners) == 1, f"expected exactly one approval to win, got {outcomes}"
    assert isinstance(losers[0], ActionRequestAlreadyDecidedError)

    assert (await read_request(factory, action_request_id)).status is ActionRequestStatus.APPROVED
    assert len(await read_runs(factory)) == 1


async def test_an_unlocked_read_of_the_same_row_does_not_wait(factory) -> None:
    """The negative control for the ordering assertion above.

    A "second read landed after the first commit" assertion is worthless if *every* read
    would land there — if, say, the harness simply never overlapped the two transactions.
    This runs the identical handshake with a plain `SELECT` in place of the locked one and
    shows it returns *while* the first transaction is still holding the row: the ordering the
    test above forbids is reachable, so forbidding it says something. Identical includes the
    arrangement — the reader waits for the holder's lock before it reads, or "it did not wait"
    would be a claim about a read that never overlapped anything.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    journal: list[str] = []
    first_locked = asyncio.Event()
    second_reading = asyncio.Event()

    async def hold_the_lock() -> None:
        first_locked.set()
        await second_reading.wait()
        await asyncio.sleep(HANDOVER_GRACE_SECONDS)

    async def holder() -> None:
        async with factory() as session:
            repository = ObservedDecisionRepository(
                session, label="first", journal=journal, after_read=hold_the_lock
            )
            await repository.lock_for_decision(action_request_id=action_request_id)
            await repository.write_decision(
                action_request_id=action_request_id,
                status=ActionRequestStatus.DENIED,
                reason="Holding the row.",
                decided_at=datetime.now(UTC),
                tool_run_id=None,
            )
            await repository.commit()

    async def unlocked_reader() -> None:
        # Same two handshakes as the test above, for the same reason: a reader that ran before
        # the holder ever locked would satisfy the assertion below without demonstrating
        # anything, which is the control failing open.
        await first_locked.wait()
        second_reading.set()
        journal.append("second:reading")
        async with factory() as session:
            await session.execute(
                sa.select(ActionRequest).where(ActionRequest.id == action_request_id)
            )
        journal.append("second:read")

    await asyncio.gather(holder(), unlocked_reader())

    assert journal.index("second:read") < journal.index("first:committed"), (
        f"even an unlocked read waited — the handshake never overlapped: {journal}"
    )


async def test_concurrent_approve_and_deny_leave_one_answer(factory) -> None:
    """The same race across the two endpoints, where the stakes are opposite.

    Whichever wins, the row holds exactly one answer and the other caller is told so. A
    denial that quietly overwrote an approval would leave a change already handed to the
    executor while the row says it was refused.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async def approve() -> Exception | None:
        async with factory() as session:
            service, _ = build_live_decision_service(session)
            try:
                await service.approve(
                    action_request_id=action_request_id,
                    caller_user_id=user_id,
                    reason=REASON,
                )
            except Exception as exc:
                return exc
            return None

    async def deny() -> Exception | None:
        async with factory() as session:
            service, _ = build_live_decision_service(session)
            try:
                await service.deny(
                    action_request_id=action_request_id,
                    caller_user_id=user_id,
                    reason="Not authorised by the ticket.",
                )
            except Exception as exc:
                return exc
            return None

    outcomes = await asyncio.gather(approve(), deny())

    assert sum(outcome is None for outcome in outcomes) == 1
    losers = [outcome for outcome in outcomes if outcome is not None]
    assert isinstance(losers[0], ActionRequestAlreadyDecidedError)

    stored = await read_request(factory, action_request_id)
    assert stored.status in {ActionRequestStatus.APPROVED, ActionRequestStatus.DENIED}
    # A run exists if and only if the approval won.
    expected_runs = 1 if stored.status is ActionRequestStatus.APPROVED else 0
    assert len(await read_runs(factory)) == expected_runs


async def test_a_second_decision_after_the_first_committed_is_refused(factory) -> None:
    """The sequential half of V28. Not redundant with the race: this is the common case."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        await service.approve(
            action_request_id=action_request_id,
            caller_user_id=user_id,
            reason=REASON,
        )

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestAlreadyDecidedError):
            await service.deny(
                action_request_id=action_request_id,
                caller_user_id=user_id,
                reason="Changed my mind.",
            )

    assert len(await read_runs(factory)) == 1


# --------------------------------------------------------------------------------------
# V27, V32, V15 against real rows
# --------------------------------------------------------------------------------------


async def test_another_operators_request_is_not_found(factory) -> None:
    """V27, against a row that genuinely exists — the case a double cannot make convincing."""
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    intruder_id = await insert_user(factory, OTHER_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=owner_id)

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestNotFoundError):
            await service.approve(
                action_request_id=action_request_id,
                caller_user_id=intruder_id,
                reason=REASON,
            )

    assert (await read_request(factory, action_request_id)).status is ActionRequestStatus.PENDING
    assert await read_runs(factory) == []


async def test_an_unknown_id_is_not_found(factory) -> None:
    user_id = await insert_user(factory, OPERATOR_EMAIL)

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestNotFoundError):
            await service.approve(
                action_request_id=uuid4(),
                caller_user_id=user_id,
                reason=REASON,
            )


async def test_a_request_whose_requester_was_deleted_is_not_found(factory) -> None:
    """The FK is `SET NULL`, so a deleted operator's request matches nobody.

    Asserted against a real `DELETE`, because `ondelete` is a string in metadata until
    something deletes — and because the fail-closed direction is the whole point: NULL must
    mean "no one may decide this", never "anyone may".
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    survivor_id = await insert_user(factory, OTHER_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        await session.execute(sa.delete(User).where(User.id == user_id))
        await session.commit()

    stored = await read_request(factory, action_request_id)
    assert stored.requested_by_user_id is None

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestNotFoundError):
            await service.approve(
                action_request_id=action_request_id,
                caller_user_id=survivor_id,
                reason=REASON,
            )


async def test_an_expired_request_becomes_terminal_on_read(factory) -> None:
    """V32's check-on-read, committed: the row is EXPIRED afterwards, not still PENDING.

    Refusing without the write would leave the next reader to make the same discovery again,
    and would leave a request nobody answered still looking answerable.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )

    async with factory() as session:
        service, executor = build_live_decision_service(session)
        with pytest.raises(ActionRequestExpiredError):
            await service.approve(
                action_request_id=action_request_id,
                caller_user_id=user_id,
                reason=REASON,
            )

    stored = await read_request(factory, action_request_id)

    assert stored.status is ActionRequestStatus.EXPIRED
    assert stored.decided_at is not None
    # The row the CHECK deliberately permits: terminal, and with no reason, because nobody
    # gave one. A constraint covering all four statuses would make this write impossible.
    assert stored.reason is None
    assert stored.tool_run_id is None
    assert await read_runs(factory) == []
    assert executor.started == []


async def test_deciding_an_expired_request_twice_reports_it_as_decided(factory) -> None:
    """The second attempt hits V28's guard, not V32's — the row is already terminal.

    Worth pinning because the two refusals carry different remedies: "ask for the change
    again" versus "reload and read the outcome".
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestExpiredError):
            await service.approve(
                action_request_id=action_request_id, caller_user_id=user_id, reason=REASON
            )

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestAlreadyDecidedError):
            await service.approve(
                action_request_id=action_request_id, caller_user_id=user_id, reason=REASON
            )


async def test_a_blank_reason_touches_no_row(factory) -> None:
    """V15, and the ordering that goes with it: refused before the lock is taken."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ChangeReasonRequiredError):
            await service.approve(
                action_request_id=action_request_id,
                caller_user_id=user_id,
                reason="\n\t ",
            )

    assert (await read_request(factory, action_request_id)).status is ActionRequestStatus.PENDING


async def test_the_gates_repository_still_cannot_decide(factory) -> None:
    """V22, asserted against the class the MCP path actually holds.

    `SQLActionRequestRepository` is what `McpToolContext` carries. If it ever grew a way to
    write a terminal status, the bearer-token path would hold the key to the authorization —
    and every other test in this file would still pass.
    """
    writable = {name for name in dir(SQLActionRequestRepository) if not name.startswith("_")}

    assert writable == {"create_pending", "commit"}
