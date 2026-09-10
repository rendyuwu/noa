"""The statements the admin authorisation trail is read with (§I.admin-api — V85, V92, V93).

Three claims live here because they are claims about **SQL**, and a check made after the rows
arrive would leave every payload test green while being no check at all:

- every filter lands in the `WHERE` (V93). One applied after the fetch also breaks paging, because
  the `LIMIT` would have cut rows the filter was about to remove and the page would come back
  short while `nextCursor` insisted there was more;
- the order carries its tie-break, `created_at DESC, id DESC` (V92(c));
- both joins are outer, so a decision that outlived its operator and a decision that produced no
  receipt both stay visible.

And the fourth, which is the one the route cannot assert about itself: **this reader cannot
write.** Two properties, matching the precedent recorded for the card read's repository — no
`commit`, and no statement that is not a `SELECT` — and both are asserted against a session double
that records everything it is handed, not against the class's attribute list. An attribute check
alone passes against a repository that reaches `session.commit()` directly or holds a session it
can commit through. The third property, that the HTTP surface offers no write to attempt at all,
is `test_admin_action_request_routes.py`'s.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from core.approvals.admin_reads import (
    MAX_PAGE_SIZE,
    ActionRequestAdminFilters,
    ActionRequestAdminReader,
    ActionRequestAdminService,
    SQLActionRequestAdminReader,
    select_action_receipt,
    select_action_request,
    select_action_request_id,
    select_action_request_page,
)
from core.audit.cursor import KeysetCursor
from core.db.lifecycle import ActionRequestStatus

FIXED_NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)

# The two ends of a window, deliberately different instants: a floor and a ceiling asserted with
# one date cannot tell which predicate carried it.
WINDOW_FROM = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 30, 0, 0, 0, tzinfo=UTC)

# One filter object with every field set, so a single compile can be read for all five predicates.
ALL_FILTERS = ActionRequestAdminFilters(
    status=ActionRequestStatus.DENIED,
    tool_name="whm_suspend_account",
    requested_by_email="ops@example.com",
    created_from=WINDOW_FROM,
    created_to=WINDOW_TO,
)

# The column **and the operator** each filter must constrain, read off the compiled `WHERE` rather
# than off the builder's source. The operator is half the claim: `created_from` and `created_to`
# are adjacent, near-identical `if` blocks over one column, so a needle pinning only the value —
# or only the column — stays green with `>=` and `<=` swapped, and an inverted window returns the
# complement of the rows asked for while still reading as a filtered answer.
FILTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("status", "action_requests.status ="),
    ("tool_name", "action_requests.tool_name ="),
    ("requested_by_email", "users.email ILIKE"),
    ("created_from", "action_requests.created_at >="),
    ("created_to", "action_requests.created_at <="),
)

# The reads the surface may make, taken off the Protocol rather than hand-listed, so a method
# added there and not driven below is caught by name (V119). `vars()` rather than
# `__protocol_attrs__`: that attribute arrived in Python 3.12 and this project pins `<3.13` (C1),
# so the attribute lookup would fall back to an empty set and the comparison would pass vacuously
# — the exact defect this binding exists to close.
PROTOCOL_METHODS: frozenset[str] = frozenset(
    name
    for name, value in vars(ActionRequestAdminReader).items()
    if callable(value) and not name.startswith("_")
)


def compiled(statement: Select[Any]) -> str:
    """The statement as Postgres would receive it, literals inlined so a value is greppable."""
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def page_sql(**filter_kwargs: Any) -> str:
    """One filtered page statement, compiled."""
    return compiled(
        select_action_request_page(
            filters=ActionRequestAdminFilters(**filter_kwargs), limit=10, cursor=None
        )
    )


def where_clause(sql: str) -> str:
    """Everything after `WHERE`. Asserting on the whole statement would let an `ON` clause pass."""
    parts = sql.split("WHERE", 1)
    assert len(parts) == 2, sql
    return parts[1]


def predicate_for(where: str, fragment: str) -> str:
    """The single `AND`-joined predicate carrying `fragment`.

    Exactly one, asserted: two `if` blocks copy-pasted into the same operator would otherwise
    leave the walk below green while one end of the window had quietly become a second copy of
    the other.
    """
    matches = [part.strip() for part in where.split(" AND ") if fragment in part]
    assert len(matches) == 1, f"`{fragment}` appears {len(matches)} times in: {where}"
    return matches[0]


# --- V93: every filter is in the WHERE, on its own column, with its own operator ---


def test_every_filter_lands_in_the_where() -> None:
    """Five filters, five predicates, all inside the statement and none of them after the fetch.

    The length assertion is what binds the table to the code: a sixth field added to
    `ActionRequestAdminFilters` with no predicate behind it ships a filter that reads as filtering
    and does nothing, and a hand-listed table of cases would answer the whole trail without
    noticing. Read off the `WHERE` tail rather than the whole statement, because a fragment
    matched inside an `ON` clause would pass while narrowing nothing.
    """
    assert len(FILTER_COLUMNS) == len(ActionRequestAdminFilters.__dataclass_fields__)

    where = where_clause(
        compiled(select_action_request_page(filters=ALL_FILTERS, limit=10, cursor=None))
    )

    for field_name, fragment in FILTER_COLUMNS:
        assert fragment in where, f"`{field_name}` is not in the WHERE: {where}"


def test_the_window_ends_are_a_floor_and_a_ceiling_and_not_two_of_one() -> None:
    """`created_from` is `>=` and `created_to` is `<=`, each carrying its own end of the window.

    The pair the walk above cannot separate. Both predicates name one column and sit in adjacent,
    near-identical `if` blocks, so reordering or copy-pasting them survives a check that only
    looks for the column or only looks for the value. Inverted, `?from=2026-09-01` answers with
    everything created *before* that date — plausible rows, newest first, with a working
    `nextCursor`, and nothing in the response saying the window turned inside out. Asserted with
    two different instants for that reason: one date could not say which predicate carried it.
    """
    where = where_clause(
        compiled(select_action_request_page(filters=ALL_FILTERS, limit=10, cursor=None))
    )

    floor = predicate_for(where, "action_requests.created_at >=")
    ceiling = predicate_for(where, "action_requests.created_at <=")

    assert WINDOW_FROM.date().isoformat() in floor, floor
    assert WINDOW_TO.date().isoformat() in ceiling, ceiling


def test_an_empty_filter_object_adds_no_predicate() -> None:
    """The control: without it, the walk above would pass against a statement that always
    filtered on something. The default answer is the whole trail — only the ordering and the
    limit are present, and not one of the five predicates is anywhere in the SQL."""
    sql = page_sql()
    assert "WHERE" not in sql
    for field_name, fragment in FILTER_COLUMNS:
        assert fragment not in sql, f"`{field_name}` narrows an unfiltered read: {sql}"


def test_the_email_filter_escapes_its_like_metacharacters() -> None:
    """A bare `%` must not widen the filter to the whole trail while the response reads as filtered.

    The escaping itself is `core.audit.tool_run_reads.escape_like`, shared rather than written
    twice (V66); what is asserted here is that this statement uses it. `test_tool_run_audit_read.py`
    owns the escaper's own behaviour and the live file settles it against Postgres.
    """
    sql = page_sql(requested_by_email="a%b")
    assert "ESCAPE" in sql
    # The compiler renders a literal `%` doubled for pyformat, so the escaped metacharacter reads
    # as `\\%%` here. Asserted on the rendered form rather than on the raw one, because the
    # rendered form is what Postgres receives — the unescaped spelling would be `%%a%%b%%`, which
    # matches every address in the table.
    assert r"a\\%%b" in sql
    assert "%%a%%b%%" not in sql


# --- V92(c): one order, and the tie-break is part of it ---


def test_the_page_orders_by_created_at_then_id() -> None:
    """`created_at DESC, id DESC`. `created_at` alone is not unique — a burst of gate insertions
    in one millisecond is ordinary — and paging by a non-unique key splits a tied group
    differently per call."""
    sql = page_sql()
    order = sql[sql.index("ORDER BY") :]
    assert "created_at DESC" in order
    assert order.index("created_at DESC") < order.index("action_requests.id DESC")


def test_the_page_asks_for_one_row_beyond_the_limit() -> None:
    """`limit + 1` is how "there is another page" is decided, without a second `COUNT` over a
    table being appended to while it is read."""
    sql = compiled(
        select_action_request_page(filters=ActionRequestAdminFilters(), limit=25, cursor=None)
    )
    assert "LIMIT 26" in sql


def test_the_cursor_predicate_is_in_the_statement() -> None:
    """A cursor narrows in SQL, not after the fetch — the same reason the filters do."""
    cursor = KeysetCursor(timestamp=FIXED_NOW, entity_id=uuid4())
    sql = compiled(
        select_action_request_page(filters=ActionRequestAdminFilters(), limit=10, cursor=cursor)
    )
    assert str(cursor.entity_id) in sql
    assert "2026-09-09 12:00:00" in sql


# --- Both joins are outer ---


def test_both_joins_are_outer_on_every_statement() -> None:
    """A decision outliving its operator (`SET NULL`, T34) and a decision with no receipt must
    both stay visible. An inner join to `users` would hide exactly the rows an audit trail is kept
    for; an inner join to `action_receipts` would hide every deny and every expiry."""
    for sql in (page_sql(), compiled(select_action_request(action_request_id=uuid4()))):
        assert sql.count("LEFT OUTER JOIN") == 2
        assert "JOIN users" in sql
        assert "JOIN action_receipts" in sql


def test_the_existence_read_loads_one_column_and_joins_nothing() -> None:
    """The receipt route's presence check is presence-sized.

    Its only caller tests the answer for `None`, so anything the statement carries beyond the id
    is loaded to be discarded — and the detail statement it replaced carries `approval_context`,
    the largest JSONB on this path, over two outer joins. Asserted on the SQL rather than on a
    timing, because "it is cheaper" is not a claim a test can settle and "it selects one column
    and joins nothing" is.
    """
    request_id = uuid4()
    sql = compiled(select_action_request_id(action_request_id=request_id))

    assert sql.split("FROM", 1)[0].split() == ["SELECT", "action_requests.id"], sql
    assert "JOIN" not in sql
    assert "approval_context" not in sql
    assert str(request_id) in where_clause(sql)


def test_the_receipt_lookup_goes_by_the_request_id() -> None:
    """The panel navigates from a decision to what it did, so the request id is the only id it
    holds — and `UNIQUE (action_request_id)` makes that lookup as precise as a primary key."""
    request_id = uuid4()
    sql = compiled(select_action_receipt(action_request_id=request_id))
    assert "action_receipts.action_request_id" in sql
    assert str(request_id) in sql


# --- The reader cannot write ---


class RecordingSession:
    """An `AsyncSession` stand-in that records what it was asked to do and answers nothing.

    Not a `MagicMock`: the point is to be able to say afterwards *which* statements were issued and
    that `commit` was never among the calls, and a mock that auto-creates attributes would make
    "was `commit` called" answerable only for the spelling the test happened to check.
    """

    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.other_calls: list[str] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        self.statements.append(statement)
        return _EmptyResult()

    def __getattr__(self, name: str) -> Any:
        # Anything the reader reaches for that is not `execute` is recorded by name and then
        # fails, so a `commit()` added later is caught here rather than being silently absorbed.
        self.other_calls.append(name)
        raise AttributeError(name)


class _EmptyResult:
    """What `execute` answers: no rows, by every access shape the reader uses."""

    def all(self) -> list[Any]:
        return []

    def first(self) -> None:
        return None

    def scalars(self) -> _EmptyResult:
        return self


# The reader methods `drive_every_read` exercises, one service call each. Named here so the
# walk can be compared against the Protocol rather than against itself.
DRIVEN_READS: frozenset[str] = frozenset(
    {"list_requests", "get_request", "get_receipt", "request_exists"}
)


async def drive_every_read(session: RecordingSession) -> None:
    """Every read the service can make, over one recording session.

    One call per `ActionRequestAdminReader` method, and the test below holds that correspondence
    to the Protocol. A read left out of this walk is outside both read-only properties, so it
    could reach `session.commit()` without the instrument that exists to see one ever looking.
    """
    service = ActionRequestAdminService(repository=SQLActionRequestAdminReader(session))  # type: ignore[arg-type]
    await service.list_requests(filters=ActionRequestAdminFilters(), limit=10)
    await service.request_detail(action_request_id=uuid4())
    await service.receipt_detail(action_request_id=uuid4())
    await service.request_exists(action_request_id=uuid4())


def test_the_walk_covers_every_read_on_the_protocol() -> None:
    """The two properties below are only as wide as the walk, so the walk is bound to the reader.

    Without this the `SELECT`-only and never-`commit` claims would be claims about whichever
    methods a walk happened to list. A fourth read added to `ActionRequestAdminReader` and not
    driven fails on this line, by name, rather than shipping outside both instruments.
    """
    assert PROTOCOL_METHODS == DRIVEN_READS


@pytest.mark.anyio
async def test_the_reader_issues_only_selects() -> None:
    """Property one of two: every statement the reader issues is a `SELECT`.

    Asserted on the objects handed to `execute`, so an `insert()`, an `update()` or a raw `text()`
    fails here regardless of how it was spelled at the call site.

    The count is taken from the Protocol, not written as a number: one statement per read, so a
    second statement appearing on an existing path fails here as well, and the figure cannot
    silently stop matching the number of reads there are.
    """
    session = RecordingSession()
    await drive_every_read(session)

    assert len(session.statements) == len(PROTOCOL_METHODS)
    for statement in session.statements:
        assert isinstance(statement, Select), f"not a SELECT: {statement!r}"
        assert compiled(statement).lstrip().startswith("SELECT")


@pytest.mark.anyio
async def test_the_reader_never_commits() -> None:
    """Property two of two: no `commit`, and nothing else on the session either.

    The recording session raises for every attribute but `execute`, so this is a claim about what
    the reader *reached for*, not about what a class happens to declare. A repository that called
    `session.commit()` — directly, or through a helper holding the same session — lands in
    `other_calls` and reddens this line.
    """
    session = RecordingSession()
    await drive_every_read(session)

    assert "commit" not in session.other_calls
    assert session.other_calls == []


@pytest.mark.anyio
async def test_the_recording_session_would_catch_a_commit() -> None:
    """The negative control for the test above.

    An assertion nobody watched fail is not evidence (V119). `commit` is reached for here on the
    same object the reader was handed, and it is recorded — so the green above is green because
    the reader did not call it, not because the instrument cannot see one.
    """
    session = RecordingSession()
    with pytest.raises(AttributeError):
        session.commit  # noqa: B018

    assert session.other_calls == ["commit"]


# --- The service's own bound ---


@pytest.mark.anyio
async def test_the_service_clamps_the_page_size() -> None:
    """The ceiling holds here as well as at the route's `Query(le=…)`.

    The route is one caller; a bound only a query-string validator holds is one a CLI or a
    background export does not have.
    """
    session = RecordingSession()
    service = ActionRequestAdminService(repository=SQLActionRequestAdminReader(session))  # type: ignore[arg-type]

    await service.list_requests(limit=MAX_PAGE_SIZE * 10)
    assert f"LIMIT {MAX_PAGE_SIZE + 1}" in compiled(session.statements[0])

    session.statements.clear()
    await service.list_requests(limit=0)
    assert "LIMIT 2" in compiled(session.statements[0])
