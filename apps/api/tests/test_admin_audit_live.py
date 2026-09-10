"""Reading the audit trail against a real Postgres.

`test_admin_audit_routes.py` drives the surface over a double and `test_tool_run_audit_read.py`
reads the compiled statement. **The claim this file exists for is the trail's last clause** — "every
MCP READ writes a `tool_runs` row … *queryable in admin audit*" — and it is a claim about two pieces
of production code agreeing through the database. A double answering it would be the test agreeing
with itself — upstream provenance is no evidence a control works — so every row here is written by
the **real writer** (`core.audit.tool_runs.SQLToolRunRepository`, the tool-run writer) and read by
the **real reader**.

Four more questions are the database's rather than the code's:

- **A deleted operator's run survives, with a NULL requester.** `SET NULL` is a state only a real
  `DELETE` produces, and the outer join is what keeps the row visible — an inner join would hide
  exactly the rows an audit trail is kept for.
- **The page tiles the trail when every timestamp is identical.** Five runs at one `created_at` is
  the case a `created_at`-only cursor gets wrong, and it gets it wrong *silently*.
- **Each filter narrows in SQL** — including the `ILIKE` escape, which only Postgres can settle.
- **The redacted arguments come back as stored.** Redaction happens at the write; this is the
  proof the read adds nothing and removes nothing.

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.audit.cursor import KeysetCursor, decode_cursor
from core.audit.tool_run_reads import (
    SQLToolRunAuditReader,
    ToolRunAuditFilters,
    ToolRunAuditService,
)
from core.audit.tool_runs import SQLToolRunRepository
from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.db.models import ToolRun, User
from core.secrets.redaction import REDACTED
from noa_api.mcp_audit import redacted_args, result_summary
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_admin_audit_test"

OPERATOR_EMAIL = "operator@example.com"
OTHER_EMAIL = "someone-else@example.com"

READ_TOOL = "whm_list_accounts"
CHANGE_TOOL = "whm_suspend_account"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test engine and session, mutated tables emptied first."""
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
            yield opened
    finally:
        await engine.dispose()


async def insert_user(session: AsyncSession, email: str) -> UUID:
    user = User(email=email, ldap_dn=f"CN={email}", display_name="Operator", is_active=True)
    session.add(user)
    await session.commit()
    return user.id


async def record_run(
    session: AsyncSession,
    *,
    requester: UUID,
    tool_name: str = READ_TOOL,
    risk: ToolRisk = ToolRisk.READ,
    conversation_ref: str | None = "conv-1",
    args: dict[str, Any] | None = None,
    status: ToolRunStatus | None = ToolRunStatus.COMPLETED,
    summary: str | None = None,
) -> UUID:
    """One run through the **production** writer — both statements, both commits.

    `status=None` leaves the row `STARTED`, which is what a call still in flight looks like and what
    the reaper sweeps. `args` goes through `noa_api.mcp_audit.redacted_args` rather than straight
    in, so what lands is what the tool path would have written.
    """
    repository = SQLToolRunRepository(session)
    tool_run_id = await repository.start_run(
        tool_name=tool_name,
        requested_by_user_id=requester,
        risk=risk,
        conversation_ref=conversation_ref,
        args=redacted_args(args or {}),
    )
    await repository.commit()

    if status is not None:
        await repository.finish_run(
            tool_run_id=tool_run_id,
            status=status,
            result_summary=summary if summary is not None else result_summary({"ok": True}),
        )
        await repository.commit()

    return tool_run_id


def audit(session: AsyncSession) -> ToolRunAuditService:
    return ToolRunAuditService(repository=SQLToolRunAuditReader(session))


# --- The row the tool path wrote is the row the audit surface finds ---


