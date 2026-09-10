"""`tool_runs` table and lifecycle enums.

Two levels, for two different claims:

- **Metadata** — the column set, the enum definitions and the indexes. No DB needed.
- **Live** — that Postgres actually rejects an off-list status, and that deleting the
  requester leaves the audit row standing. Neither is provable from `Base.metadata`:
  SQLAlchemy renders a CHECK constraint whether or not the migration created one, and
  an FK's `ondelete` is a string until a real `DELETE` runs against it. Skipped (never
  failed) when Postgres is unreachable, like every other DB-backed test here.

This file is about the *shape*, not the writer. T73 wired the write into the tool path and
`test_mcp_tool_audit.py` covers it end to end; what is asserted here is that the columns
V45-V47 ask for exist and hold, so the writer cannot quietly reshape them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db import Base
from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.db.models import ToolRun, User
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_tool_runs_schema_test"

TOOL_RUNS = Base.metadata.tables["tool_runs"]

# §T.35's column list, verbatim, plus the second half of "timing". A column added without
# a task to specify it is a guess (`test_schema_v1.py` guards whole tables the same way);
# one removed takes a V47 field with it.
T35_COLUMNS = {
    "id",
    "tool_name",
    "requested_by_user_id",
    "risk",
    "status",
    "conversation_ref",
    "args",
    "result_summary",
    "created_at",
    "completed_at",
}

# V47's field list, named one by one so a failure says which audit field went missing
# rather than only that a set differs.
V47_FIELDS = [
    "requested_by_user_id",
    "tool_name",
    "status",
    "conversation_ref",
    "result_summary",
    "args",
]

# §I.admin-api's audit filters (toolName, status, user, conversationRef, date range) plus
# the ordering T55 pages on. `risk` is deliberately absent: nothing filters by it.
INDEXED_COLUMNS = {
    "tool_name",
    "status",
    "requested_by_user_id",
    "conversation_ref",
    "created_at",
}


# --------------------------------------------------------------------------------------
# Metadata: shape, enums, indexes
# --------------------------------------------------------------------------------------


def test_column_set_is_exactly_what_t35_specifies() -> None:
    """No extra columns, no missing ones.

    Two absences are deliberate and this is what holds them: `error` (a sanitized failure
    code fits `result_summary`, and V19 keeps raw exception text away from the LLM), and
    `action_request_id` (T34 puts `tool_run_id` on `action_requests`; a second FK pointing
    back would be two truths about one link).
    """
    assert set(TOOL_RUNS.c.keys()) == T35_COLUMNS


@pytest.mark.parametrize("field", V47_FIELDS)
def test_v47_names_every_audit_column(field: str) -> None:
    """V47: the tool-run audit record carries each of these."""
    assert field in TOOL_RUNS.c


def test_timing_is_a_pair_of_timestamps() -> None:
    """V47 "timing": start and end. Duration is derived on read, never stored.

    Two columns cannot disagree with each other; a stored duration can disagree with both.
    `completed_at` is nullable because a STARTED run has not got one yet.
    """
    assert TOOL_RUNS.c.created_at.nullable is False
    assert TOOL_RUNS.c.completed_at.nullable is True
    for column in (TOOL_RUNS.c.created_at, TOOL_RUNS.c.completed_at):
        assert isinstance(column.type, sa.DateTime)
        assert column.type.timezone is True


def test_risk_and_status_are_separate_columns_so_a_failed_read_is_representable() -> None:
    """V20: two columns, two enums — not one flat lifecycle set.

    Folded together, `FAILED` and `READ` compete for one cell and a failed READ becomes
    unwritable. `noa-old` kept `risk` on `action_requests` only, so its `tool_runs` could
    not say whether a run was a change without joining a row that need not exist.
    """
    risk, status = TOOL_RUNS.c.risk, TOOL_RUNS.c.status

    assert risk is not status
    assert risk.nullable is False
    assert status.nullable is False

    run = ToolRun(tool_name="whm_list_servers", risk=ToolRisk.READ, status=ToolRunStatus.FAILED)

    assert (run.risk, run.status) == (ToolRisk.READ, ToolRunStatus.FAILED)


def test_change_runs_are_representable() -> None:
    """V46: an approved CHANGE writes here too.

    The receipt half of V46 is T36's `action_receipts` (table built, writer still T38's), and
    the CHANGE row itself is written by the
    post-approval executor rather than by T73's middleware, which records READs only —
    a CHANGE tool's `tools/call` opens the approval gate and executes nothing. This
    table only has to be able to say that a run was a change.
    """
    run = ToolRun(
        tool_name="whm_suspend_account", risk=ToolRisk.CHANGE, status=ToolRunStatus.COMPLETED
    )

    assert run.risk is ToolRisk.CHANGE


def test_lifecycle_enums_are_distinct_and_machine_stable() -> None:
    """V20: this table's two enums, stable values.

    Exact member sets, so a rename is a test failure rather than a silent data change.
    Disjoint values, because a shared member would let a query written against one column
    match rows in the other. V20's third enum landed with T34; the three-way version of
    this assertion lives in `test_action_requests_schema.py`, where all three exist.
    """
    assert {member.name: member.value for member in ToolRisk} == {
        "READ": "READ",
        "CHANGE": "CHANGE",
    }
    assert {member.name: member.value for member in ToolRunStatus} == {
        "STARTED": "STARTED",
        "COMPLETED": "COMPLETED",
        "FAILED": "FAILED",
    }
    assert ToolRisk is not ToolRunStatus
    assert {member.value for member in ToolRisk} & {
        member.value for member in ToolRunStatus
    } == set()


def test_enum_columns_are_checked_varchars_not_native_postgres_enums() -> None:
    """V20 "machine-stable" enforced by the database, not by application discipline.

    `native_enum=False` alone leaves an unconstrained VARCHAR — SQLAlchemy 2.0 defaults
    `create_constraint` to `False`. The CHECK is what makes a renamed member a migration.
    A native enum type would instead need `ALTER TYPE` to grow and would outlive the table
    on downgrade.
    """
    for column, values in (
        (TOOL_RUNS.c.risk, [member.value for member in ToolRisk]),
        (TOOL_RUNS.c.status, [member.value for member in ToolRunStatus]),
    ):
        assert isinstance(column.type, sa.Enum)
        assert column.type.native_enum is False
        assert column.type.create_constraint is True
        assert list(column.type.enums) == values


def test_status_defaults_to_started() -> None:
    """The row is inserted before the tool body runs, so its default has to be truthful.

    A process that dies mid-call then leaves a STARTED row for T38's reaper, rather than
    no evidence at all.
    """
    assert TOOL_RUNS.c.status.server_default.arg == ToolRunStatus.STARTED.value
    assert TOOL_RUNS.c.risk.server_default is None


def test_args_default_to_an_empty_object_never_null() -> None:
    """V45: args are recorded (redacted by the writer — `core.secrets.redaction`, T73).

    `'{}'` rather than NULL so an audit view cannot confuse "took no arguments" with
    "arguments were not recorded".
    """
    args = TOOL_RUNS.c.args

    assert args.nullable is False
    assert "'{}'::jsonb" in str(args.server_default.arg)


def test_result_summary_is_bounded() -> None:
    """V45, V47: "truncated summary".

    An unbounded column invites the whole result body, which V64 deliberately keeps out of
    context and out of this table.
    """
    assert TOOL_RUNS.c.result_summary.type.length == 2000
    assert TOOL_RUNS.c.result_summary.nullable is True


def test_conversation_ref_is_a_nullable_label() -> None:
    """DECISIONS §3.2: an audit/grouping label, never a security scope.

    Nullable because MCP has no thread to guarantee one — C16 dropped threads, and the old
    fail-closed-when-absent rule (old V165) went with the evidence store.
    """
    assert TOOL_RUNS.c.conversation_ref.nullable is True


@pytest.mark.parametrize("column_name", sorted(INDEXED_COLUMNS))
def test_audit_query_columns_are_indexed(column_name: str) -> None:
    """Every §I.admin-api audit filter, plus the column T55's cursor pages on."""
    indexed = {column.name for index in TOOL_RUNS.indexes for column in index.columns}

    assert column_name in indexed


