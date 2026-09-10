"""The expiry sweep against a real Postgres.

`test_action_request_expiry.py` drives the same service over an in-memory repository, which
proves the loop and the reporting but cannot prove the three claims that are *about the
database*:

- **The predicate.** "PENDING and past its deadline, and nothing else" is a statement about
  SQL, and a double that re-implements it agrees with the test rather than with the code.
- **The deadline boundary.** A row exactly on `expires_at` has to be judged the same way by
  the sweep and by the decision door. Two comparisons in two statements can only be shown to
  agree by running both.
- **The lock behaviour V28 leans on.** The sweep takes no `SELECT … FOR UPDATE`; it relies on
  a bare `UPDATE` re-evaluating its `WHERE` against the row version left by the transaction
  it waited for. That is a Postgres property, so only Postgres can demonstrate it — and if it
  did not hold, a sweep could expire a change that had already been authorised and handed to
  an executor.

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.approvals.errors import ActionRequestAlreadyDecidedError, ActionRequestExpiredError
from core.approvals.expiry import (
    ActionRequestExpiryService,
    PendingExpirySweeper,
    SQLActionRequestExpiryRepository,
)
from core.db.lifecycle import ActionRequestStatus
from core.db.models import ActionRequest
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
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_action_expiry_test"

OPERATOR_EMAIL = "operator@example.com"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory over a freshly emptied database.

    A *factory*, not a session: the race needs two independent connections, and a single
    shared session would serialise them in Python before Postgres ever saw a lock.
    """
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def sweep(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> tuple[UUID, ...]:
    """One pass of the production service over the production repository."""
    async with factory() as session:
        service = ActionRequestExpiryService(SQLActionRequestExpiryRepository(session))
        return await service.sweep(now=now)


async def expire_if_due(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """The render path's check-on-read, over real SQL."""
    async with factory() as session:
        service = ActionRequestExpiryService(SQLActionRequestExpiryRepository(session))
        return await service.expire_if_due(action_request_id=action_request_id, now=now)


# --------------------------------------------------------------------------------------
# The predicate
# --------------------------------------------------------------------------------------


async def test_the_sweep_expires_a_pending_request_past_its_deadline(factory) -> None:
    """V32's whole point: terminality with nobody having opened the card.

    The row shape matters as much as the status. `reason` stays NULL because an expiry is the
    absence of an answer, not one — T34's CHECK exempts EXPIRED for exactly this write — and
    `tool_run_id` stays NULL because nothing ran.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )
    before = datetime.now(UTC)

    expired = await sweep(factory)

    assert expired == (action_request_id,)

    stored = await read_request(factory, action_request_id)
    assert stored.status is ActionRequestStatus.EXPIRED
    assert stored.reason is None
    assert stored.tool_run_id is None
    # A bound, not an equality: `decided_at` is clock-stamped.
    assert stored.decided_at is not None
    assert before <= stored.decided_at <= datetime.now(UTC)
    assert await read_runs(factory) == []


async def test_the_sweep_leaves_a_live_pending_request_alone(factory) -> None:
    """A request still inside its TTL is still answerable."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=3600
    )

    assert await sweep(factory) == ()

    stored = await read_request(factory, action_request_id)
    assert stored.status is ActionRequestStatus.PENDING
    assert stored.decided_at is None


async def test_one_pass_expires_every_due_request(factory) -> None:
    """The sweep is set-based: a backlog does not need a pass each."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    due = {
        await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-offset)
        for offset in (1, 60, 86400)
    }
    live = {
        await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=offset)
        for offset in (30, 3600)
    }

    assert set(await sweep(factory)) == due

    for action_request_id in live:
        assert (await read_request(factory, action_request_id)).status is (
            ActionRequestStatus.PENDING
        )


async def test_a_request_exactly_on_its_deadline_expires_at_both_doors(factory) -> None:
    """`<=`, in both comparisons, asserted together.

    `§T.39`'s line reads `expires_at < now()` and the decision door compares
    `expires_at <= decided_at`. Taken literally, a request whose deadline is *this instant*
    would be refused at the door and left PENDING by the sweep forever — a row that can never
    be answered and never becomes terminal. The two are pinned in one test on purpose: a
    future edit to either comparison has to break this to land.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    moment = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)

    swept = await open_request(factory, requested_by_user_id=user_id, expires_at=moment)
    decided = await open_request(factory, requested_by_user_id=user_id, expires_at=moment)

    # The door first: the sweep is set-based, so running it first would expire both rows and
    # leave nothing for the other half of the comparison to be made against.
    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestExpiredError):
            await service.approve(
                action_request_id=decided,
                caller_user_id=user_id,
                reason=REASON,
                now=moment,
            )

    assert (await read_request(factory, decided)).status is ActionRequestStatus.EXPIRED
    assert await sweep(factory, now=moment) == (swept,)


