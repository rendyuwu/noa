"""What the table surface's reader actually asks the database for.

V93 is the rule this file exists for: a separation held by a check the caller makes *after*
the read is a separation held by nothing much. The row would be loaded — in front of the
logger, the next edit and a refactor that widens the view — and every payload assertion in
`test_result_table_routes.py` would stay green while it was.

So the compiled SQL is the assertion. No database: a `Select` compiles without a connection,
and what is claimed here is about the statement NOA builds, not about what Postgres does with
it — `test_result_tables_live.py` owns the second question.

Three predicates, one `WHERE`: the token names the row, the requester decides whether it may
be seen, and the deadline decides whether it still exists. All three checked here,
because a reader that fetched first and filtered afterwards is exactly what would pass every
other test in the suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from core.results.tables import SQLToolResultTableReader

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class _NoRows:
    """A result with nothing in it. The reader answers `None` and stops."""

    def scalar_one_or_none(self) -> None:
        return None


class RecordingSession:
    """An `AsyncSession` stand-in that keeps the statement instead of running it."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _NoRows:
        self.statements.append(statement)
        return _NoRows()


async def read_sql() -> str:
    """The one statement the reader issues, as PostgreSQL SQL.

    Named dialect rather than the default: this is the text the database NOA ships
    would see.
    """
    session = RecordingSession()
    await SQLToolResultTableReader(cast("AsyncSession", session)).get_for_requester(
        token="table-token-1",
        requester_user_id=uuid4(),
        now=NOW,
    )

    assert len(session.statements) == 1
    return str(session.statements[0].compile(dialect=postgresql.dialect()))


def where_clause(sql: str) -> str:
    head, _, tail = sql.partition("WHERE")
    assert tail, sql
    assert head, sql
    return tail


async def test_the_read_carries_the_requester_match_in_the_where() -> None:
    """V27, V93: a table that is not the caller's is never fetched.

    In the statement, not in a branch after it — the difference V93 measured at T42(b), where
    adding a join to a reader left the whole suite green because the projection had nowhere to
    put the row it had already loaded.
    """
    assert "requested_by_user_id" in where_clause(await read_sql())


async def test_the_read_judges_the_deadline_in_the_same_statement() -> None:
    """An expired table is not fetched and then hidden; it is not fetched.

    Same `WHERE` as the requester-match, so "expired" and "not yours" are one refusal built
    one way rather than two conditions that could drift apart.
    """
    assert "expires_at" in where_clause(await read_sql())


async def test_the_read_names_the_token() -> None:
    """The control that proves the two above are not passing on an empty clause.

    Without it, a `WHERE` containing the word `requested_by_user_id` in some other position
    would satisfy this file just as well as the real predicate does.
    """
    assert "token" in where_clause(await read_sql())


async def test_the_read_touches_one_table_and_joins_nothing() -> None:
    """A parked table is self-contained: rows, counts and deadline are all columns on it.

    Asserted because the alternative is easy to reach for — joining `users` for a display name
    or `tool_runs` for timing would put a second table behind an operator-facing surface, and
    a join is also a second place for a `WHERE` to be got wrong.
    """
    sql = await read_sql()

    assert "JOIN" not in sql.upper()
    assert "tool_result_tables" in sql
