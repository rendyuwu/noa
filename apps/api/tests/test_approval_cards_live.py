"""The approval card's read, against a real Postgres (T41 — V27, V32, V35).

`test_approval_card_route.py` drives this path over doubles, which proves the route's shape, its
refusals and the order it does things in. Three claims here are claims *about the database*:

- **The provenance and the before-state come out of JSONB.** The requester block and the
  preflight evidence are keys on `action_requests.approval_context`, written by T33's gate and
  round-tripped through asyncpg. A double handing back a dict proves the mapper; it does not
  prove that what the gate wrote is what the card reads.
- **A deleted requester's row is refused.** `requested_by_user_id` is `SET NULL` (T34), so only
  a real `DELETE` produces the state every requester-match has to fail closed on.
- **The card and the three writers describe one row.** The `EXPIRED` a stale request becomes and
  the run an approval starts are written by other classes in `core.approvals`; this is where the
  card is put against them rather than against a fixture that agrees with it.
- **The receipt is a join, not a copy.** `action_receipts` is its own table with its own writer
  (T36, T38), and what a double can prove about that is only that a dict came back. Whether the
  join hangs off the requester-matched row — so a receipt is never fetched for a request the
  caller may not read — is a claim about the statement, and only Postgres answers it.

The requester-matched `SELECT` itself is shared with T63 (`core.approvals.reads`) and is covered
there too. It is re-asserted here rather than cited, because what a *shared* statement guarantees
about this repository is exactly what V69 says provenance does not: the card is its own caller,
so the card's own refusal is what gets tested.

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

from core.approvals.card import ApprovalCardService, ApprovalCardView, SQLApprovalCardRepository
from core.approvals.decisions import SQLActionDecisionRepository
from core.approvals.execution import build_receipt
from core.approvals.expiry import ActionRequestExpiryService, SQLActionRequestExpiryRepository
from core.audit.receipts import SQLActionReceiptRepository
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from core.db.models import User
from support.action_decisions import (
    CHANGE_TOOL,
    CONVERSATION_ID,
    REASON,
    build_decision_service,
    insert_user,
    open_request,
    read_request,
)
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_approval_cards_test"

OPERATOR_EMAIL = "operator@example.com"
OTHER_EMAIL = "someone-else@example.com"

# Sentinels that appear in exactly one branch of `approval_context`, so "the card shows the
# before-state" is a compare that can separate from "the card shows the arguments". The shipped
# fixture shares `acmeco` across both, which makes it the wrong instrument for this claim — the
# same reason `test_action_results_live.py` builds its own context.
EVIDENCE_SENTINEL = "before-state-only-the-approval-card-may-see"
REQUESTER_SENTINEL = "librechat-account-the-card-shows-as-origin"

EVIDENCE_HALF: dict[str, object] = {"before_state": EVIDENCE_SENTINEL}

SEPARABLE_CONTEXT: dict[str, object] = {
    "arguments": {"server_ref": "alpha", "account": "acmeco"},
    "requester": {"email": OPERATOR_EMAIL, "librechat_user_id": REQUESTER_SENTINEL},
    "evidence": EVIDENCE_HALF,
}

# What the runner answered, in the envelope `build_receipt` reads (T38). Its sentinel appears in
# no other branch of anything this file writes, so "the card carries the after-state" is a
# compare that separates from "the card carries the before-state twice" (V87).
AFTER_SENTINEL = "after-state-the-runner-reported"

RUNNER_PAYLOAD: dict[str, object] = {"ok": True, "result": AFTER_SENTINEL}


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


async def read_card(
    factory: async_sessionmaker[AsyncSession],
    action_request_id: UUID,
    *,
    requester_user_id: UUID,
    now: datetime | None = None,
) -> ApprovalCardView | None:
    """The production read path — the card reader and the expiry, over one session (T41)."""
    async with factory() as session:
        service = ApprovalCardService(
            repository=SQLApprovalCardRepository(session),
            expiry=ActionRequestExpiryService(SQLActionRequestExpiryRepository(session)),
        )
        return await service.card_for(
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
    tool_run_id: UUID | None = None,
    receipt_data: dict[str, object] | None = None,
) -> None:
    """A receipt through the production writer, in the production shape (T36, T38).

    `receipt_data` defaults to what `build_receipt` produces, so what this file reads back is
    what the executor actually stores rather than a hand-built payload that agrees with the
    reader. Overriding it is how the malformed-payload case reaches a row no writer would emit.
    """
    async with factory() as session:
        await SQLActionReceiptRepository(session).create_if_missing(
            action_request_id=action_request_id,
            tool_run_id=tool_run_id,
            receipt_data=(
                build_receipt(evidence=EVIDENCE_HALF, payload=RUNNER_PAYLOAD)
                if receipt_data is None
                else receipt_data
            ),
        )
        await session.commit()


async def delete_user(factory: async_sessionmaker[AsyncSession], user_id: UUID) -> None:
    """Delete the operator, leaving their requests behind with a NULL requester (T34)."""
    async with factory() as session:
        await session.execute(sa.delete(User).where(User.id == user_id))
        await session.commit()


# --------------------------------------------------------------------------------------
# V35: the provenance and the before-state, out of the row the gate wrote
# --------------------------------------------------------------------------------------


async def test_the_card_reads_the_provenance_and_the_evidence_the_gate_persisted(factory) -> None:  # type: ignore[no-untyped-def]
    """V33/V35: built once at gate time, persisted, read back — not rebuilt at render time.

    The compare separates: the arguments, the requester block *and* the evidence all arrive,
    and each sentinel lives in one branch of the payload only, so a mapper that read the wrong
    key would go red rather than accidentally right.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(
        factory,
        requested_by_user_id=user_id,
        approval_context=SEPARABLE_CONTEXT,
    )

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.action_request_id == request_id
    assert card.tool_name == CHANGE_TOOL
    assert card.status is ActionRequestStatus.PENDING
    assert card.conversation_ref == CONVERSATION_ID
    assert card.arguments == SEPARABLE_CONTEXT["arguments"]
    assert card.evidence == {"before_state": EVIDENCE_SENTINEL}
    assert card.requester.email == OPERATOR_EMAIL
    assert card.requester.librechat_user_id == REQUESTER_SENTINEL
    assert card.decided_at is None
    assert card.run is None

    stored = await read_request(factory, request_id)
    assert card.expires_at == stored.expires_at.replace(tzinfo=card.expires_at.tzinfo)