async def test_the_sweep_writes_away_a_reason_a_pending_row_should_not_have(factory) -> None:
    """The `reason = NULL` in the sweep's `SET`, proven rather than described.

    No production writer can put a reason on a `PENDING` row today — only a decision writes
    one, and it writes a terminal status in the same statement. But T34's CHECK exempts
    `EXPIRED` precisely so an expiry *can* carry none, which means nothing at the database
    level would catch an `EXPIRED` row that carried one. So the guard is in the sweep's
    statement, and this is the case that makes it a control rather than a comment (V69's
    shape: a property asserted by prose and held by nothing).
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )

    async with factory() as session:
        await session.execute(
            ActionRequest.__table__.update()
            .where(ActionRequest.id == action_request_id)
            .values(reason="written by something that had no business writing it")
        )
        await session.commit()

    assert await sweep(factory) == (action_request_id,)
    assert (await read_request(factory, action_request_id)).reason is None


async def test_the_sweep_does_not_restamp_an_already_expired_row(factory) -> None:
    """The predicate is `status = PENDING`, so a second pass is a no-op on the first's work.

    Re-stamping would move `decided_at` on every pass and turn the moment a request stopped
    being answerable into "the last time a sweeper ran".
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )

    await sweep(factory)
    first = (await read_request(factory, action_request_id)).decided_at

    assert await sweep(factory) == ()
    assert (await read_request(factory, action_request_id)).decided_at == first


async def test_the_sweep_cannot_touch_a_decided_request(factory) -> None:
    """An approval is an authorization. A loop with no operator behind it does not edit one.

    `reason` is asserted too, not only `status`: a statement that lost its `WHERE` would
    overwrite the operator's own words with the NULL an expiry writes.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=3600
    )

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        outcome = await service.approve(
            action_request_id=action_request_id,
            caller_user_id=user_id,
            reason=REASON,
        )

    approved = await read_request(factory, action_request_id)

    # A `now` far past the deadline: the row is only protected by its status.
    assert await sweep(factory, now=datetime.now(UTC) + timedelta(days=365)) == ()

    stored = await read_request(factory, action_request_id)
    assert stored.status is ActionRequestStatus.APPROVED
    assert stored.reason == REASON
    assert stored.tool_run_id == outcome.tool_run_id
    assert stored.decided_at == approved.decided_at


# --------------------------------------------------------------------------------------
# Check-on-read, the half T41's card depends on
# --------------------------------------------------------------------------------------


async def test_check_on_read_expires_a_due_request_and_reports_it(factory) -> None:
    """So a GET renders the row's real state, never a PENDING nobody may act on."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )

    assert await expire_if_due(factory, action_request_id) is True

    stored = await read_request(factory, action_request_id)
    assert stored.status is ActionRequestStatus.EXPIRED
    assert stored.reason is None


async def test_check_on_read_leaves_a_live_request_pending(factory) -> None:
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=3600
    )

    assert await expire_if_due(factory, action_request_id) is False
    assert (await read_request(factory, action_request_id)).status is ActionRequestStatus.PENDING


async def test_check_on_read_touches_only_the_request_it_was_given(factory) -> None:
    """The id narrows the sweep's statement. A render of one card is not a sweep of all."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    rendered = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-5)
    untouched = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-5)

    assert await expire_if_due(factory, rendered) is True

    assert (await read_request(factory, untouched)).status is ActionRequestStatus.PENDING


async def test_check_on_read_cannot_expire_a_decided_request(factory) -> None:
    """A GET cannot rewrite a decision, whatever the clock says."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=3600
    )

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        await service.deny(
            action_request_id=action_request_id,
            caller_user_id=user_id,
            reason="Not authorised by the ticket.",
        )

    denied = await read_request(factory, action_request_id)
    future = datetime.now(UTC) + timedelta(days=365)

    assert await expire_if_due(factory, action_request_id, now=future) is False

    stored = await read_request(factory, action_request_id)
    assert stored.status is ActionRequestStatus.DENIED
    assert stored.reason == denied.reason
    assert stored.decided_at == denied.decided_at