async def test_a_read_run_written_by_the_tool_path_is_queryable(session: AsyncSession) -> None:
    """The trail, end to end: the tool-run writer, the audit reader, one database, every recorded
    field — requester, tool, status, conversation ref, summary, redacted args, timing.

    The clause this whole task exists for. Until now the row landed and nothing could ask about it,
    so "queryable in admin audit" was prose — and prose is what the inert pin shipped.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    tool_run_id = await record_run(
        session,
        requester=operator,
        args={"limit": 25, "api_token": "whm-secret"},
        summary='{"ok": true, "count": 3}',
    )

    page = await audit(session).list_runs()

    assert [item.tool_run_id for item in page.items] == [tool_run_id]
    item = page.items[0]
    assert item.tool_name == READ_TOOL
    assert item.risk is ToolRisk.READ
    assert item.status is ToolRunStatus.COMPLETED
    assert item.conversation_ref == "conv-1"
    assert item.requested_by_email == OPERATOR_EMAIL
    assert item.result_summary == '{"ok": true, "count": 3}'
    assert item.duration_ms is not None and item.duration_ms >= 0


async def test_a_failed_read_is_queryable(session: AsyncSession) -> None:
    """`risk` and `status` are separate columns, so a failed READ is a row.

    `noa-old` kept `risk` on `action_requests` instead, so its trail could not describe this at all.
    The pair is asserted together — either one alone would pass against a row of the other kind.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    await record_run(
        session,
        requester=operator,
        status=ToolRunStatus.FAILED,
        summary='{"ok": false, "error_code": "timeout"}',
    )

    item = (await audit(session).list_runs()).items[0]

    assert (item.risk, item.status) == (ToolRisk.READ, ToolRunStatus.FAILED)
    assert item.result_summary is not None and "timeout" in item.result_summary


async def test_a_started_run_has_no_completion_or_duration(session: AsyncSession) -> None:
    """A call still in flight: timing half two is NULL in the column and `None` on the view."""
    operator = await insert_user(session, OPERATOR_EMAIL)
    await record_run(session, requester=operator, status=None)

    item = (await audit(session).list_runs()).items[0]

    assert item.status is ToolRunStatus.STARTED
    assert item.completed_at is None
    assert item.duration_ms is None


async def test_a_change_run_is_in_the_same_trail(session: AsyncSession) -> None:
    """A change's row and a read's row are one table, and one list answers for both.

    `risk` is a filter here, never a scope: a surface that answered only READs would leave the
    changes — the rows an operator most wants — visible nowhere.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    await record_run(session, requester=operator, tool_name=CHANGE_TOOL, risk=ToolRisk.CHANGE)
    await record_run(session, requester=operator)

    page = await audit(session).list_runs()

    assert {item.risk for item in page.items} == {ToolRisk.READ, ToolRisk.CHANGE}


async def test_a_deleted_requester_leaves_the_run_with_a_null_email(session: AsyncSession) -> None:
    """`SET NULL`: the row outlives its operator, and the outer join keeps it visible.

    A state only a real `DELETE` produces. An inner join would drop it — and dropping it is worse
    than showing it, because a deleted account is exactly whose actions get asked about.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    tool_run_id = await record_run(session, requester=operator)

    await session.execute(sa.delete(User).where(User.id == operator))
    await session.commit()

    page = await audit(session).list_runs()

    assert [item.tool_run_id for item in page.items] == [tool_run_id]
    assert page.items[0].requested_by_email is None

    detail = await audit(session).run_detail(tool_run_id=tool_run_id)
    assert detail is not None
    assert detail.requested_by_user_id is None


# --- Redaction happened at the write, and the read neither adds nor undoes it ---


async def test_the_surface_serves_the_stored_redacted_args(session: AsyncSession) -> None:
    """The credential is `[REDACTED]` in the column, so it is `[REDACTED]` on the detail.

    Two halves, and both matter: the secret is gone, and the *other* arguments survive intact — a
    read that redacted again could blank the whole payload and still pass a "no secret here" test —
    a compare must still separate. The column is read directly as well, so this cannot pass because
    the reader dropped `args` altogether.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    tool_run_id = await record_run(
        session,
        requester=operator,
        args={"server_ref": "web16", "api_token": "whm-secret-plaintext", "limit": 5},
    )

    stored = await session.execute(sa.select(ToolRun.args).where(ToolRun.id == tool_run_id))
    detail = await audit(session).run_detail(tool_run_id=tool_run_id)

    assert detail is not None
    assert detail.args == stored.scalar_one()
    assert detail.args["api_token"] == REDACTED
    assert detail.args["server_ref"] == "web16"
    assert detail.args["limit"] == 5
    assert "whm-secret-plaintext" not in str(detail.args)


async def test_a_call_with_no_arguments_reads_back_as_an_empty_object(
    session: AsyncSession,
) -> None:
    """`{}`, not `None` — the column's server default and the view agree."""
    operator = await insert_user(session, OPERATOR_EMAIL)
    tool_run_id = await record_run(session, requester=operator, args={})

    detail = await audit(session).run_detail(tool_run_id=tool_run_id)

    assert detail is not None
    assert detail.args == {}