def test_requester_fk_sets_null_rather_than_cascading() -> None:
    """Deliberately unlike every other user FK in schema v1, which cascades.

    An audit trail a user deletion erases is not an audit trail. `RESTRICT` would instead
    make `DELETE /admin/users/{id}` fail the moment a user had run one tool. T73 always
    writes an id; NULL describes life after the subject is deleted.
    """
    (fk,) = TOOL_RUNS.c.requested_by_user_id.foreign_keys

    assert fk.ondelete == "SET NULL"
    assert TOOL_RUNS.c.requested_by_user_id.nullable is True


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


async def insert_user(session: AsyncSession, email: str) -> UUID:
    user = User(email=email, is_active=True)
    session.add(user)
    await session.commit()
    return user.id


async def insert_raw_run(session: AsyncSession, *, risk: str, status: str) -> None:
    """Insert bypassing the ORM, so only the database's own guard can refuse it."""
    await session.execute(
        sa.text("INSERT INTO tool_runs (tool_name, risk, status) VALUES (:tool, :risk, :status)"),
        {"tool": "whm_list_servers", "risk": risk, "status": status},
    )
    await session.commit()


async def test_migration_created_the_check_constraint_on_status(session: AsyncSession) -> None:
    """V20: the database refuses a status the enum does not define.

    Raw SQL on purpose — the ORM's own `validate_strings` would catch it first and prove
    nothing about the schema the migration built.
    """
    with pytest.raises(IntegrityError):
        await insert_raw_run(session, risk="READ", status="RUNNING")


