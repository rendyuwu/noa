"""`action_receipts` table (T36, V46).

Two levels, for two different claims:

- **Metadata** — the column set, the unique constraint, the index set, and the absences the
  port's extra columns turn on. No DB needed.
- **Live** — that Postgres refuses a second receipt for one request, refuses one for a
  request that does not exist, and that deleting the request takes the receipt with it
  while deleting the run does not. None of those is provable from `Base.metadata`: an FK's
  `ondelete` is a string until a real `DELETE` runs against it, and a `UniqueConstraint` in
  metadata says nothing about what the migration built. Skipped (never failed) when
  Postgres is unreachable, like every other DB-backed test here.

This file is about the *shape*, not the writer. Nothing writes this table yet — T38's
executor does, and T42's card and T63's `noa_get_action_result` read it beside the run.
What is asserted here is that the columns V46 asks for exist and hold, so none of those
three can quietly reshape them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db import Base
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import ActionReceipt, ActionRequest, ToolRun, User
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_action_receipts_schema_test"

ACTION_RECEIPTS = Base.metadata.tables["action_receipts"]

# §T.36's column list, verbatim. A column added without a task to specify it is a guess
# (`test_schema_v1.py` guards whole tables the same way); one removed takes a V46 field
# with it.
T36_COLUMNS = {
    "id",
    "action_request_id",
    "tool_run_id",
    "receipt_data",
    "created_at",
}

# What this table refuses. The first three are `noa-old`'s, dropped on purpose; the last two
# are the shapes a later edit would most plausibly reach for. Named one by one so a failure
# says which boundary was crossed rather than only that a set differs.
FORBIDDEN_COLUMNS = {
    # C16 drops the multi-phase workflows this described. The terminal state lives on
    # `tool_runs.status` and `action_requests.status`; a third column saying it again is a
    # third truth about one moment (T34's argument for the four columns it dropped).
    "terminal_phase": "V20 — terminal state lives on `tool_runs`/`action_requests`",
    # `approval_context` and `tool_runs.args` are both unversioned JSONB. Versioning the
    # third would make their bareness look deliberate when it is not.
    "schema_version": "consistency — the other two JSONB payloads carry no version",
    # §T.36 names `receipt_data`. `payload` is the port's name for the same column, and two
    # names for one column is how a writer ends up filling the wrong one.
    "payload": "§T.36 names `receipt_data`",
    # C8, V43: the reason is operator-typed on the card and lives on `action_requests`.
    # V43 says "⊥ LLM-authored reason anywhere in schema/DB/**receipt**" — this is the
    # receipt half of that sentence.
    "reason": "C8, V43 — one reason, and it is on `action_requests`",
    # A receipt records one terminal moment. There is nothing to update.
    "updated_at": "a receipt is written once; there is no second moment to stamp",
}


# --------------------------------------------------------------------------------------
# Metadata: shape, uniqueness, indexes
# --------------------------------------------------------------------------------------


def test_column_set_is_exactly_what_t36_specifies() -> None:
    """No extra columns, no missing ones."""
    assert set(ACTION_RECEIPTS.c.keys()) == T36_COLUMNS


@pytest.mark.parametrize(("column", "why"), sorted(FORBIDDEN_COLUMNS.items()))
def test_the_columns_this_design_refuses_are_absent(column: str, why: str) -> None:
    """Each absence is load-bearing, not an oversight — the reason is the test id."""
    assert column not in ACTION_RECEIPTS.c, why


def test_one_receipt_per_request_is_a_constraint_not_a_convention() -> None:
    """V28, V34: one decision, one outcome.

    `noa-old` made `action_request_id` the primary key, so this came with the table. §T.36
    names a separate `id`, which means the property is stated here or it is lost in the
    port — and losing it is not theoretical: T38's executor and its reaper can both reach a
    finished run, and a second receipt turns "the receipt" into "some receipt".
    """
    unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in ACTION_RECEIPTS.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }

    assert ("action_request_id",) in unique


def test_the_request_link_is_required() -> None:
    """A receipt is always *about* a request; there is no free-standing one.

    Unlike every other FK added since T35, which are nullable because their rows still
    describe something without their subject. This one would not: the tool name, the
    requester and the arguments all live on `action_requests`.
    """
    action_request_id = ACTION_RECEIPTS.c.action_request_id

    assert action_request_id.nullable is False
    (fk,) = action_request_id.foreign_keys
    assert fk.column is Base.metadata.tables["action_requests"].c.id
    assert fk.ondelete == "CASCADE"


def test_the_run_link_is_described_the_same_way_at_both_ends() -> None:
    """One edge, one spelling (T34).

    `action_requests.tool_run_id` is nullable with `SET NULL`; so is this. NULL describes
    life after the run row is deleted, not a receipt written without one.
    """
    tool_run_id = ACTION_RECEIPTS.c.tool_run_id
    (fk,) = tool_run_id.foreign_keys

    assert tool_run_id.nullable is True
    assert fk.column is Base.metadata.tables["tool_runs"].c.id
    assert fk.ondelete == "SET NULL"
    assert Base.metadata.tables["action_requests"].c.tool_run_id.nullable is True


def test_receipt_data_is_required_and_has_no_default() -> None:
    """V46: a receipt with nothing in it is a failed insert, not a stored row.

    Deliberately unlike `tool_runs.args`, which defaults to `'{}'` so "took no arguments"
    and "not recorded" stay distinguishable, and exactly like `approval_context`: there is
    no such thing as an approved change that did nothing worth recording.
    """
    receipt_data = ACTION_RECEIPTS.c.receipt_data

    assert receipt_data.nullable is False
    assert receipt_data.server_default is None


def test_the_index_set_is_only_what_a_reader_needs() -> None:
    """Every reader arrives holding an `action_request_id`, which the unique already indexes.

    `noa-old` also indexed `tool_run_id`, `terminal_phase` and `created_at`. Nothing filters
    or orders by them here — the discipline that left `risk` unindexed at T35 and kept T34's
    index set smaller than `tool_runs`'.
    """
    assert {index.name for index in ACTION_RECEIPTS.indexes} == set()


def test_created_at_is_the_only_timestamp() -> None:
    """A receipt is written once, at the end. There is no second moment to stamp."""
    created_at = ACTION_RECEIPTS.c.created_at

    assert created_at.nullable is False
    assert created_at.type.timezone is True
    assert [column.name for column in ACTION_RECEIPTS.c if column.name.endswith("_at")] == [
        "created_at"
    ]


# --------------------------------------------------------------------------------------
# Live: what only a real Postgres can answer
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test engine and session, mutated tables emptied first.

    Function-scoped because an asyncpg connection belongs to the loop that opened it.
    """
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
            yield opened
    finally:
        await engine.dispose()