async def test_an_unknown_id_answers_none(session: AsyncSession) -> None:
    """No row, no view. The route turns this into its one 404."""
    assert await audit(session).run_detail(tool_run_id=UUID(int=99)) is None


# --- Paging against real SQL ---


async def stamp(session: AsyncSession, tool_run_id: UUID, created_at: datetime) -> None:
    """Force one run's `created_at`. The column is a server default, so a test that needs a
    controlled ordering — or a deliberate tie — has to write it."""
    await session.execute(
        sa.update(ToolRun).where(ToolRun.id == tool_run_id).values(created_at=created_at)
    )
    await session.commit()


async def test_a_page_walk_yields_every_run_once(session: AsyncSession) -> None:
    """Five runs, `limit=2`, distinct timestamps: 2 + 2 + 1, newest first, no repeats.

    The ordinary case, asserted against Postgres rather than against the double's slicing — the
    `ORDER BY` and the keyset predicate are the database's to combine.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    base = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    ids = []
    for index in range(5):
        tool_run_id = await record_run(session, requester=operator, tool_name=f"tool_{index}")
        await stamp(session, tool_run_id, base + timedelta(minutes=index))
        ids.append(tool_run_id)

    seen: list[UUID] = []
    cursor: KeysetCursor | None = None
    for _ in range(3):
        page = await audit(session).list_runs(limit=2, cursor=cursor)
        seen.extend(item.tool_run_id for item in page.items)
        cursor = decode_cursor(page.next_cursor) if page.next_cursor else None

    assert cursor is None
    assert seen == list(reversed(ids))


async def test_a_tied_timestamp_page_walk_yields_every_row_once(session: AsyncSession) -> None:
    """Five runs sharing one `created_at`, paged two at a time, each seen exactly once.

    This is the case a `created_at`-only cursor gets wrong, and it fails both ways at once — `<`
    skips the rest of the tied group, `<=` serves it again forever. The tie-break is what makes
    the walk terminate *and* be complete, so the assertion is on the multiset of ids, not on the
    count: five distinct ids and five rows seen are different claims.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    tied = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    ids = set()
    for index in range(5):
        tool_run_id = await record_run(session, requester=operator, tool_name=f"tool_{index}")
        await stamp(session, tool_run_id, tied)
        ids.add(tool_run_id)

    seen: list[UUID] = []
    cursor: KeysetCursor | None = None
    for _ in range(4):
        page = await audit(session).list_runs(limit=2, cursor=cursor)
        seen.extend(item.tool_run_id for item in page.items)
        if page.next_cursor is None:
            cursor = None
            break
        cursor = decode_cursor(page.next_cursor)

    assert cursor is None, "the walk did not terminate"
    assert len(seen) == 5
    assert set(seen) == ids


async def test_the_last_page_carries_no_cursor(session: AsyncSession) -> None:
    """Two runs, `limit=5`: one page and `next_cursor is None`.

    The bound rides in the answer, so a client can tell "that is all of them" from "there is more"
    without inferring it from a row count against the limit it asked for.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    await record_run(session, requester=operator)
    await record_run(session, requester=operator)

    page = await audit(session).list_runs(limit=5)

    assert len(page.items) == 2
    assert page.next_cursor is None


async def test_an_empty_trail_answers_an_empty_page(session: AsyncSession) -> None:
    """No runs: no items, no cursor. A cursor minted over nothing would page forever."""
    page = await audit(session).list_runs()

    assert page.items == []
    assert page.next_cursor is None


# --- Filters, in SQL ---


async def test_each_filter_narrows_against_real_sql(session: AsyncSession) -> None:
    """One matching run and one that must not match, per filter.

    A "does it narrow" test with only matching rows passes against a filter that does nothing, so
    each case here carries its own negative row — the run the filter has to exclude.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    other = await insert_user(session, OTHER_EMAIL)

    wanted = await record_run(
        session,
        requester=operator,
        tool_name=READ_TOOL,
        risk=ToolRisk.READ,
        conversation_ref="conv-wanted",
        status=ToolRunStatus.FAILED,
    )
    await record_run(
        session,
        requester=other,
        tool_name=CHANGE_TOOL,
        risk=ToolRisk.CHANGE,
        conversation_ref="conv-other",
        status=ToolRunStatus.COMPLETED,
    )

    cases: tuple[ToolRunAuditFilters, ...] = (
        ToolRunAuditFilters(tool_name=READ_TOOL),
        ToolRunAuditFilters(status=ToolRunStatus.FAILED),
        ToolRunAuditFilters(risk=ToolRisk.READ),
        ToolRunAuditFilters(conversation_ref="conv-wanted"),
        ToolRunAuditFilters(requested_by_email="operator@"),
    )

    for filters in cases:
        page = await audit(session).list_runs(filters=filters)
        assert [item.tool_run_id for item in page.items] == [wanted], filters