async def test_a_decided_requests_reason_is_on_the_row_and_not_on_the_card(factory) -> None:  # type: ignore[no-untyped-def]
    """C8/V15/V43: the card collects a reason; it does not replay one.

    The row genuinely holds one — asserted, so this is not green because nothing wrote a reason
    (V87's shape). The view has no field for it and the payload has no copy of it.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    await approve(factory, request_id, caller_user_id=user_id)

    stored = await read_request(factory, request_id)
    assert stored.reason == REASON

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert not hasattr(card, "reason")
    assert REASON not in json.dumps(card.as_payload())


async def test_a_context_the_gate_did_not_write_renders_empty_rather_than_raising(factory) -> None:  # type: ignore[no-untyped-def]
    """A card in front of an operator is the wrong place for a `KeyError` (V35).

    Reachable if a row predates the gate's guarantees or was written by something else. The
    honest render is "nothing recorded", not an exception — and not a claimed identity nobody
    persisted.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(
        factory,
        requested_by_user_id=user_id,
        approval_context={"arguments": "not-a-mapping", "requester": [], "evidence": None},
    )

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.arguments == {}
    assert card.evidence == {}
    assert card.requester.email == ""
    assert card.requester.librechat_user_id == ""


# --------------------------------------------------------------------------------------
# V27: the requester-match, against rows only a database produces
# --------------------------------------------------------------------------------------


async def test_another_operators_card_is_not_readable(factory) -> None:  # type: ignore[no-untyped-def]
    """V27: the row is not fetched, so no later branch can forget to drop it.

    The intruder is a real user with a real id, so the refusal is the requester-match and not a
    missing row; and the owner's read succeeding is what makes the pair separate.
    """
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    intruder_id = await insert_user(factory, OTHER_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=owner_id)

    assert await read_card(factory, request_id, requester_user_id=intruder_id) is None
    assert await read_card(factory, request_id, requester_user_id=owner_id) is not None