async def test_migration_created_the_check_constraint_on_risk(session: AsyncSession) -> None:
    """V20, same for the risk column — the two constraints are separate."""
    with pytest.raises(IntegrityError):
        await insert_raw_run(session, risk="DELETE", status="STARTED")


async def test_orm_refuses_an_unknown_member_before_the_flush(session: AsyncSession) -> None:
    """`validate_strings=True`: the same mistake caught earlier, in Python."""
    session.add(ToolRun(tool_name="whm_list_servers", risk="READ", status="RUNNING"))

    with pytest.raises((StatementError, LookupError)):
        await session.commit()
    await session.rollback()


async def test_a_failed_read_round_trips(session: AsyncSession) -> None:
    """V20, end to end: risk READ with status FAILED is a row Postgres accepts."""
    user_id = await insert_user(session, "operator@example.com")
    session.add(
        ToolRun(
            tool_name="whm_list_servers",
            requested_by_user_id=user_id,
            risk=ToolRisk.READ,
            status=ToolRunStatus.FAILED,
            conversation_ref="conv-1",
            result_summary="tool_execution_failed",
        )
    )
    await session.commit()

    stored = (await session.execute(sa.select(ToolRun))).scalar_one()

    assert (stored.risk, stored.status) == (ToolRisk.READ, ToolRunStatus.FAILED)
    assert stored.id is not None
    assert stored.created_at is not None
    assert stored.completed_at is None
    # Server-side default, not an ORM-side one: an insert that names no args still records
    # "no arguments" rather than "not recorded".
    assert stored.args == {}


async def test_deleting_the_requester_keeps_the_run(session: AsyncSession) -> None:
    """The audit row outlives its subject; only the requester goes NULL.

    `ondelete` is a string in metadata until a real DELETE runs against it — this is the
    assertion the metadata test above cannot make.
    """
    user_id = await insert_user(session, "leaver@example.com")
    session.add(
        ToolRun(
            tool_name="whm_list_servers",
            requested_by_user_id=user_id,
            risk=ToolRisk.CHANGE,
            status=ToolRunStatus.COMPLETED,
        )
    )
    await session.commit()

    await session.execute(sa.delete(User).where(User.id == user_id))
    await session.commit()
    session.expire_all()

    surviving = (await session.execute(sa.select(ToolRun))).scalar_one()

    assert surviving.tool_name == "whm_list_servers"
    assert surviving.status is ToolRunStatus.COMPLETED
    assert surviving.requested_by_user_id is None


async def test_args_hold_a_redacted_payload(session: AsyncSession) -> None:
    """V45: JSONB round-trips the shape T73 writes (redaction is the writer's, not the
    column's)."""
    redacted = {"server_ref": "whm-1", "ssh_password": "[redacted]", "targets": ["1.2.3.4"]}
    session.add(
        ToolRun(
            tool_name="whm_firewall_release_and_allow",
            risk=ToolRisk.CHANGE,
            status=ToolRunStatus.STARTED,
            args=redacted,
        )
    )
    await session.commit()

    stored = (await session.execute(sa.select(ToolRun))).scalar_one()

    # Nothing here redacts anything; the guarantee is only that there is a place for the
    # redacted form, and that JSONB gives it back unchanged. `noa_api.mcp_audit` owns the
    # redaction, and `test_secret_redaction.py` covers it.
    assert stored.args == redacted