RECEIPT_DATA = {
    "before": {"blocked_by": "csf", "log_line": "Deny 203.0.113.7 # lfd: brute force"},
    "after": {"released": True, "allowlisted": True, "expires_at": "2026-08-09T18:00:00Z"},
}


async def insert_user(session: AsyncSession, email: str) -> UUID:
    user = User(email=email, is_active=True)
    session.add(user)
    await session.commit()
    return user.id


async def insert_run(session: AsyncSession) -> UUID:
    run = ToolRun(
        tool_name="whm_firewall_release_and_allow",
        risk=ToolRisk.CHANGE,
        status=ToolRunStatus.COMPLETED,
    )
    session.add(run)
    await session.commit()
    return run.id


async def insert_approved_request(session: AsyncSession, *, tool_run_id: UUID | None) -> UUID:
    """An APPROVED row, which is the only state a receipt is ever written for (V46)."""
    request = ActionRequest(
        tool_name="whm_firewall_release_and_allow",
        approval_context={"args": {"server_ref": "whm-1", "target": "203.0.113.7"}},
        status=ActionRequestStatus.APPROVED,
        reason="Customer confirmed the address is theirs on ticket NOC-4471.",
        decided_at=datetime.now(UTC),
        tool_run_id=tool_run_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(request)
    await session.commit()
    return request.id


async def test_a_receipt_round_trips_its_payload(session: AsyncSession) -> None:
    """V46: the approved change's outcome, stored beside the run that produced it.

    The payload shape is T38's, not this column's — what is asserted here is only that JSONB
    gives back what the executor put in, so DECISIONS §6.5's two-part story (before-state and
    after-state, each verified separately) survives a round trip without being collapsed.
    """
    await insert_user(session, "operator@example.com")
    run_id = await insert_run(session)
    request_id = await insert_approved_request(session, tool_run_id=run_id)

    session.add(
        ActionReceipt(action_request_id=request_id, tool_run_id=run_id, receipt_data=RECEIPT_DATA)
    )
    await session.commit()

    stored = (await session.execute(sa.select(ActionReceipt))).scalar_one()

    assert stored.receipt_data == RECEIPT_DATA
    assert stored.action_request_id == request_id
    assert stored.tool_run_id == run_id
    assert stored.created_at is not None


async def test_a_second_receipt_for_one_request_is_refused(session: AsyncSession) -> None:
    """V28, V34: one decision, one outcome — held by the database, not by the writer.

    This is the case the port got for free from its primary key and this shape has to state.
    T38's executor and its reaper can both reach a finished run; without the constraint the
    second write succeeds and every reader afterwards picks one of two receipts arbitrarily.
    """
    run_id = await insert_run(session)
    request_id = await insert_approved_request(session, tool_run_id=run_id)
    session.add(
        ActionReceipt(action_request_id=request_id, tool_run_id=run_id, receipt_data=RECEIPT_DATA)
    )
    await session.commit()

    session.add(
        ActionReceipt(
            action_request_id=request_id,
            tool_run_id=run_id,
            receipt_data={"before": {}, "after": {"released": False}},
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_two_requests_may_each_have_their_own_receipt(session: AsyncSession) -> None:
    """The negative control for the case above (V87).

    Without it, "the database refuses a second receipt" is a claim about nothing: a table
    that refuses *every* second receipt satisfies it while breaking every change after the
    first, and the test above cannot tell the two apart.

    Proven to separate, not assumed: moving the unique constraint onto `receipt_data`
    (a plausible wrong column — one payload, one row) turns this test red while leaving the
    metadata shape intact.
    """
    first = await insert_approved_request(session, tool_run_id=None)
    second = await insert_approved_request(session, tool_run_id=None)

    session.add(ActionReceipt(action_request_id=first, receipt_data=RECEIPT_DATA))
    session.add(ActionReceipt(action_request_id=second, receipt_data=RECEIPT_DATA))
    await session.commit()

    stored = (await session.execute(sa.select(ActionReceipt))).scalars().all()

    assert {receipt.action_request_id for receipt in stored} == {first, second}


async def test_a_receipt_cannot_exist_without_its_request(session: AsyncSession) -> None:
    """The FK, not a convention: there is no free-standing receipt.

    An id nothing authorised is the shape of the mistake that matters — a writer holding a
    stale or fabricated request id would otherwise store an outcome for a change nobody
    approved (V23: the request row *is* the authorization).
    """
    session.add(ActionReceipt(action_request_id=uuid4(), receipt_data=RECEIPT_DATA))

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_receipt_data_cannot_be_omitted(session: AsyncSession) -> None:
    """No default, so a receipt with nothing in it is a failed insert, not a stored row."""
    request_id = await insert_approved_request(session, tool_run_id=None)
    session.add(ActionReceipt(action_request_id=request_id))

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_deleting_the_request_removes_the_receipt(session: AsyncSession) -> None:
    """CASCADE, proven by a real DELETE.

    `ondelete` is a string in metadata until something deletes (T34's rule). This is the one
    FK in the schema that cascades rather than nulls, because a receipt whose request is gone
    describes nothing: the tool name, the requester and the arguments all lived on that row.
    The audit artifact that must survive a user deletion is `action_requests` itself, and T34
    made both of its FKs `SET NULL` for exactly that.
    """
    request_id = await insert_approved_request(session, tool_run_id=None)
    session.add(ActionReceipt(action_request_id=request_id, receipt_data=RECEIPT_DATA))
    await session.commit()

    await session.execute(sa.delete(ActionRequest).where(ActionRequest.id == request_id))
    await session.commit()

    remaining = (await session.execute(sa.select(ActionReceipt))).scalars().all()

    assert remaining == []


async def test_deleting_the_run_keeps_the_receipt(session: AsyncSession) -> None:
    """SET NULL, mirroring `action_requests.tool_run_id`.

    Losing the execution row does not erase what the change did — the receipt is the record
    of that, and it is still about a request that still exists.
    """
    run_id = await insert_run(session)
    request_id = await insert_approved_request(session, tool_run_id=run_id)
    session.add(
        ActionReceipt(action_request_id=request_id, tool_run_id=run_id, receipt_data=RECEIPT_DATA)
    )
    await session.commit()

    await session.execute(sa.delete(ToolRun).where(ToolRun.id == run_id))
    await session.commit()
    session.expire_all()

    surviving = (await session.execute(sa.select(ActionReceipt))).scalar_one()

    assert surviving.action_request_id == request_id
    assert surviving.tool_run_id is None
    assert surviving.receipt_data == RECEIPT_DATA


async def test_deleting_the_requester_keeps_the_receipt(session: AsyncSession) -> None:
    """The chain that matters end to end: a user deletion erases neither half.

    T34 made `action_requests.requested_by_user_id` `SET NULL` naming this table as the
    reason ("cascading would let one user deletion erase both what was authorised and the
    receipt proving it ran"). That sentence is asserted here rather than only written there,
    because it is a claim about two tables and neither file can make it alone.
    """
    user_id = await insert_user(session, "leaver@example.com")
    run_id = await insert_run(session)
    request = ActionRequest(
        tool_name="whm_firewall_release_and_allow",
        requested_by_user_id=user_id,
        approval_context={"args": {"server_ref": "whm-1", "target": "203.0.113.7"}},
        status=ActionRequestStatus.APPROVED,
        reason="Approved before the account was closed.",
        decided_at=datetime.now(UTC),
        tool_run_id=run_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(request)
    await session.commit()
    session.add(
        ActionReceipt(action_request_id=request.id, tool_run_id=run_id, receipt_data=RECEIPT_DATA)
    )
    await session.commit()

    await session.execute(sa.delete(User).where(User.id == user_id))
    await session.commit()
    session.expire_all()

    surviving_request = (await session.execute(sa.select(ActionRequest))).scalar_one()
    surviving_receipt = (await session.execute(sa.select(ActionReceipt))).scalar_one()

    assert surviving_request.requested_by_user_id is None
    assert surviving_request.status is ActionRequestStatus.APPROVED
    assert surviving_receipt.receipt_data == RECEIPT_DATA