async def test_an_unknown_id_reads_as_nothing(factory) -> None:  # type: ignore[no-untyped-def]
    """The other half of the pair the route answers identically (V27)."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)

    assert await read_card(factory, uuid4(), requester_user_id=user_id) is None


async def test_a_card_whose_requester_was_deleted_belongs_to_nobody(factory) -> None:  # type: ignore[no-untyped-def]
    """V27 fails closed on the row only a real `DELETE` can produce (T34's `SET NULL`).

    `NULL = :caller` is NULL, never true, so the request matches nobody — including the
    operator who is now gone.
    """
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    other_id = await insert_user(factory, OTHER_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=owner_id)

    await delete_user(factory, owner_id)

    stored = await read_request(factory, request_id)
    assert stored.requested_by_user_id is None
    assert await read_card(factory, request_id, requester_user_id=owner_id) is None
    assert await read_card(factory, request_id, requester_user_id=other_id) is None


# --------------------------------------------------------------------------------------
# V29 / V34: one URL, through the answer
# --------------------------------------------------------------------------------------


async def test_the_card_reports_the_run_the_approval_started(factory) -> None:  # type: ignore[no-untyped-def]
    """V29/V34/V47: the link T37 wrote inside the decision's transaction is what this reads."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    tool_run_id = await approve(factory, request_id, caller_user_id=user_id)

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.status is ActionRequestStatus.APPROVED
    assert card.is_pending is False
    assert card.decided_at is not None
    assert card.run is not None
    assert card.run.tool_run_id == tool_run_id
    assert card.run.status is ToolRunStatus.STARTED


# --------------------------------------------------------------------------------------
# V46 / V34 / DECISIONS §6.5: the receipt, in the two halves it was written as
# --------------------------------------------------------------------------------------


async def test_the_card_carries_the_receipt_its_writer_wrote(factory) -> None:  # type: ignore[no-untyped-def]
    """T42(b): the row T38 writes is the row this card reads, through a real join.

    The compare separates on the property the requirement is about — before and after are two
    payloads with two sentinels, neither of which appears in the other. A reader that showed
    the before-state twice, or collapsed the pair into one "done", goes red here.

    Nothing is re-derived on the way out: `after` is compared against the payload the writer
    redacted (V8, V45) rather than against a shape rebuilt here.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(
        factory,
        requested_by_user_id=user_id,
        approval_context=SEPARABLE_CONTEXT,
    )
    tool_run_id = await approve(factory, request_id, caller_user_id=user_id)
    await write_receipt(factory, request_id, tool_run_id=tool_run_id)

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.receipt is not None
    assert card.receipt.ok is True
    assert card.receipt.before == EVIDENCE_HALF
    assert card.receipt.after == RUNNER_PAYLOAD
    assert card.receipt.error_code is None
    # The pair, stated as a pair: one URL owns the question and the answer (V34), and the
    # answer is two halves that do not agree with each other.
    assert card.receipt.before != card.receipt.after
    assert AFTER_SENTINEL in json.dumps(card.as_payload())


async def test_a_card_with_no_receipt_reports_none(factory) -> None:  # type: ignore[no-untyped-def]
    """The separating case (V87): the join runs and finds nothing, and that is an answer.

    Without this, "the card carries the receipt" passes just as well against a reader that
    invents an empty one for every request — which would render an outcome section over a
    change that never ran.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    await approve(factory, request_id, caller_user_id=user_id)

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.run is not None
    assert card.receipt is None
    assert card.as_payload()["receipt"] is None


async def test_another_operators_receipt_is_not_readable(factory) -> None:  # type: ignore[no-untyped-def]
    """V27: the receipt rides on the requester-matched statement, so it is never fetched either.

    The receipt genuinely exists — the owner's read proves it — which is what makes the
    intruder's `None` a refusal rather than an empty table (V87).
    """
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    intruder_id = await insert_user(factory, OTHER_EMAIL)
    request_id = await open_request(
        factory,
        requested_by_user_id=owner_id,
        approval_context=SEPARABLE_CONTEXT,
    )
    tool_run_id = await approve(factory, request_id, caller_user_id=owner_id)
    await write_receipt(factory, request_id, tool_run_id=tool_run_id)

    assert await read_card(factory, request_id, requester_user_id=intruder_id) is None

    owners = await read_card(factory, request_id, requester_user_id=owner_id)
    assert owners is not None
    assert owners.receipt is not None


async def test_a_receipt_payload_no_writer_would_emit_renders_empty_rather_than_raising(  # type: ignore[no-untyped-def]
    factory,
) -> None:
    """V38: a card in front of an operator is the wrong place for a `TypeError`.

    `receipt_data` is unversioned JSONB (T36), so a row written by something other than T38's
    two writers is expressible. Both halves render as "nothing recorded", and `ok` fails closed
    on a truthy value that is not `True` — reading `"yes"` as success is the one direction that
    must not be permissive.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id)
    tool_run_id = await approve(factory, request_id, caller_user_id=user_id)
    await write_receipt(
        factory,
        request_id,
        tool_run_id=tool_run_id,
        receipt_data={"ok": "yes", "before": "not-a-mapping", "after": None, "error_code": ""},
    )

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.receipt is not None
    assert card.receipt.ok is False
    assert card.receipt.before == {}
    assert card.receipt.after == {}
    assert card.receipt.error_code is None


# --------------------------------------------------------------------------------------
# V32: no stale PENDING, and no write to a row that is not the caller's
# --------------------------------------------------------------------------------------


async def test_a_card_past_its_deadline_reads_expired_and_is_written_expired(factory) -> None:  # type: ignore[no-untyped-def]
    """V32: the render path makes the row terminal, so the next reader finds it so.

    Both halves: reporting `EXPIRED` without the write leaves the next reader to rediscover it,
    and writing without reporting hands the operator a live Approve button over a request the
    decision door would refuse.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=-1)

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.status is ActionRequestStatus.EXPIRED
    assert card.is_pending is False
    assert card.decided_at is not None

    stored = await read_request(factory, request_id)
    assert stored.status is ActionRequestStatus.EXPIRED
    # An expiry is the absence of an answer, so it carries no reason — T34's CHECK exempts
    # `EXPIRED` precisely so it can, which means nothing at the database level would catch one
    # that did (V15, T39).
    assert stored.reason is None


