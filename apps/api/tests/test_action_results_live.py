"""Reading an approval request back, against a real Postgres.

`test_noa_tools_action_result.py` drives the same read path over doubles, which proves the
tool's shape, its one refusal and the order it does things in. Three claims here are claims
*about the database*, and a double answering them would be agreeing with the test rather than
with the code:

- **A deleted requester's row is refused.** The FK is `SET NULL`, so deleting an
  operator leaves their requests behind with a NULL column — a state only a real `DELETE`
  produces, and one every requester-match has to fail closed on.
- **The reader and the three writers describe one row.** The reason a decision persists, the
  run an approval starts, the `EXPIRED` a stale request becomes: each is written by a
  different class in `core.approvals`, and this is the only place the reader is put against
  all three rather than against a fixture that agrees with it.
- **A read never writes to a row that is not the caller's.** The check-on-read takes an id
  and no requester, so the ordering is only visible in what the *table* says afterwards.

What this file does **not** prove, recorded so the file is not read as proving it: that the
requester-match lives in the `WHERE` rather than in a Python comparison after the read. Both
spellings were run; both are green here (see `core.approvals.results`' module docstring). The
statement is preferred for defence in depth, not because a test separates them.

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.approvals.decisions import SQLActionDecisionRepository
from core.approvals.execution import build_receipt
from core.approvals.expiry import ActionRequestExpiryService, SQLActionRequestExpiryRepository
from core.approvals.results import (
    ActionResultService,
    ActionResultView,
    SQLActionResultRepository,
)
from core.audit.receipts import SQLActionReceiptRepository
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from core.db.models import User
from support.action_decisions import (
    APPROVAL_CONTEXT,
    CHANGE_TOOL,
    REASON,
    build_decision_service,
    insert_user,
    open_request,
    read_request,
)
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_action_results_test"

OPERATOR_EMAIL = "operator@example.com"
OTHER_EMAIL = "someone-else@example.com"

# Values that appear in exactly one branch of `approval_context`, so "the evidence did not
# travel" is a compare that can separate. The shipped fixture (`support.action_decisions`)
# deliberately shares `acmeco` between the arguments and the evidence, which makes it the
# wrong instrument for this one claim.
EVIDENCE_SENTINEL = "before-state-only-the-approval-card-may-see"
REQUESTER_SENTINEL = "librechat-account-only-the-approval-card-may-see"

EVIDENCE_HALF: dict[str, object] = {"before_state": EVIDENCE_SENTINEL}

SEPARABLE_CONTEXT: dict[str, object] = {
    "arguments": {"server_ref": "alpha", "account": "acmeco"},
    "requester": {"email": OPERATOR_EMAIL, "librechat_user_id": REQUESTER_SENTINEL},
    "evidence": EVIDENCE_HALF,
}

# The receipt's after-state. Its own sentinel, because "the receipt did not travel" has to
# separate from "the evidence did not travel" — the two halves fail for different reasons if the
# model path ever grows the join (V17 for one, V76 for the pair).
RECEIPT_SENTINEL = "after-state-only-the-approval-card-may-see"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory over a freshly emptied database."""
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def read_result(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    *,
    requester_user_id: UUID,
    now: datetime | None = None,
) -> ActionResultView | None:
    """The production read path — the reader and the expiry, over one session."""
    async with factory() as session:
        service = ActionResultService(
            repository=SQLActionResultRepository(session),
            expiry=ActionRequestExpiryService(SQLActionRequestExpiryRepository(session)),
        )
        return await service.result_for(
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
            now=now,
        )


async def approve(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    *,
    caller_user_id: UUID,
) -> UUID:
    """A real approval through the real decision service; returns the run it started."""
    async with factory() as session:
        service = build_decision_service(SQLActionDecisionRepository(session))
        outcome = await service.approve(
            action_request_id=action_request_id,
            caller_user_id=caller_user_id,
            reason=REASON,
        )
        return outcome.tool_run_id


async def write_receipt(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    *,
    tool_run_id: UUID,
) -> None:
    """A receipt through the production writer, in the production shape."""
    async with factory() as session:
        await SQLActionReceiptRepository(session).create_if_missing(
            action_request_id=action_request_id,
            tool_run_id=tool_run_id,
            receipt_data=build_receipt(
                evidence=EVIDENCE_HALF,
                payload={"ok": True, "result": RECEIPT_SENTINEL},
            ),
        )
        await session.commit()


async def delete_user(factory: async_sessionmaker[AsyncSession], user_id: UUID) -> None:
    """Delete the operator, leaving their requests behind with a NULL requester."""
    async with factory() as session:
        await session.execute(sa.delete(User).where(User.id == user_id))
        await session.commit()


# --------------------------------------------------------------------------------------
# The happy path: the row the gate wrote, read back
# --------------------------------------------------------------------------------------


