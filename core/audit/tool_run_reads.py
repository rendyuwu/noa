"""Reading the `tool_runs` audit trail for the admin panel (T55 — V45, V47, §I.admin-api).

T35 built the table, T73 wrote to it, and until now nothing could ask about it. V45's last clause
is "queryable in admin audit", so this module is what turns that phrase from prose into a
statement — the shape V69 keeps insisting on.

**Reader beside the writer, split by what it can do.** `core.audit.tool_runs` inserts and
commits; nothing here commits and no statement here is anything but a `SELECT`. Same discipline
as `core.results.tables` and `core.approvals`: the write side is reachable from the MCP tool path,
this side only from behind a session cookie and `require_admin`, and neither can be reached
from the other's side of that line.

**Every filter is in the `WHERE`, and the statements are functions so a test can prove it**
(V93). A filter applied after the fetch is not a filter here — it also breaks paging, because the
`LIMIT` would have already cut the rows that the filter was going to remove, so a page could come
back short (or empty) while `nextCursor` insisted there was more. `test_tool_run_audit_read.py`
compiles these and reads the SQL.

**One order, and the tie-break is part of it**: `created_at DESC, id DESC`. `created_at` is not
unique — two calls in the same millisecond are ordinary on the MCP path — and paging by a
non-unique key splits a tied group differently per call, which is V92(c)'s rule one surface over.
The page asks for `limit + 1` rows and returns `limit` of them; the extra row is how "there is
another page" is decided, and it is never serialised.

**The requester is an outer join.** `tool_runs.requested_by_user_id` is `SET NULL` (T35: an audit
trail a user deletion erases is not one), so an inner join would hide precisely the rows that
outlived their operator. A missing email is `null` on the wire, which reads as "the account is
gone" rather than as "nobody ran this".

**Nothing here redacts, and that is not an omission.** `args` and `result_summary` arrive already
redacted from their writers — `noa_api.mcp_audit.redacted_args` on the READ path and
`core.approvals.decisions.start_change_run(args=locked.redacted_arguments)` on the CHANGE path.
A second redactor under the read would be a quieter second home for one rule and the one an
audit reader would have to trust without seeing it; the same argument `core.audit.tool_runs` makes
about not redacting under the SQL.

**Duration is derived, never stored** (T35's model docstring): `completed_at - created_at` at read
time, so the pair cannot disagree with a third column. `null` while a run is `STARTED`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.cursor import KeysetCursor, encode_cursor, keyset_predicate
from core.clock import as_utc
from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.db.models import ToolRun, User

# Page size bounds, shared with the route's `Query(ge=…, le=…)` so the API and the statement agree
# on one answer. A ceiling at all because a page is loaded into memory and serialised whole — an
# unbounded `limit` is the audit surface's version of the unbounded pass V92(a) forbids.
DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

# LIKE metacharacters in a caller-supplied `requestedByEmail`. Escaped rather than passed through:
# a bare `%` widens the filter to *every* row while the response still says it was filtered, which
# is a wrong answer wearing a right one's clothes. `noa-old` did not escape these.
#
# `LIKE_ESCAPE` is exported because `escape_like` only inserts it — the `ESCAPE '…'` clause that
# tells Postgres what it means is written by the caller, so the two halves live in two modules and
# they have to name the same character. A second private copy next to the second caller was
# the shape this replaced: change one to `!` for a driver quirk and the other still declares `\`,
# so `!%` arrives as a literal `!` beside a live `%` and every address matches while the response
# still reads as filtered.
LIKE_ESCAPE: Final = "\\"
_LIKE_SPECIALS: Final = re.compile(r"([\\%_])")


def escape_like(value: str) -> str:
    """`value` as a literal substring pattern for `ILIKE ... ESCAPE '\\'`.

    A function rather than a `sub` template: a replacement string has its own backslash grammar, so
    the obvious spelling produces a literal `\\1` instead of an escaped metacharacter — and the
    result still *looks* like a pattern, which is why the escaping is asserted on the value and not
    only on the presence of the `ESCAPE` clause (`test_tool_run_audit_read.py`).
    """
    return _LIKE_SPECIALS.sub(lambda match: LIKE_ESCAPE + match.group(1), value)


@dataclass(frozen=True)
class ToolRunAuditFilters:
    """What an admin narrowed the list to (§I.admin-api, V47).

    All optional, and an empty object means "everything" — the panel's first load. Held as one
    frozen value rather than seven parameters threaded through three layers, so the route maps
    query string to filters once and the statement builder reads one thing.

    `tool_name` is an exact match: it is an identifier from `TOOL_CATALOG`, and the panel's search
    box offers the tool names it already rendered. `requested_by_email` is a contains-match,
    because an operator searching an audit trail types a fragment of an address.
    """

    tool_name: str | None = None
    status: ToolRunStatus | None = None
    risk: ToolRisk | None = None
    conversation_ref: str | None = None
    requested_by_email: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


def _duration_ms(created_at: datetime, completed_at: datetime | None) -> int | None:
    """Milliseconds from start to terminal state, or `None` while the run is `STARTED`.

    Clamped at zero: `created_at` is a server default and `completed_at` is stamped by the
    application clock (`core.audit.tool_runs.finish_run`), so on a machine whose clock stepped
    backwards the difference can be negative. A negative duration is a clock fault, not a
    measurement, and rendering it as `-4ms` in an audit view invites a bug hunt in the wrong place.
    """
    if completed_at is None:
        return None
    return max(0, int((as_utc(completed_at) - as_utc(created_at)).total_seconds() * 1000))


@dataclass(frozen=True)
class ToolRunListItem:
    """One run as the audit list renders it.

    Every field V47 names except `args`, which the detail read carries: a list page of fifty runs
    would otherwise ship fifty JSONB payloads to draw five columns.

    `requested_by_email` rather than the user id — the panel shows people, and the id is on the
    detail. `duration_ms` is derived (see `_duration_ms`).
    """

    tool_run_id: UUID
    tool_name: str
    risk: ToolRisk
    status: ToolRunStatus
    conversation_ref: str | None
    requested_by_email: str | None
    result_summary: str | None
    created_at: datetime
    completed_at: datetime | None
    duration_ms: int | None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the HTTP body, camelCase like the rest of the admin surface.

        One spelling of this body: the route's response model is built from this dict rather than
        re-listing the fields, the way `ResultTableView.as_payload()` is T56's single spelling.
        Two hand-maintained copies of one payload is one that can disagree.
        """
        return {
            "toolRunId": str(self.tool_run_id),
            "toolName": self.tool_name,
            "risk": self.risk.value,
            "status": self.status.value,
            "conversationRef": self.conversation_ref,
            "requestedByEmail": self.requested_by_email,
            "resultSummary": self.result_summary,
            "createdAt": self.created_at.isoformat(),
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "durationMs": self.duration_ms,
        }