async def test_a_live_card_is_not_expired_by_being_read(factory) -> None:  # type: ignore[no-untyped-def]
    """The separating case (V87): the check runs, and on a live row it changes nothing."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=user_id, expires_in_seconds=3600)

    card = await read_card(factory, request_id, requester_user_id=user_id)

    assert card is not None
    assert card.status is ActionRequestStatus.PENDING
    assert (await read_request(factory, request_id)).status is ActionRequestStatus.PENDING


async def test_a_foreign_card_is_never_written_to(factory) -> None:  # type: ignore[no-untyped-def]
    """V27 before V32, and the table is the only place that ordering is visible.

    `expire_if_due` takes an id and no requester, and the id in this URL reaches the operator
    through a tool result that persists in LibreChat's MongoDB (V26). An expiry-first card would
    let an id its reader cannot see be written to; reading first makes it a pure no-op.
    """
    owner_id = await insert_user(factory, OPERATOR_EMAIL)
    intruder_id = await insert_user(factory, OTHER_EMAIL)
    request_id = await open_request(factory, requested_by_user_id=owner_id, expires_in_seconds=-1)

    assert await read_card(factory, request_id, requester_user_id=intruder_id) is None

    stored = await read_request(factory, request_id)
    assert stored.status is ActionRequestStatus.PENDING
    assert stored.decided_at is None


async def test_the_deadline_boundary_is_the_same_at_the_card_as_at_the_door(factory) -> None:  # type: ignore[no-untyped-def]
    """T39(a): `<=` at both doors, so a row exactly on its deadline is one thing, not two."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    request_id = await open_request(factory, requested_by_user_id=user_id, expires_at=deadline)

    on_the_deadline = await read_card(factory, request_id, requester_user_id=user_id, now=deadline)

    assert on_the_deadline is not None
    assert on_the_deadline.status is ActionRequestStatus.EXPIRED
