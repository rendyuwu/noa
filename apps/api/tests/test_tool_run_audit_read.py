"""What the audit reader actually asks the database for.

`test_admin_audit_routes.py` proves the query string reaches a `ToolRunAuditFilters`, and
`test_admin_audit_live.py` proves Postgres narrows on it. Neither can see the thing in between:
**whether the predicate is in the statement or applied after the rows arrive.**

That distinction is not cosmetic here. A filter applied in Python after the fetch would leave every
payload assertion green (the fetch-bound-separation rule, measured one reader over at the
receipt-join flag) *and* would quietly break paging:
`LIMIT` runs in the database, so it would have already cut rows the filter was about to remove, and
a page could come back short — or empty — while `nextCursor` insisted there was more. Same for the
cursor: a keyset predicate applied outside the statement is a `LIMIT` over the wrong window.

So the compiled SQL is the assertion. No database: a `Select` compiles without a connection, and
what is claimed here is about the statement NOA builds, not about what Postgres does with it, which
is the live file's question.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from core.audit.cursor import KeysetCursor
from core.audit.tool_run_reads import (
    ToolRunAuditFilters,
    escape_like,
    select_tool_run,
    select_tool_run_page,
)
from core.db.lifecycle import ToolRisk, ToolRunStatus

# One filter object with every field set, so a single compile can be read for all seven predicates.
ALL_FILTERS = ToolRunAuditFilters(
    tool_name="whm_list_accounts",
    status=ToolRunStatus.FAILED,
    risk=ToolRisk.CHANGE,
    conversation_ref="conv-77",
    requested_by_email="ops@",
    created_from=datetime(2026, 8, 1, tzinfo=UTC),
    created_to=datetime(2026, 8, 31, tzinfo=UTC),
)

# The column each filter must constrain. Read off the compiled `WHERE`, not off the builder's
# source, so a predicate moved into a Python `if` after the fetch fails here.
FILTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tool_name", "tool_runs.tool_name ="),
    ("status", "tool_runs.status ="),
    ("risk", "tool_runs.risk ="),
    ("conversation_ref", "tool_runs.conversation_ref ="),
    ("requested_by_email", "users.email ILIKE"),
    ("created_from", "tool_runs.created_at >="),
    ("created_to", "tool_runs.created_at <="),
)


def compiled(statement: object) -> str:
    """`statement` as PostgreSQL SQL.

    Named dialect rather than the default, for `test_requester_matched_read.py`'s reason: the joins
    and the `ILIKE` are what is being read, and compiling against the dialect NOA ships means
    this is the text the database would see.
    """
    return str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def where_clause(sql: str) -> str:
    """Everything after `WHERE`. Asserting on the whole statement would let an `ON` clause pass."""
    parts = sql.split("WHERE", 1)
    assert len(parts) == 2, sql
    return parts[1]


def test_every_filter_lands_in_the_where() -> None:
    """The fetch-bound-separation rule: seven filters, seven predicates, all inside the statement.

    The count is asserted as well, so a filter added to `ToolRunAuditFilters` without a predicate —
    the shape that reads as "filtering" and does nothing — fails here rather than answering the
    whole trail.
    """
    assert len(FILTER_COLUMNS) == len(ALL_FILTERS.__dataclass_fields__)

    where = where_clause(compiled(select_tool_run_page(filters=ALL_FILTERS, limit=50, cursor=None)))

    for field_name, fragment in FILTER_COLUMNS:
        assert fragment in where, f"`{field_name}` is not in the WHERE: {where}"


def test_an_unfiltered_read_carries_no_predicates() -> None:
    """The negative control: with no filters, there is no `WHERE` at all.

    Without this, the walk above passes against a builder that pinned every predicate
    unconditionally — which would filter a first page down to nothing while looking correct.
    """
    sql = compiled(select_tool_run_page(filters=ToolRunAuditFilters(), limit=50, cursor=None))

    assert "WHERE" not in sql
    for _, fragment in FILTER_COLUMNS:
        assert fragment not in sql


def test_the_cursor_predicate_is_in_the_statement() -> None:
    """The keyset comparison is SQL, not a Python slice after the fetch.

    Both branches asserted: the timestamp step *and* the id tie-break, because a predicate that
    kept only the first would page correctly until two runs shared a millisecond and then repeat or
    drop the rest of that group.
    """
    cursor = KeysetCursor(timestamp=datetime(2026, 8, 19, tzinfo=UTC), entity_id=uuid4())

    where = where_clause(
        compiled(select_tool_run_page(filters=ToolRunAuditFilters(), limit=50, cursor=cursor))
    )

    assert "tool_runs.created_at <" in where
    assert "tool_runs.created_at =" in where
    assert "tool_runs.id <" in where


def test_the_page_is_ordered_by_created_at_then_id() -> None:
    """The cut is ordered by a uniqueness-completing key before it happens (the capped-read
    ordering rule and the background-limit rule).

    `created_at` alone is not unique on the MCP path — two calls in one millisecond are ordinary —
    and paging by a non-unique key splits a tied group differently per call, so a run can be served
    twice or never. Both keys DESC, so the tie-break agrees with the cursor predicate above; they
    disagreeing is how a page silently skips.
    """
    sql = compiled(select_tool_run_page(filters=ToolRunAuditFilters(), limit=50, cursor=None))

    order_by = sql.split("ORDER BY", 1)[1]
    assert "tool_runs.created_at DESC" in order_by
    assert "tool_runs.id DESC" in order_by
    assert order_by.index("created_at") < order_by.index("tool_runs.id")


def test_the_page_asks_for_one_row_beyond_the_limit() -> None:
    """`LIMIT limit + 1`: the extra row is how "there is another page" is decided.

    Asserted as a parameter value rather than a literal in the SQL, because SQLAlchemy binds it.
    A statement that asked for exactly `limit` would need a second `COUNT` over a table that is
    being appended to while it is read — two answers to one question, taken at two moments.
    """
    statement = select_tool_run_page(filters=ToolRunAuditFilters(), limit=25, cursor=None)

    assert statement.compile(dialect=postgresql.dialect()).params["param_1"] == 26


def test_the_requester_join_is_an_outer_join_on_both_reads() -> None:
    """`SET NULL`: an inner join would hide every run whose operator was deleted.

    Those rows are precisely what an audit trail is for — the reason that FK is not a cascade — so
    the join is asserted on both statements rather than on the one a test happened to exercise.
    """
    for statement in (
        select_tool_run_page(filters=ToolRunAuditFilters(), limit=50, cursor=None),
        select_tool_run(tool_run_id=uuid4()),
    ):
        sql = compiled(statement)
        assert "LEFT OUTER JOIN users" in sql


def test_the_detail_read_is_a_bare_id_lookup() -> None:
    """One id, one row: no filters, no cursor, no `LIMIT`.

    A `LIMIT 1` on a primary-key lookup is a bound with nothing to bound, and a filter here would
    mean a run could be *hidden* from a detail read that its own list page had just offered.
    """
    sql = compiled(select_tool_run(tool_run_id=uuid4()))

    assert "tool_runs.id =" in where_clause(sql)
    assert "LIMIT" not in sql
    assert "ORDER BY" not in sql


def test_the_email_filter_escapes_like_metacharacters() -> None:
    """A `%` in `requestedByEmail` is a literal, not "match everything".

    Unescaped, a single `%` widens the filter to the whole trail while the response still reads as
    filtered — a wrong answer wearing a right one's clothes, and the harder kind to notice because
    nothing errors. `noa-old` passed the value through. The `ESCAPE` clause is asserted in the SQL
    as well as the escaping in the value: one without the other does nothing.
    """
    assert escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"

    statement = select_tool_run_page(
        filters=ToolRunAuditFilters(requested_by_email="100%_ops"),
        limit=50,
        cursor=None,
    )
    compiled_statement = statement.compile(dialect=postgresql.dialect())

    assert "ESCAPE" in str(compiled_statement)
    assert "%100\\%\\_ops%" in compiled_statement.params.values()