async def test_a_request_reads_back_with_its_arguments_and_its_deadline(factory) -> None:  # type: ignore[no-untyped-def]
    """The reader answers about the row T33's gate wrote, not about a copy of it."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    assert view.action_request_id == request_id
    assert view.tool_name == CHANGE_TOOL
    assert view.status is ActionRequestStatus.PENDING
    assert view.arguments == APPROVAL_CONTEXT["arguments"]
    assert view.decided_at is None
    assert view.run is None

    stored = await read_request(factory, request_id)
    assert view.expires_at == stored.expires_at.replace(tzinfo=view.expires_at.tzinfo)


# --------------------------------------------------------------------------------------
# V27 / V76: the requester-match is the statement's
# --------------------------------------------------------------------------------------


async def test_another_operators_request_is_not_readable(factory) -> None:  # type: ignore[no-untyped-def]
    """V27/V76: the row is not fetched, so there is nothing for a later branch to drop.

    The intruder here is a real user with a real id — the refusal is the requester-match and
    not a missing row.
    """
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    intruder_id = await insert_user(factory, OTHER_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=owner_id)

    assert await read_result(factory, request_id, requester_user_id=intruder_id) is None
    assert await read_result(factory, request_id, requester_user_id=owner_id) is not None


async def test_an_unknown_id_reads_as_nothing(factory) -> None:  # type: ignore[no-untyped-def]
    """The other half of the pair the tool answers identically."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)

    assert await read_result(factory, uuid4(), requester_user_id=user_id) is None


async def test_a_request_whose_requester_was_deleted_is_not_readable(factory) -> None:  # type: ignore[no-untyped-def]
    """V27 fails closed on the row only a real `DELETE` can produce.

    `requested_by_user_id` is `SET NULL`, so deleting the operator leaves the request
    behind with a NULL requester: `NULL = :caller` is NULL, never true, so it matches nobody —
    including the operator who is now gone. Live because the state is the FK's, not a value a
    double would think to hand back.
    """
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    other_id = await insert_user(factory, OTHER_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=owner_id)

    await delete_user(factory, owner_id)

    stored = await read_request(factory, request_id)
    assert stored.requested_by_user_id is None
    assert await read_result(factory, request_id, requester_user_id=owner_id) is None
    assert await read_result(factory, request_id, requester_user_id=other_id) is None


# --------------------------------------------------------------------------------------
# C8 / V15 / V43 / V17: what the model is never told
# --------------------------------------------------------------------------------------


async def test_a_decided_requests_reason_never_reaches_the_result(factory) -> None:  # type: ignore[no-untyped-def]
    """C8/V15/V43: the operator's reason is on this row, and the LLM never sees it.

    The row genuinely holds one — asserted, so this is not a test that passes because nothing
    wrote a reason in the first place (V87's shape). What the reader returns has no field for
    it and no copy of it anywhere in the payload.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    await approve(factory, request_id, caller_user_id=user_id)

    stored = await read_request(factory, request_id)
    assert stored.reason == REASON

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    assert not hasattr(view, "reason")
    assert REASON not in json.dumps(view.as_payload())


async def test_preflight_evidence_never_reaches_the_result(factory) -> None:  # type: ignore[no-untyped-def]
    """V17: the evidence is born in-process and stays out of the transcript.

    It sits on the same JSONB payload the arguments come out of, so the compare has to
    separate: the arguments *do* arrive, and the evidence and the requester block do not. The
    two sentinels appear nowhere else in the context on purpose — the shipped fixture reuses
    `acmeco` across arguments and evidence, and a substring scan against that would go red for
    the wrong reason and green for the wrong one just as easily.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(
        factory,
        requested_by_user_id=user_id,
        approval_context=SEPARABLE_CONTEXT,
    )

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    payload_object = view.as_payload()
    payload = json.dumps(payload_object)

    assert view.arguments == SEPARABLE_CONTEXT["arguments"]
    assert "evidence" not in payload_object
    assert "requester" not in payload_object
    assert EVIDENCE_SENTINEL not in payload
    assert REQUESTER_SENTINEL not in payload