@dataclass(frozen=True)
class ToolRunDetailView:
    """One run in full: the list item plus the redacted arguments (§I.admin-api).

    Composed rather than subclassed so the extra field is visible at the call site and so the list
    item's payload keys cannot drift from the detail's.
    """

    item: ToolRunListItem
    args: dict[str, Any]
    requested_by_user_id: UUID | None

    def as_payload(self) -> dict[str, Any]:
        """The list item's fields, plus `args` and the requester's id.

        `args` is `{}` for a call that took none — the column's server default says the same thing
        (T35), because "no arguments" and "arguments not recorded" must not read alike in an audit
        view.
        """
        return {
            **self.item.as_payload(),
            "requestedByUserId": (
                str(self.requested_by_user_id) if self.requested_by_user_id else None
            ),
            "args": dict(self.args),
        }


@dataclass(frozen=True)
class ToolRunPage:
    """One page of runs, and the token for the next one — `None` on the last page.

    `next_cursor` is the bound this surface owes (V85's family): a client can tell "that is all of
    them" from "there is more" without counting rows against the limit it asked for.
    """

    items: list[ToolRunListItem]
    next_cursor: str | None


def _apply_filters(statement: Select[Any], filters: ToolRunAuditFilters) -> Select[Any]:
    """Add one predicate per set filter. Every one lands in the `WHERE`."""
    if filters.tool_name:
        statement = statement.where(ToolRun.tool_name == filters.tool_name)
    if filters.status is not None:
        statement = statement.where(ToolRun.status == filters.status)
    if filters.risk is not None:
        statement = statement.where(ToolRun.risk == filters.risk)
    if filters.conversation_ref:
        statement = statement.where(ToolRun.conversation_ref == filters.conversation_ref)
    if filters.requested_by_email:
        statement = statement.where(
            User.email.ilike(f"%{escape_like(filters.requested_by_email)}%", escape=LIKE_ESCAPE)
        )
    if filters.created_from is not None:
        statement = statement.where(ToolRun.created_at >= filters.created_from)
    if filters.created_to is not None:
        statement = statement.where(ToolRun.created_at <= filters.created_to)
    return statement


def select_tool_run_page(
    *,
    filters: ToolRunAuditFilters,
    limit: int,
    cursor: KeysetCursor | None,
) -> Select[Any]:
    """The statement one audit page is read with.

    Built as a function for the reason `select_table_for_requester` is: the claims worth
    asserting — that the filters and the cursor are *in* the statement, and that the order carries
    its tie-break — are claims about the SQL, and a check made after the rows arrive would leave
    every payload test green while being no check at all.

    `limit + 1`: the extra row answers "is there another page" without a second `COUNT` over a
    table that is being appended to while it is read.
    """
    statement = _apply_filters(
        select(ToolRun, User.email.label("requested_by_email")).outerjoin(
            User, User.id == ToolRun.requested_by_user_id
        ),
        filters,
    )

    if cursor is not None:
        statement = statement.where(
            keyset_predicate(
                timestamp_column=ToolRun.created_at,
                id_column=ToolRun.id,
                cursor=cursor,
            )
        )

    return statement.order_by(ToolRun.created_at.desc(), ToolRun.id.desc()).limit(limit + 1)


