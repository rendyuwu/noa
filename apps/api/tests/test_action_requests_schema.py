"""`action_requests` table and the decision enum (T34, V20, V32, V33, V43).

Two levels, for two different claims:

- **Metadata** — the column set, the enum definition, the indexes, and the absences C8
  and V43 turn on. No DB needed.
- **Live** — that Postgres refuses an off-list status, that a row can carry each state
  the lifecycle needs, and that deleting either the requester or the run leaves the
  decision standing. None of those is provable from `Base.metadata`: SQLAlchemy renders a
  CHECK constraint whether or not the migration created one, and an FK's `ondelete` is a
  string until a real `DELETE` runs against it. Skipped (never failed) when Postgres is
  unreachable, like every other DB-backed test here.

This file is about the *shape*, not the writer. T33 inserts the row, T37 decides it, T38
links the run and T39 expires it; what is asserted here is that the columns those tasks
need exist and hold, so none of them can quietly reshape the table.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db import Base
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import ActionRequest, ToolRun, User
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_action_requests_schema_test"

ACTION_REQUESTS = Base.metadata.tables["action_requests"]

# §T.34's column list, verbatim. A column added without a task to specify it is a guess
# (`test_schema_v1.py` guards whole tables the same way); one removed takes a V20/V32/V33
# field with it.
T34_COLUMNS = {
    "id",
    "tool_name",
    "requested_by_user_id",
    "status",
    "conversation_ref",
    "approval_context",
    "reason",
    "tool_run_id",
    "expires_at",
    "created_at",
    "decided_at",
}

# What §T.34 says must NOT be here, plus the four `noa-old` carried that this design
# refuses. Named one by one so a failure says which boundary was crossed.
FORBIDDEN_COLUMNS = {
    # C8, V43: the reason is operator-typed at approve time. `noa-old` had no such column
    # either, but it did worse — the reason rode inside `args`, so the LLM authored it.
    "proposed_reason": "C8, V43 — the LLM never authors, relays or sees a reason",
    # V33: one gate-time payload, one record of one moment.
    "args": "V33 — arguments live inside `approval_context`",
    # V16: READs never reach the gate, so every row here is a CHANGE.
    "risk": "V20 — risk lives on `tool_runs`, which is what makes a failed READ writable",
    # V27: the decider is the requester; a mismatch is a 404.
    "decided_by_user_id": "V27 — one identity, not two truths about one person",
    # V28 permits one mutation and `decided_at` stamps it.
    "updated_at": "V28 — `decided_at` is the only mutation timestamp",
}

# Nothing filters this table the way §I.admin-api filters the audit surface, so the index
# set is deliberately smaller than `tool_runs`'. `status` leads the composite, so
# status-only lookups use it too.
INDEXED_COLUMNS = {"status", "expires_at", "requested_by_user_id"}


# --------------------------------------------------------------------------------------
# Metadata: shape, enum, absences, indexes
# --------------------------------------------------------------------------------------


def test_column_set_is_exactly_what_t34_specifies() -> None:
    """No extra columns, no missing ones."""
    assert set(ACTION_REQUESTS.c.keys()) == T34_COLUMNS


@pytest.mark.parametrize(("column", "why"), sorted(FORBIDDEN_COLUMNS.items()))
def test_the_columns_this_design_refuses_are_absent(column: str, why: str) -> None:
    """Each absence is load-bearing, not an oversight — the reason is the test id."""
    assert column not in ACTION_REQUESTS.c, why


def test_exactly_one_reason_column_exists() -> None:
    """V43: exactly ONE reason field, and this is it.

    Not "a reason column exists" — *one*. A second spelling anywhere on the row is what
    V43 forbids, and `proposed_reason` was only the name `noa-old`'s design implied; the
    assertion is over the whole column set so a differently-named twin fails too.
    """
    reason_columns = [name for name in ACTION_REQUESTS.c.keys() if "reason" in name]

    assert reason_columns == ["reason"]


def test_no_table_in_the_schema_carries_a_proposed_reason_column() -> None:
    """V43 across the whole schema, not just this table.

    Written over `Base.metadata` on purpose: T36's `action_receipts` and every later table
    inherit the rule without being listed here, the same way `test_config.py` scans every
    tracked file rather than an enumerated set. V43 says "⊥ LLM-authored reason anywhere
    in schema/DB/receipt", and the receipt is the half that has not been built yet.
    """
    offenders = {
        f"{table_name}.{column.name}"
        for table_name, table in Base.metadata.tables.items()
        for column in table.c
        if "reason" in column.name and column.name != "reason"
    }

    assert offenders == set()


def test_reason_is_nullable_and_unbounded() -> None:
    """V15: NULL until an operator types one; still NULL after an expiry.

    Unbounded `Text` rather than a capped VARCHAR because no machine writes it. There is
    no result body to invite in (the reason `tool_runs.result_summary` is capped), and
    truncating the field that authorises a change is worse than storing a long one. T37
    bounds the input at the endpoint.
    """
    reason = ACTION_REQUESTS.c.reason

    assert reason.nullable is True
    assert reason.type.python_type is str
    assert getattr(reason.type, "length", None) is None


def test_action_request_status_is_exactly_v20s_four_members() -> None:
    """V20: machine-stable member set, `EXPIRED` included.

    Exact set, so a rename is a test failure rather than a silent data change. `EXPIRED`
    is the one `noa-old` lacked — without it a request nobody answered stayed PENDING
    forever and V32 had no terminal state to sweep into.
    """
    assert {member.name: member.value for member in ActionRequestStatus} == {
        "PENDING": "PENDING",
        "APPROVED": "APPROVED",
        "DENIED": "DENIED",
        "EXPIRED": "EXPIRED",
    }


def test_the_three_lifecycle_enums_are_distinct_with_disjoint_values() -> None:
    """V20: three distinct enums, not one flat set — and now all three exist.

    Disjoint values, because a shared member would let a query written against one column
    match rows in another. Distinct types, because folding decision state into run state
    would make an APPROVED request whose run then failed unrepresentable.
    """
    enums = (ToolRisk, ToolRunStatus, ActionRequestStatus)

    assert len({id(enum) for enum in enums}) == 3
    values = [{member.value for member in enum} for enum in enums]
    assert values[0] & values[1] == set()
    assert values[0] & values[2] == set()
    assert values[1] & values[2] == set()


def test_status_is_a_checked_varchar_not_a_native_postgres_enum() -> None:
    """V20 "machine-stable" enforced by the database, not by application discipline.

    `native_enum=False` alone leaves an unconstrained VARCHAR — SQLAlchemy 2.0 defaults
    `create_constraint` to `False`. The CHECK is what makes a renamed member a migration.
    """
    status = ACTION_REQUESTS.c.status

    assert isinstance(status.type, sa.Enum)
    assert status.type.native_enum is False
    assert status.type.create_constraint is True
    assert list(status.type.enums) == [member.value for member in ActionRequestStatus]


def test_status_defaults_to_pending() -> None:
    """The gate (T33) inserts before anyone has decided anything, so PENDING is truthful.

    A server-side default, not an ORM-side one: V23 reads this column as the authorization,
    and a row that reached the table by any route must not read as APPROVED.
    """
    assert ACTION_REQUESTS.c.status.server_default.arg == ActionRequestStatus.PENDING.value
    assert ACTION_REQUESTS.c.status.nullable is False


def test_approval_context_is_required_and_has_no_default() -> None:
    """V33: built at gate time and persisted here, never rebuilt at render time.

    No server default, deliberately unlike `tool_runs.args` (which defaults to `'{}'` so
    "took no arguments" and "not recorded" stay distinguishable). Here an empty context is
    never a legitimate state — the gate always has provenance and arguments to put in it —
    so an insert that omits it should fail rather than record a card with nothing on it.
    """
    approval_context = ACTION_REQUESTS.c.approval_context

    assert approval_context.nullable is False
    assert approval_context.server_default is None


def test_expires_at_is_required_so_every_pending_row_carries_a_deadline() -> None:
    """V32: a row without a deadline cannot expire, and "pending forever" is what V32 removes.

    Required rather than nullable, so the sweep in T39 covers the whole table by
    construction instead of by whoever remembered to set it.
    """
    expires_at = ACTION_REQUESTS.c.expires_at

    assert expires_at.nullable is False
    assert isinstance(expires_at.type, sa.DateTime)
    assert expires_at.type.timezone is True


def test_decided_at_is_nullable_and_is_the_only_mutation_timestamp() -> None:
    """V28: exactly one pending → decided transition, so one stamp records it.

    NULL while PENDING. An `updated_at` beside it would be a second timestamp for the same
    event, and a reader would have to know which one meant the decision.
    """
    decided_at = ACTION_REQUESTS.c.decided_at

    assert decided_at.nullable is True
    assert decided_at.type.timezone is True
    assert "updated_at" not in ACTION_REQUESTS.c


def test_the_link_to_a_run_lives_here_and_only_here() -> None:
    """One edge, one column (T38).

    NULL until an approval starts a run, and NULL forever on deny or expiry. `tool_runs`
    deliberately has no `action_request_id` pointing back — two columns describing one
    edge can disagree, and `test_tool_runs_schema.py` records the same decision from its
    side.
    """
    tool_run_id = ACTION_REQUESTS.c.tool_run_id

    assert tool_run_id.nullable is True
    (fk,) = tool_run_id.foreign_keys
    assert fk.column is Base.metadata.tables["tool_runs"].c.id
    assert "action_request_id" not in Base.metadata.tables["tool_runs"].c


def test_both_foreign_keys_set_null_rather_than_cascading() -> None:
    """The decision record outlives its subjects.

    `noa-old` cascaded the requester. Here an approved CHANGE is an audit artifact (V46)
    and T36's receipts hang off this row, so a cascade would let one user deletion erase
    both the record of what was authorised and the receipt proving it ran. Fails closed
    against V27: NULL matches no caller, so a requester-match lookup 404s.
    """
    for column_name in ("requested_by_user_id", "tool_run_id"):
        (fk,) = ACTION_REQUESTS.c[column_name].foreign_keys
        assert fk.ondelete == "SET NULL", column_name
        assert ACTION_REQUESTS.c[column_name].nullable is True, column_name


def test_conversation_ref_is_a_nullable_label() -> None:
    """DECISIONS §3.2: an audit/grouping label, never a security scope.

    Same column and same caveat as `tool_runs.conversation_ref` — LibreChat sends no
    conversation id in the call, so it arrives as an optional header (R27, R28) and is
    absent whenever the operator's YAML does not supply one.
    """
    conversation_ref = ACTION_REQUESTS.c.conversation_ref

    assert conversation_ref.nullable is True
    assert conversation_ref.type.length == 255


@pytest.mark.parametrize("column_name", sorted(INDEXED_COLUMNS))
def test_the_sweep_and_the_inflight_cap_are_indexed(column_name: str) -> None:
    """T39's expiry sweep and V31's per-user cap are the only recurring queries.

    Nothing filters by `tool_name`, `conversation_ref` or `created_at` today, so they are
    deliberately unindexed — the same discipline that kept `risk` unindexed on `tool_runs`.
    """
    indexed = {column.name for index in ACTION_REQUESTS.indexes for column in index.columns}

    assert column_name in indexed


def test_the_sweep_index_leads_with_status() -> None:
    """`status = PENDING AND expires_at < now()` — column order is the whole point.

    Led by `expires_at` instead, the index would not serve a status-only lookup and T39's
    sweep would scan rows already decided.
    """
    (sweep,) = [
        index for index in ACTION_REQUESTS.indexes if index.name.endswith("status_expires_at")
    ]

    assert [column.name for column in sweep.columns] == ["status", "expires_at"]


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


def pending_request(**overrides: object) -> ActionRequest:
    """A gate-time row (T33's shape), with only the required fields filled."""
    fields: dict[str, object] = {
        "tool_name": "whm_suspend_account",
        "approval_context": {"args": {"server_ref": "whm-1", "user": "acme"}},
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    fields.update(overrides)
    return ActionRequest(**fields)  # type: ignore[arg-type]


async def insert_user(session: AsyncSession, email: str) -> UUID:
    user = User(email=email, is_active=True)
    session.add(user)
    await session.commit()
    return user.id


async def insert_run(session: AsyncSession) -> UUID:
    run = ToolRun(
        tool_name="whm_suspend_account", risk=ToolRisk.CHANGE, status=ToolRunStatus.COMPLETED
    )
    session.add(run)
    await session.commit()
    return run.id


async def test_migration_created_the_check_constraint_on_status(session: AsyncSession) -> None:
    """V20: the database refuses a status the enum does not define.

    Raw SQL on purpose — the ORM's own `validate_strings` would catch it first and prove
    nothing about the schema the migration built. `WAITING` is the shape of the mistake
    that matters: a plausible extra state, added in code, that would otherwise let a row
    sit in a status V23 has no rule for.
    """
    with pytest.raises(IntegrityError):
        await session.execute(
            sa.text(
                "INSERT INTO action_requests (tool_name, status, approval_context, expires_at)"
                " VALUES (:tool, :status, '{}'::jsonb, now())"
            ),
            {"tool": "whm_suspend_account", "status": "WAITING"},
        )
        await session.commit()
    await session.rollback()


async def test_orm_refuses_an_unknown_member_before_the_flush(session: AsyncSession) -> None:
    """`validate_strings=True`: the same mistake caught earlier, in Python."""
    session.add(pending_request(status="WAITING"))

    with pytest.raises((StatementError, LookupError)):
        await session.commit()
    await session.rollback()


async def test_the_gate_row_defaults_to_pending(session: AsyncSession) -> None:
    """V23, T33: inserted with no status named, the row still reads PENDING.

    A server-side default, so this holds for any writer — not only for one that remembered
    to set it.
    """
    user_id = await insert_user(session, "operator@example.com")
    session.add(pending_request(requested_by_user_id=user_id, conversation_ref="conv-1"))
    await session.commit()

    stored = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert stored.status is ActionRequestStatus.PENDING
    assert stored.reason is None
    assert stored.decided_at is None
    assert stored.tool_run_id is None
    assert stored.created_at is not None


async def test_approval_context_round_trips_the_gate_time_payload(session: AsyncSession) -> None:
    """V33: persisted at gate time, read back unchanged when the card renders.

    The payload shape is T33's, not this column's — what is asserted here is only that
    JSONB gives back what the gate put in, so V35's provenance and V17's in-process
    preflight evidence never have to be rebuilt from a transcript.
    """
    context = {
        "args": {"server_ref": "whm-1", "user": "acme"},
        "provenance": {"origin": "librechat", "requested_by": "operator@example.com"},
        "preflight": {"account_exists": True, "already_suspended": False},
    }
    session.add(pending_request(approval_context=context))
    await session.commit()

    stored = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert stored.approval_context == context


async def test_approval_context_cannot_be_omitted(session: AsyncSession) -> None:
    """V33: no default, so a card with nothing on it is a failed insert, not a stored row."""
    session.add(
        ActionRequest(
            tool_name="whm_suspend_account", expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_request_cannot_be_created_without_a_deadline(session: AsyncSession) -> None:
    """V32: "pending forever" is unrepresentable, which is what makes T39's sweep total."""
    session.add(ActionRequest(tool_name="whm_suspend_account", approval_context={"args": {}}))

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_an_approval_records_reason_decision_time_and_run(session: AsyncSession) -> None:
    """V15, V28, T38: the full APPROVED shape survives a round trip.

    The reason arrives here and nowhere else (C8) — the column is NULL at insert and holds
    the operator's own words after the decision.
    """
    user_id = await insert_user(session, "operator@example.com")
    run_id = await insert_run(session)
    request = pending_request(requested_by_user_id=user_id)
    session.add(request)
    await session.commit()

    request.status = ActionRequestStatus.APPROVED
    request.reason = "Customer confirmed the abuse ticket by phone; suspending pending review."
    request.decided_at = datetime.now(UTC)
    request.tool_run_id = run_id
    await session.commit()
    session.expire_all()

    stored = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert stored.status is ActionRequestStatus.APPROVED
    assert stored.reason is not None
    assert stored.reason.startswith("Customer confirmed")
    assert stored.decided_at is not None
    assert stored.tool_run_id == run_id


async def test_a_pending_request_can_expire_without_a_reason(session: AsyncSession) -> None:
    """V32: an expiry is terminal, and nobody typed anything.

    This is why `reason` stays nullable after a decision and why DENIED and EXPIRED are
    separate members: a denial is an operator's answer, an expiry is the absence of one.
    """
    request = pending_request(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    session.add(request)
    await session.commit()

    request.status = ActionRequestStatus.EXPIRED
    request.decided_at = datetime.now(UTC)
    await session.commit()
    session.expire_all()

    stored = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert stored.status is ActionRequestStatus.EXPIRED
    assert stored.reason is None
    assert stored.tool_run_id is None


async def test_deleting_the_requester_keeps_the_request(session: AsyncSession) -> None:
    """The decision record outlives its subject; only the requester goes NULL.

    `ondelete` is a string in metadata until a real DELETE runs against it — this is the
    assertion the metadata test above cannot make. `noa-old` cascaded here, which would
    have erased what was authorised along with the person who asked.
    """
    user_id = await insert_user(session, "leaver@example.com")
    session.add(
        pending_request(
            requested_by_user_id=user_id,
            status=ActionRequestStatus.APPROVED,
            reason="Approved before the account was closed.",
            decided_at=datetime.now(UTC),
        )
    )
    await session.commit()

    await session.execute(sa.delete(User).where(User.id == user_id))
    await session.commit()
    session.expire_all()

    surviving = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert surviving.status is ActionRequestStatus.APPROVED
    assert surviving.reason == "Approved before the account was closed."
    assert surviving.requested_by_user_id is None


@pytest.mark.parametrize(
    "decided", [ActionRequestStatus.APPROVED, ActionRequestStatus.DENIED], ids=lambda s: s.value
)
@pytest.mark.parametrize(
    "reason",
    [None, "", "   ", "\t", "\n", " \t\n\r "],
    ids=["null", "empty", "spaces", "tab", "newline", "mixed"],
)
async def test_database_refuses_a_decided_row_without_a_reason(
    session: AsyncSession, decided: ActionRequestStatus, reason: str | None
) -> None:
    """T37, closing what T34 flagged open (C8, V15, V84c).

    V15's 409 lives in the endpoint; this is the same rule at the mechanism, so a *second*
    writer — T38's executor, T39's sweep, an admin script — cannot record a decision nobody
    justified. Whitespace counts as blank, which is the case a `NOT NULL` alone would miss.

    The tab and newline cases are not padding: bare `btrim()` strips *spaces only*, so a
    constraint written that way passes the first three ids and lets a one-tab reason through
    — a blank the endpoint's Python `.strip()` would have refused. Two spellings of "blank"
    is one too many, and the database's is the one a non-endpoint writer is measured against.

    Raw SQL rather than the ORM: the constraint under test is the one the migration built,
    and an application-side guard would answer first and prove nothing about the schema.
    """
    with pytest.raises(IntegrityError):
        await session.execute(
            sa.text(
                "INSERT INTO action_requests"
                " (tool_name, status, approval_context, expires_at, reason, decided_at)"
                " VALUES (:tool, :status, '{}'::jsonb, now(), :reason, now())"
            ),
            {"tool": "whm_suspend_account", "status": decided.value, "reason": reason},
        )
        await session.commit()
    await session.rollback()


async def test_a_decided_row_with_a_reason_is_accepted(session: AsyncSession) -> None:
    """The negative control for the case above (V87).

    A constraint that refused everything would pass every parametrisation there while
    breaking every real approval, and nothing in that test could tell the difference.
    """
    session.add(
        pending_request(
            status=ActionRequestStatus.APPROVED,
            reason="Confirmed with the customer on ticket NOC-4471.",
            decided_at=datetime.now(UTC),
        )
    )
    await session.commit()

    stored = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert stored.status is ActionRequestStatus.APPROVED


@pytest.mark.parametrize(
    "undecided",
    [ActionRequestStatus.PENDING, ActionRequestStatus.EXPIRED],
    ids=lambda s: s.value,
)
async def test_an_undecided_row_may_carry_no_reason(
    session: AsyncSession, undecided: ActionRequestStatus
) -> None:
    """PENDING and EXPIRED sit outside the constraint, deliberately.

    A pending request has not been answered at all, and an expiry is the *absence* of an
    answer — T39's sweep must stay able to write one with `reason IS NULL`. A constraint
    that covered all four statuses would make that sweep impossible to write.
    """
    session.add(
        pending_request(
            status=undecided,
            decided_at=datetime.now(UTC) if undecided is ActionRequestStatus.EXPIRED else None,
        )
    )
    await session.commit()

    stored = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert stored.status is undecided
    assert stored.reason is None


async def test_deleting_the_run_keeps_the_request(session: AsyncSession) -> None:
    """The authorization is not erased by losing the execution it produced."""
    run_id = await insert_run(session)
    session.add(
        pending_request(
            status=ActionRequestStatus.APPROVED,
            reason="Approved.",
            decided_at=datetime.now(UTC),
            tool_run_id=run_id,
        )
    )
    await session.commit()

    await session.execute(sa.delete(ToolRun).where(ToolRun.id == run_id))
    await session.commit()
    session.expire_all()

    surviving = (await session.execute(sa.select(ActionRequest))).scalar_one()

    assert surviving.status is ActionRequestStatus.APPROVED
    assert surviving.tool_run_id is None
