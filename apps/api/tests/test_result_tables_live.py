"""Parking a table and reading it back, against a real Postgres.

`test_result_table_store.py` drives the writer over a double and `test_result_table_routes.py`
drives the surface over another, which between them prove the cap, the redaction and the one
refusal. Four claims here are claims *about the database*, and a double answering them would
be agreeing with the test rather than with the code:

- **A foreign token is not fetched.** The requester-match lives in the `WHERE`
  (`test_result_table_read.py` asserts it is *in* the statement); this asserts Postgres agrees.
- **A deleted requester's table is refused.** The FK is `SET NULL`, so deleting an operator
  leaves a row with a NULL owner — a state only a real `DELETE` produces, and one the match
  has to fail closed on, because `NULL = :caller` is NULL and NULL is not true.
- **The deadline is judged by the same statement**, including the boundary: a table one second
  from expiring still reads, and one a second past it does not.
- **The writer and the reader describe one row.** JSONB in, ordered columns and rows out, with
  the stored bound intact.

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db.models import ToolResultTable, User
from core.results.tables import (
    ResultTableService,
    SQLToolResultTableReader,
    SQLToolResultTableWriter,
    TableColumn,
    park_result_table,
)
from core.secrets.redaction import REDACTED
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_result_tables_test"

OPERATOR_EMAIL = "operator@example.com"
OTHER_EMAIL = "someone-else@example.com"

READ_TOOL = "whm_list_accounts"

COLUMNS = [
    TableColumn(key="user", label="Account"),
    TableColumn(key="domain", label="Primary domain"),
]

TTL_SECONDS = 3600


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


def rows(count: int) -> list[dict[str, object]]:
    return [
        {"user": f"account-{index:03d}", "domain": f"{index}.example"} for index in range(count)
    ]


async def park(
    session: AsyncSession,
    *,
    requester: UUID,
    row_count: int = 2,
    max_rows: int = 25,
    ttl_seconds: int = TTL_SECONDS,
    payload: list[dict[str, object]] | None = None,
) -> str:
    """Park one table through the production writer; return its token."""
    parked = await park_result_table(
        SQLToolResultTableWriter(session),
        tool_name=READ_TOOL,
        requested_by_user_id=requester,
        columns=COLUMNS,
        rows=rows(row_count) if payload is None else payload,
        max_rows=max_rows,
        ttl_seconds=ttl_seconds,
    )
    return parked.token


def service(session: AsyncSession) -> ResultTableService:
    return ResultTableService(repository=SQLToolResultTableReader(session))


async def test_a_parked_table_reads_back_for_its_requester(session: AsyncSession) -> None:
    """The round trip: JSONB in, ordered columns and rows out, bound intact."""
    operator = await insert_user(session, OPERATOR_EMAIL)
    token = await park(session, requester=operator, row_count=3)

    view = await service(session).table_for(token=token, requester_user_id=operator)

    assert view is not None
    assert view.tool_name == READ_TOOL
    assert [column.key for column in view.columns] == ["user", "domain"]
    assert [row["user"] for row in view.rows] == ["account-000", "account-001", "account-002"]
    assert view.total_rows == 3
    assert view.truncated is False


async def test_a_capped_table_stores_the_count_before_the_cut(session: AsyncSession) -> None:
    """The cap's own bound, through Postgres: the total is a column, not the length of what
    came back."""
    operator = await insert_user(session, OPERATOR_EMAIL)
    token = await park(session, requester=operator, row_count=40, max_rows=25)

    view = await service(session).table_for(token=token, requester_user_id=operator)

    assert view is not None
    assert view.stored_rows == 25
    assert view.total_rows == 40
    assert view.truncated is True


async def test_a_credential_never_reaches_the_column(session: AsyncSession) -> None:
    """The envelope shape's discipline reaches the table too: redaction happens on the way in, so
    the stored row is the redacted one.

    Read straight off the table rather than through the view — what matters is what the
    database holds, because that row outlives the call and is what a later reader, a logger or
    a backup sees (a per-writer exemption from one redaction rule, one writer over).
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    token = await park(
        session,
        requester=operator,
        payload=[{"user": "acmeco", "ssh_password": "hunter2"}],
    )

    stored = await session.scalar(sa.select(ToolResultTable).where(ToolResultTable.token == token))
    assert stored is not None
    assert stored.rows == [{"user": "acmeco", "ssh_password": REDACTED}]


async def test_another_operators_table_is_not_fetched(session: AsyncSession) -> None:
    """The requester-match, as Postgres evaluates it."""
    operator = await insert_user(session, OPERATOR_EMAIL)
    stranger = await insert_user(session, OTHER_EMAIL)
    token = await park(session, requester=stranger)

    assert await service(session).table_for(token=token, requester_user_id=operator) is None


async def test_a_table_whose_requester_was_deleted_matches_nobody(session: AsyncSession) -> None:
    """`SET NULL` plus SQL's NULL semantics — the fail-closed direction, proven by a `DELETE`.

    Both halves asserted: the row is still there (deleting an operator does not erase what NOA
    did) and it answers `None` for the id it used to belong to.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    token = await park(session, requester=operator)

    await session.execute(sa.delete(User).where(User.id == operator))
    await session.commit()

    surviving = await session.scalar(
        sa.select(ToolResultTable).where(ToolResultTable.token == token)
    )
    assert surviving is not None
    assert surviving.requested_by_user_id is None
    assert await service(session).table_for(token=token, requester_user_id=operator) is None


async def test_an_unknown_token_answers_the_same_nothing(session: AsyncSession) -> None:
    """One refusal for the whole family, at the statement rather than at the route."""
    operator = await insert_user(session, OPERATOR_EMAIL)
    await park(session, requester=operator)

    assert await service(session).table_for(token="no-such-token", requester_user_id=operator) is (
        None
    )


async def test_a_table_past_its_deadline_is_not_fetched(session: AsyncSession) -> None:
    """The lifetime is part of the same `WHERE` — an expired table is not fetched at all."""
    operator = await insert_user(session, OPERATOR_EMAIL)
    token = await park(session, requester=operator, ttl_seconds=60)

    later = datetime.now(UTC) + timedelta(seconds=61)
    assert (
        await service(session).table_for(
            token=token,
            requester_user_id=operator,
            now=later,
        )
        is None
    )


async def test_a_table_a_second_short_of_its_deadline_still_reads(session: AsyncSession) -> None:
    """The boundary, and the negative control for the test above.

    Without it, "an expired table is refused" passes just as well against a reader that
    refuses everything — which is exactly what a `WHERE` with the comparison inverted would
    produce.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    token = await park(session, requester=operator, ttl_seconds=60)

    just_before = datetime.now(UTC) + timedelta(seconds=59)
    view = await service(session).table_for(
        token=token,
        requester_user_id=operator,
        now=just_before,
    )

    assert view is not None
    assert view.token == token