async def test_a_receipt_never_reaches_the_result(factory) -> None:  # type: ignore[no-untyped-def]
    """V76, V17: T42's card renders the receipt; this reader does not even fetch one.

    The receipt is the same before-state one table over (T38 copies `approval_context`'s
    evidence onto it), so a join added here would put V17's in-process evidence back on the
    path that answers into a transcript LibreChat persists. The row genuinely exists —
    asserted against the table — so this is not green because nothing wrote a receipt.

    Both sentinels, because both halves would arrive together: the before-state that must not
    travel for V17's reason, and the after-state that must not for V76's.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(
        factory,
        requested_by_user_id=user_id,
        approval_context=SEPARABLE_CONTEXT,
    )
    tool_run_id = await approve(factory, request_id, caller_user_id=user_id)
    await write_receipt(factory, request_id, tool_run_id=tool_run_id)

    async with factory() as session:
        stored = await session.execute(sa.text("SELECT count(*) FROM action_receipts"))
        assert stored.scalar_one() == 1

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    payload_object = view.as_payload()
    payload = json.dumps(payload_object)

    assert not hasattr(view, "receipt")
    assert "receipt" not in payload_object
    assert RECEIPT_SENTINEL not in payload
    assert EVIDENCE_SENTINEL not in payload
    # The run still arrives: what a model may be told about an approved change is its status
    # and its redacted summary, and dropping that too would make this pass for the wrong
    # reason.
    assert payload_object["run"] is not None


# --------------------------------------------------------------------------------------
# V47: the run an approval started
# --------------------------------------------------------------------------------------


async def test_an_approved_requests_run_is_reported(factory) -> None:  # type: ignore[no-untyped-def]
    """V29/V47: the link T37 wrote in the decision's transaction is what this reads.

    `STARTED` with no summary is today's honest answer — T38's executor, which moves the row,
    is unbuilt. A model told "started" tells an operator to wait, which is true.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    tool_run_id = await approve(factory, request_id, caller_user_id=user_id)

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    assert view.status is ActionRequestStatus.APPROVED
    assert view.decided_at is not None
    assert view.run is not None
    assert view.run.tool_run_id == tool_run_id
    assert view.run.status is ToolRunStatus.STARTED
    assert view.run.result_summary is None
    assert view.run.completed_at is None


async def test_a_denied_request_reports_no_run(factory) -> None:  # type: ignore[no-untyped-def]
    """A denial did not run anything, and the outer join says so rather than inventing one."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        service = build_decision_service(SQLActionDecisionRepository(session))
        await service.deny(
            action_request_id=request_id,
            caller_user_id=user_id,
            reason=REASON,
        )

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    assert view.status is ActionRequestStatus.DENIED
    assert view.run is None


# --------------------------------------------------------------------------------------
# V32: no stale PENDING, and no write to a row that is not the caller's
# --------------------------------------------------------------------------------------


async def test_a_request_past_its_deadline_reads_expired_and_is_written_expired(factory) -> None:  # type: ignore[no-untyped-def]
    """V32/V23: the check-on-read makes the row terminal, so the next reader finds it so.

    Both halves, because either alone is a different bug: reporting `EXPIRED` without writing
    leaves the next reader to make the same discovery, and writing without reporting hands the
    model a `PENDING` nobody may act on.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-1)

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    assert view.status is ActionRequestStatus.EXPIRED
    assert view.decided_at is not None

    stored = await read_request(factory, request_id)
    assert stored.status is ActionRequestStatus.EXPIRED
    assert stored.decided_at is not None
    # An expiry is the absence of an answer, so it carries no reason — T34's CHECK exempts
    # `EXPIRED` precisely so it can, which means nothing at the database level would catch one
    # that did.
    assert stored.reason is None


async def test_a_live_request_is_left_pending(factory) -> None:  # type: ignore[no-untyped-def]
    """The negative control for the case above: a deadline in the future changes nothing."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=3600)

    view = await read_result(factory, request_id, requester_user_id=user_id)

    assert view is not None
    assert view.status is ActionRequestStatus.PENDING
    assert (await read_request(factory, request_id)).status is ActionRequestStatus.PENDING


async def test_a_request_exactly_on_its_deadline_reads_expired(factory) -> None:  # type: ignore[no-untyped-def]
    """`<=`, the same comparison the sweep and the decision door make (T39(a)).

    A row exactly on `expires_at` has to be one thing at all three doors. This one shares the
    sweep's statement, so the boundary is inherited rather than re-implemented — and asserted
    here so a third spelling could not appear without going red.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    deadline = datetime.now(UTC) + timedelta(hours=1)
    request_id = await open_request(factory, requested_by_user_id=user_id, expires_at=deadline)

    view = await read_result(factory, request_id, requester_user_id=user_id, now=deadline)

    assert view is not None
    assert view.status is ActionRequestStatus.EXPIRED


async def test_a_foreign_due_request_is_not_expired_by_this_read(factory) -> None:  # type: ignore[no-untyped-def]
    """V27/V32: the read path touches nothing that is not the caller's.

    `expire_if_due` narrows the sweep's statement to one id and takes no requester — so a read
    path that ran it before the requester-matched read would let a prompt-injected id make NOA
    write to a stranger's row while still answering this caller not-found. The row staying
    PENDING is the only place that ordering is visible.
    """
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    intruder_id = await insert_user(factory, OTHER_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=owner_id, expires_in_seconds=-1)

    assert await read_result(factory, request_id, requester_user_id=intruder_id) is None

    stored = await read_request(factory, request_id)
    assert stored.status is ActionRequestStatus.PENDING
    assert stored.decided_at is None

    # And the owner's own read still expires it, so the assertion above is about *who* asked
    # rather than about a check-on-read that never fires.
    owner_view = await read_result(factory, request_id, requester_user_id=owner_id)
    assert owner_view is not None
    assert owner_view.status is ActionRequestStatus.EXPIRED