def select_tool_run(*, tool_run_id: UUID) -> Select[Any]:
    """The statement one run's detail is read with.

    Same outer join, no filters, no cursor and no limit: an id names at most one row, and a
    `LIMIT 1` on a primary-key lookup would be a bound with nothing to bound.
    """
    return (
        select(ToolRun, User.email.label("requested_by_email"))
        .outerjoin(User, User.id == ToolRun.requested_by_user_id)
        .where(ToolRun.id == tool_run_id)
    )


def _to_list_item(run: ToolRun, requested_by_email: str | None) -> ToolRunListItem:
    """One row → one list item. The only place the mapping lives."""
    created_at = as_utc(run.created_at)
    completed_at = as_utc(run.completed_at) if run.completed_at else None
    return ToolRunListItem(
        tool_run_id=run.id,
        tool_name=run.tool_name,
        risk=ToolRisk(run.risk),
        status=ToolRunStatus(run.status),
        conversation_ref=run.conversation_ref,
        requested_by_email=requested_by_email,
        result_summary=run.result_summary,
        created_at=created_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(created_at, completed_at),
    )


class ToolRunAuditReader(Protocol):
    """The two reads this surface may make. No write, and no `commit` to make one with."""

    async def list_runs(
        self,
        *,
        filters: ToolRunAuditFilters,
        limit: int,
        cursor: KeysetCursor | None,
    ) -> tuple[list[ToolRunListItem], bool]: ...

    async def get_run(self, *, tool_run_id: UUID) -> ToolRunDetailView | None: ...


class SQLToolRunAuditReader:
    """`ToolRunAuditReader` over one `AsyncSession`.

    Returns `(items, has_more)` rather than a cursor: encoding one is a decision about the wire,
    and the row a cursor points at is the last item this method just returned — so the service
    above can mint it from the item without this class knowing the token format.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_runs(
        self,
        *,
        filters: ToolRunAuditFilters,
        limit: int,
        cursor: KeysetCursor | None,
    ) -> tuple[list[ToolRunListItem], bool]:
        """One page, and whether the statement saw a row beyond it."""
        result = await self._session.execute(
            select_tool_run_page(filters=filters, limit=limit, cursor=cursor)
        )
        rows = list(result.all())

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        return [_to_list_item(run, email) for run, email in rows], has_more

    async def get_run(self, *, tool_run_id: UUID) -> ToolRunDetailView | None:
        """One run in full, or `None` when no row carries that id."""
        result = await self._session.execute(select_tool_run(tool_run_id=tool_run_id))
        row = result.first()
        if row is None:
            return None

        run, email = row
        return ToolRunDetailView(
            item=_to_list_item(run, email),
            args=dict(run.args or {}),
            requested_by_user_id=run.requested_by_user_id,
        )


class ToolRunAuditService:
    """Query the `tool_runs` audit trail.

    Thin, like `ResultTableService`: it bounds the page size, decides the continuation token, and
    holds a *reader* rather than the writer next door. The bound is enforced here as well as in the
    route's `Query(le=…)` — the route is one caller, and a limit that only a query-string validator
    holds is a limit a future caller (a CLI, a background export) does not have.
    """

    def __init__(self, *, repository: ToolRunAuditReader) -> None:
        self._repository = repository

    async def list_runs(
        self,
        *,
        filters: ToolRunAuditFilters | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: KeysetCursor | None = None,
    ) -> ToolRunPage:
        """One page of runs, newest first, with the token for the next one.

        `next_cursor` is minted from the last item on *this* page — never from the row beyond it,
        which the reader dropped: the cursor means "continue strictly after what you have seen",
        and taking it from the extra row would skip that row on the next page.
        """
        bounded = max(1, min(int(limit), MAX_PAGE_SIZE))
        items, has_more = await self._repository.list_runs(
            filters=filters or ToolRunAuditFilters(),
            limit=bounded,
            cursor=cursor,
        )

        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(
                KeysetCursor(timestamp=last.created_at, entity_id=last.tool_run_id)
            )

        return ToolRunPage(items=items, next_cursor=next_cursor)

    async def run_detail(self, *, tool_run_id: UUID) -> ToolRunDetailView | None:
        """One run with its redacted arguments, or `None`."""
        return await self._repository.get_run(tool_run_id=tool_run_id)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "LIKE_ESCAPE",
    "MAX_PAGE_SIZE",
    "SQLToolRunAuditReader",
    "ToolRunAuditFilters",
    "ToolRunAuditReader",
    "ToolRunAuditService",
    "ToolRunDetailView",
    "ToolRunListItem",
    "ToolRunPage",
    "escape_like",
    "select_tool_run",
    "select_tool_run_page",
]