async def test_check_on_read_of_an_unknown_id_reports_nothing_and_raises_nothing(
    factory,
) -> None:
    """A render path calls this before it knows whether the row exists (V27 answers that)."""
    assert await expire_if_due(factory, UUID(int=0)) is False


# --------------------------------------------------------------------------------------
# V89 — the sweep against an approval that is already in flight
# --------------------------------------------------------------------------------------


async def test_a_sweep_cannot_expire_a_request_being_approved(factory) -> None:
    """V28, V32: the request expired *while* an operator's approval was in flight.

    **The race is arranged, and the arrangement is the point**. Running the two in an
    `asyncio.gather` is not a race — the first commonly finishes before the second's statement
    reaches the server, so that version passes even if the `UPDATE` could clobber the
    decision. Here the approval is held open between its locked read and its commit, and the
    sweep is issued inside that window.

    **The ordering assertion carries the invariant**, not the outcome count: `sweep:returned`
    must land *after* `approve:committed`, which is what "the `UPDATE` waited for the row"
    means. The negative control below shows that ordering is not automatic.

    The two moments differ on purpose. The approval judges the row against a `now` before its
    deadline (so it passes V32's check-on-read at the door) and the sweep judges it against
    one after — which is exactly the situation the invariant is about: the TTL passed while a
    human was deciding. If the `UPDATE` did not re-check its `WHERE` after waiting, this row
    would end up EXPIRED with a `tool_runs` row already handed to an executor.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_at=deadline
    )

    journal: list[str] = []
    sweep_issued = asyncio.Event()

    async def hold_the_lock() -> None:
        """Stay inside the approval's transaction until the sweep has reached for the row."""
        await sweep_issued.wait()
        await asyncio.sleep(HANDOVER_GRACE_SECONDS)

    async def approve() -> None:
        async with factory() as session:
            service = build_decision_service(
                ObservedDecisionRepository(
                    session, label="approve", journal=journal, after_read=hold_the_lock
                )
            )
            await service.approve(
                action_request_id=action_request_id,
                caller_user_id=user_id,
                reason=REASON,
                now=deadline - timedelta(seconds=1),
            )

    async def sweeper() -> tuple[UUID, ...]:
        sweep_issued.set()
        journal.append("sweep:issued")
        expired = await sweep(factory, now=deadline + timedelta(seconds=1))
        journal.append("sweep:returned")
        return expired

    _, expired = await asyncio.gather(approve(), sweeper())

    assert journal.index("sweep:returned") > journal.index("approve:committed"), (
        f"the sweep did not wait for the approval's transaction: {journal}"
    )

    assert expired == ()
    stored = await read_request(factory, action_request_id)
    assert stored.status is ActionRequestStatus.APPROVED
    assert stored.reason == REASON
    assert len(await read_runs(factory)) == 1


async def test_an_unlocked_read_of_the_same_row_does_not_wait(factory) -> None:
    """The negative control for the ordering assertion above (V89c).

    "The sweep returned after the commit" is worth nothing if *every* statement would land
    there — if, say, the handshake never overlapped the two transactions at all. This runs the
    identical arrangement with a plain `SELECT` in place of the sweep's `UPDATE` and shows it
    returns *while* the approval still holds the row: the ordering the test above forbids is
    reachable, so forbidding it says something.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_at=deadline
    )

    journal: list[str] = []
    reader_issued = asyncio.Event()

    async def hold_the_lock() -> None:
        await reader_issued.wait()
        await asyncio.sleep(HANDOVER_GRACE_SECONDS)

    async def approve() -> None:
        async with factory() as session:
            service = build_decision_service(
                ObservedDecisionRepository(
                    session, label="approve", journal=journal, after_read=hold_the_lock
                )
            )
            await service.approve(
                action_request_id=action_request_id,
                caller_user_id=user_id,
                reason=REASON,
                now=deadline - timedelta(seconds=1),
            )

    async def unlocked_reader() -> None:
        reader_issued.set()
        journal.append("read:issued")
        async with factory() as session:
            await session.execute(
                ActionRequest.__table__.select().where(ActionRequest.id == action_request_id)
            )
        journal.append("read:returned")

    await asyncio.gather(approve(), unlocked_reader())

    assert journal.index("read:returned") < journal.index("approve:committed"), (
        f"even an unlocked read waited — the handshake never overlapped: {journal}"
    )


async def test_a_decision_after_the_sweep_is_refused_as_already_decided(factory) -> None:
    """The other order: the sweep won, so the operator's click is a 409, not an approval.

    `already_decided` rather than `expired`, because V28's status guard runs before V32's
    deadline guard and the row is terminal by the time the door sees it. The remedy the two
    messages give differs, and this is the one an operator gets after a sweep.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )

    assert await sweep(factory) == (action_request_id,)

    async with factory() as session:
        service, executor = build_live_decision_service(session)
        with pytest.raises(ActionRequestAlreadyDecidedError):
            await service.approve(
                action_request_id=action_request_id,
                caller_user_id=user_id,
                reason=REASON,
            )

    assert await read_runs(factory) == []
    assert executor.started == []


