"""V31's per-user cap on in-flight changes, against a real Postgres.

The cap counts `tool_runs` rows that are `CHANGE` and `STARTED` for one requester, and an
advisory lock taken inside the counting transaction is what makes it hold. Neither half is
provable over an in-memory repository: the count is a statement issued against real rows, and
a lock is only what two real transactions do to each other.

Split from `test_action_request_decisions_live.py` when that file passed the 900-line limit,
and a clean seam because the two locks guard different things. Two decisions on **one
request** contend on the row, which is V28's `SELECT ... FOR UPDATE`; two approvals of
**different requests by one operator** never touch each other's rows, so that lock serializes
nothing between them and V31 needs an overlap test of its own rather than inheriting V28's.

Both races here are arranged rather than hoped for, and each ships the negative control V89
requires: the window is held open on purpose, the assertion is on *order* rather than on a win
count, and the control shows the forbidden order is reachable once the lock is gone.

Row helpers come from `support.action_decisions` and the scratch-database fixtures from
`support.database`.

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.approvals.errors import ChangeExecutionLimitReachedError
from core.audit.tool_runs import SQLToolRunRepository
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from support.action_decisions import (
    HANDOVER_GRACE_SECONDS,
    REASON,
    ObservedDecisionRepository,
    UnlockedCountDecisionRepository,
    build_decision_service,
    build_live_decision_service,
    insert_user,
    open_request,
    read_request,
    read_runs,
)
from support.database import migrated_database, session_factory

SCRATCH_DB = "noa_action_change_cap_test"

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


# --------------------------------------------------------------------------------------
# V31 — the per-user cap, and the lock that makes it hold
# --------------------------------------------------------------------------------------


async def test_the_cap_counts_a_started_change_run(factory) -> None:
    """V31, sequentially: one change in flight, a limit of one, and the second is refused.

    The count is `tool_runs` rows that are `CHANGE` and `STARTED` — which is what "in flight"
    means when the executor has not written a terminal status yet. Two requests, because a
    second approval of the *same* request is V28's 409 and would pass this test for the wrong
    reason.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    first_request = await open_request(factory, requested_by_user_id=user_id)
    second_request = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        service, _ = build_live_decision_service(session, max_inflight_per_user=1)
        await service.approve(
            action_request_id=first_request,
            caller_user_id=user_id,
            reason=REASON,
        )

    async with factory() as session:
        service, _ = build_live_decision_service(session, max_inflight_per_user=1)
        with pytest.raises(ChangeExecutionLimitReachedError):
            await service.approve(
                action_request_id=second_request,
                caller_user_id=user_id,
                reason=REASON,
            )

    assert len(await read_runs(factory)) == 1
    assert (await read_request(factory, second_request)).status is ActionRequestStatus.PENDING