async def test_the_date_range_filters_bound_both_ends(session: AsyncSession) -> None:
    """`from`/`to` are inclusive, and each excludes the run on the far side of it.

    The boundary is asserted rather than assumed: a `>` where the code says `>=` drops the run an
    operator filtered *to* the minute of, which reads as "there is nothing there".
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    old = await record_run(session, requester=operator, tool_name="tool_old")
    new = await record_run(session, requester=operator, tool_name="tool_new")
    boundary = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    await stamp(session, old, boundary - timedelta(hours=1))
    await stamp(session, new, boundary)

    from_boundary = await audit(session).list_runs(
        filters=ToolRunAuditFilters(created_from=boundary)
    )
    to_boundary = await audit(session).list_runs(filters=ToolRunAuditFilters(created_to=boundary))

    assert [item.tool_run_id for item in from_boundary.items] == [new]
    assert {item.tool_run_id for item in to_boundary.items} == {old, new}


async def test_a_percent_in_the_email_filter_matches_literally(session: AsyncSession) -> None:
    """`%` is a character, not "everything" — the escape, settled by Postgres.

    Unescaped, this filter answers the whole trail while the response still reads as filtered: a
    wrong answer that looks right, and one nothing errors on. `noa-old` passed the value straight
    into `ilike`. The second half is the control — the escaped pattern still *matches* when the
    address really contains a `%` — because a filter that matched nothing would pass the first half.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    odd = await insert_user(session, "100%_ops@example.com")
    await record_run(session, requester=operator)
    wanted = await record_run(session, requester=odd, tool_name="tool_odd")

    literal = await audit(session).list_runs(
        filters=ToolRunAuditFilters(requested_by_email="100%_ops")
    )
    bare_percent = await audit(session).list_runs(
        filters=ToolRunAuditFilters(requested_by_email="%")
    )

    assert [item.tool_run_id for item in literal.items] == [wanted]
    # A lone `%` asks for addresses *containing a percent sign*, so it finds the odd one and not
    # `operator@example.com`. Unescaped it would be the wildcard and both runs would come back —
    # which is the same body an unfiltered list returns, filtered-looking and wrong.
    assert [item.tool_run_id for item in bare_percent.items] == [wanted]


async def test_filters_and_paging_compose(session: AsyncSession) -> None:
    """A filtered walk pages over the filtered set, not over the table.

    The failure this catches is the missing-field bind: a filter applied after the fetch would let
    `LIMIT` cut the unfiltered rows first, so a page could come back short — or empty — while the
    cursor claimed there was more.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    base = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    wanted = []
    for index in range(4):
        read_run = await record_run(session, requester=operator, tool_name=READ_TOOL)
        await stamp(session, read_run, base + timedelta(minutes=index))
        wanted.append(read_run)
        noise = await record_run(
            session, requester=operator, tool_name=CHANGE_TOOL, risk=ToolRisk.CHANGE
        )
        await stamp(session, noise, base + timedelta(minutes=index, seconds=30))

    filters = ToolRunAuditFilters(tool_name=READ_TOOL)
    seen: list[UUID] = []
    cursor: KeysetCursor | None = None
    for _ in range(2):
        page = await audit(session).list_runs(filters=filters, limit=2, cursor=cursor)
        assert len(page.items) == 2
        seen.extend(item.tool_run_id for item in page.items)
        cursor = decode_cursor(page.next_cursor) if page.next_cursor else None

    assert cursor is None
    assert seen == list(reversed(wanted))