# --------------------------------------------------------------------------------------
# V87 — two writers, one kind of EXPIRED row
# --------------------------------------------------------------------------------------

# Everything the two expiry writers are supposed to agree on. `id`, `expires_at`, `created_at`
# and `decided_at` are dropped: the first three differ per row by construction and the fourth
# is clock-stamped, so keeping it would make this compare a coin flip on which second the two
# writes landed in. `decided_at` is asserted as a property instead, below.
COMPARED_COLUMNS = (
    "tool_name",
    "requested_by_user_id",
    "status",
    "conversation_ref",
    "approval_context",
    "reason",
    "tool_run_id",
)


def shape(row: ActionRequest) -> dict[str, object]:
    """The row minus what the clock and the identity column decide."""
    return {name: getattr(row, name) for name in COMPARED_COLUMNS}


async def test_a_swept_row_matches_one_expired_at_the_decision_door(factory) -> None:
    """Two writers, one event, one kind of row.

    The decision door and the sweep both write EXPIRED, from different sides and for different
    reasons — an operator arrived too late, or nobody arrived at all. If they disagreed on the
    row they leave, "expired" would mean two things in one column, and T41's card and the
    admin audit would each be right about a different one.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    at_the_door = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-5)
    by_the_sweep = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-5)

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        with pytest.raises(ActionRequestExpiredError):
            await service.approve(
                action_request_id=at_the_door,
                caller_user_id=user_id,
                reason=REASON,
            )

    assert await sweep(factory) == (by_the_sweep,)

    door_row = await read_request(factory, at_the_door)
    swept_row = await read_request(factory, by_the_sweep)

    assert shape(swept_row) == shape(door_row)
    # The dropped clock column, asserted by property rather than by equality.
    assert door_row.decided_at is not None
    assert swept_row.decided_at is not None


async def test_the_shape_compare_still_separates_two_different_outcomes(factory) -> None:
    """The case V87 requires beside every dropped field: proof the comparator still bites.

    A field-dropping comparator degrades into a tautology silently, and a tautology passes
    every run — including the ones it was written to catch. So: a denial and an expiry differ
    under exactly the comparison the test above uses.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    denied = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=3600)
    expired = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-5)

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        await service.deny(
            action_request_id=denied,
            caller_user_id=user_id,
            reason="Not authorised by the ticket.",
        )

    await sweep(factory)

    assert shape(await read_request(factory, denied)) != shape(await read_request(factory, expired))


# --------------------------------------------------------------------------------------
# The sweeper's own wiring, over the real repository
# --------------------------------------------------------------------------------------


async def test_the_sweepers_pass_expires_against_a_real_database(factory) -> None:
    """`PendingExpirySweeper` with its production default repository (T39's wiring).

    `test_action_request_expiry.py` swaps that default out to drive the loop without Postgres,
    which means the default itself is only exercised here — the seam a test replaces is
    exactly the seam nothing else proves.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory, requested_by_user_id=user_id, expires_in_seconds=-5
    )

    sweeper = PendingExpirySweeper(session_factory=factory, interval_seconds=3600)

    assert await sweeper.run_once() == (action_request_id,)
    assert (await read_request(factory, action_request_id)).status is ActionRequestStatus.EXPIRED