async def test_a_finished_change_frees_the_operators_slot(factory) -> None:
    """The negative control for the cap: it must let go.

    A cap that counted every change an operator ever made would refuse the second approval
    forever, and this test would be the only thing that noticed.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    first_request = await open_request(factory, requested_by_user_id=user_id)
    second_request = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        service, _ = build_live_decision_service(session, max_inflight_per_user=1)
        outcome = await service.approve(
            action_request_id=first_request,
            caller_user_id=user_id,
            reason=REASON,
        )

    # What the executor does when the change finishes.
    async with factory() as session:
        runs = SQLToolRunRepository(session)
        await runs.finish_run(
            tool_run_id=outcome.tool_run_id,
            status=ToolRunStatus.COMPLETED,
            result_summary='{"ok":true}',
        )
        await runs.commit()

    async with factory() as session:
        service, _ = build_live_decision_service(session, max_inflight_per_user=1)
        await service.approve(
            action_request_id=second_request,
            caller_user_id=user_id,
            reason=REASON,
        )

    assert len(await read_runs(factory)) == 2


async def test_another_operators_change_does_not_spend_this_ones_allowance(factory) -> None:
    """The count is scoped by `requested_by_user_id` in the statement.

    Global instead of per-user, the cap would make one operator's slow change stop the team —
    which is the outage V31's wording ("per-user") exists to avoid.
    """
    first_user = await insert_user(factory, OPERATOR_EMAIL)
    second_user = await insert_user(factory, OTHER_EMAIL)
    first_request = await open_request(factory, requested_by_user_id=first_user)
    second_request = await open_request(factory, requested_by_user_id=second_user)

    for request_id, user_id in ((first_request, first_user), (second_request, second_user)):
        async with factory() as session:
            service, _ = build_live_decision_service(session, max_inflight_per_user=1)
            await service.approve(
                action_request_id=request_id,
                caller_user_id=user_id,
                reason=REASON,
            )

    assert len(await read_runs(factory)) == 2


async def test_two_overlapping_approvals_by_one_operator_start_one_run(factory) -> None:
    """V31 under concurrency, proven overlapped.

    **Why V28's row lock does not cover this.** Two approvals of *different* requests never
    touch each other's rows, so `SELECT … FOR UPDATE` serializes nothing between them. Both read
    a count of zero under READ COMMITTED — neither can see the other's uncommitted `tool_runs`
    insert — and both proceed. What serializes them is V31's per-user advisory lock, taken inside
    the same transaction as the count it protects.

    **The window is held open on purpose.** The first transaction stops between taking the
    advisory lock and committing, and the second is started inside that window.

    **The ordering assertion is what carries the invariant**, not the win count: `second:counted`
    must land *after* `first:committed`, because that is what "the second count waited for the
    lock" means, and it is false the instant the advisory lock is removed. The
    `tool_runs` count is the harm this prevents — two rows would mean two production changes
    running for one operator whose limit is one.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    first_request = await open_request(factory, requested_by_user_id=user_id)
    second_request = await open_request(factory, requested_by_user_id=user_id)

    journal: list[str] = []
    second_counting = asyncio.Event()

    async def hold_the_lock() -> None:
        """Stay inside the first transaction until the second has reached for the key."""
        await second_counting.wait()
        await asyncio.sleep(HANDOVER_GRACE_SECONDS)

    async def announce_count() -> None:
        second_counting.set()

    async def approve(
        action_request_id: UUID,
        *,
        label: str,
        before_count=None,  # type: ignore[no-untyped-def]
        after_count=None,  # type: ignore[no-untyped-def]
    ) -> Exception | None:
        async with factory() as session:
            service = build_decision_service(
                ObservedDecisionRepository(
                    session,
                    label=label,
                    journal=journal,
                    before_count=before_count,
                    after_count=after_count,
                ),
                max_inflight_per_user=1,
            )
            try:
                await service.approve(
                    action_request_id=action_request_id,
                    caller_user_id=user_id,
                    reason=f"{REASON} ({label})",
                )
            except Exception as exc:
                return exc
            return None

    outcomes = await asyncio.gather(
        approve(first_request, label="first", after_count=hold_the_lock),
        approve(second_request, label="second", before_count=announce_count),
    )

    assert journal.index("second:counted") > journal.index("first:committed"), (
        f"the second count did not wait for the first transaction: {journal}"
    )

    winners = [outcome for outcome in outcomes if outcome is None]
    losers = [outcome for outcome in outcomes if outcome is not None]

    assert len(winners) == 1, f"expected exactly one approval to win, got {outcomes}"
    assert isinstance(losers[0], ChangeExecutionLimitReachedError)
    assert len(await read_runs(factory)) == 1


async def test_an_unlocked_count_does_not_wait_and_the_cap_is_breached(factory) -> None:
    """The negative control for the ordering assertion above (V87, V89's obligation (b)).

    A "the second count landed after the first commit" assertion is worthless if *every* count
    would land there — if, say, the harness never actually overlapped the two transactions. This
    runs the identical handshake with the advisory lock removed and nothing else changed, and
    shows two things at once: the count returns *while* the first transaction still holds its
    slot, and both approvals then win. So the ordering the test above forbids is reachable, and
    the harm it forbids is real.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    first_request = await open_request(factory, requested_by_user_id=user_id)
    second_request = await open_request(factory, requested_by_user_id=user_id)

    journal: list[str] = []
    second_counting = asyncio.Event()

    async def hold_open() -> None:
        await second_counting.wait()
        await asyncio.sleep(HANDOVER_GRACE_SECONDS)

    async def announce_count() -> None:
        second_counting.set()

    async def approve(
        action_request_id: UUID,
        *,
        label: str,
        before_count=None,  # type: ignore[no-untyped-def]
        after_count=None,  # type: ignore[no-untyped-def]
    ) -> Exception | None:
        async with factory() as session:
            service = build_decision_service(
                UnlockedCountDecisionRepository(
                    session,
                    label=label,
                    journal=journal,
                    before_count=before_count,
                    after_count=after_count,
                ),
                max_inflight_per_user=1,
            )
            try:
                await service.approve(
                    action_request_id=action_request_id,
                    caller_user_id=user_id,
                    reason=f"{REASON} ({label})",
                )
            except Exception as exc:
                return exc
            return None

    outcomes = await asyncio.gather(
        approve(first_request, label="first", after_count=hold_open),
        approve(second_request, label="second", before_count=announce_count),
    )

    assert journal.index("second:counted") < journal.index("first:committed"), (
        f"the unlocked count still waited, so the handshake never overlapped: {journal}"
    )
    assert outcomes == [None, None], f"expected both approvals to win without the lock: {outcomes}"
    assert len(await read_runs(factory)) == 2
