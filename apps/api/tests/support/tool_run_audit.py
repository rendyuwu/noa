"""In-memory `tool_runs` reader for the audit route tests.

The double holds `ToolRunListItem`s and answers the two reads the surface makes, so a route test
can assert on status codes, on the payload shape and — the part a live database makes awkward — on
**exactly which filters the route handed down**. `calls` records every `ToolRunAuditFilters` it was
given, which is how `test_admin_audit_routes.py` proves the query string maps to the filter object
rather than to nothing.

What the double deliberately does **not** do is apply those filters or the keyset predicate. Both
live in the SQL (`core.audit.tool_run_reads.select_tool_run_page`), so a Python re-implementation
here would be a second, more forgiving answer to the question the statement already answers — and a
test that passed against it would be agreeing with the double. `test_tool_run_audit_read.py` reads
the compiled statement and `test_admin_audit_live.py` runs it against Postgres; between them the
filtering is asserted where it lives.

Paging *is* modelled, because it is the service's decision rather than the SQL's: the reader
contract is "return at most `limit` items and say whether there was another" (see
`SQLToolRunAuditReader.list_runs`), and `ToolRunAuditService` mints the cursor from that. So the
double slices its list and reports `has_more`, which lets a route test walk pages without a
database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from core.audit.cursor import KeysetCursor
from core.audit.tool_run_reads import (
    ToolRunAuditFilters,
    ToolRunDetailView,
    ToolRunListItem,
)
from core.db.lifecycle import ToolRisk, ToolRunStatus

# A fixed instant so timing assertions are about the code's arithmetic and not about the clock
#: every derived `durationMs` in these tests is computable by hand from here.
RUN_CREATED_AT = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)


def build_list_item(
    *,
    tool_run_id: UUID | None = None,
    tool_name: str = "whm_list_accounts",
    risk: ToolRisk = ToolRisk.READ,
    status: ToolRunStatus = ToolRunStatus.COMPLETED,
    conversation_ref: str | None = "conv-1",
    requested_by_email: str | None = "operator@example.com",
    result_summary: str | None = '{"ok": true}',
    created_at: datetime = RUN_CREATED_AT,
    completed_ms: int | None = 1500,
) -> ToolRunListItem:
    """One list item, built the way the reader would build it.

    `completed_ms` rather than a `completed_at`: the pair is what `duration_ms` is derived from, so
    handing the test one number keeps the two fields consistent by construction — a `completed_at`
    that disagreed with `duration_ms` is a state the real reader cannot produce.
    """
    completed_at = created_at + timedelta(milliseconds=completed_ms) if completed_ms else None
    return ToolRunListItem(
        tool_run_id=tool_run_id or uuid4(),
        tool_name=tool_name,
        risk=risk,
        status=status,
        conversation_ref=conversation_ref,
        requested_by_email=requested_by_email,
        result_summary=result_summary,
        created_at=created_at,
        completed_at=completed_at,
        duration_ms=completed_ms,
    )


@dataclass
class RecordedListCall:
    """One `list_runs` call as the route made it."""

    filters: ToolRunAuditFilters
    limit: int
    cursor: KeysetCursor | None


class FakeToolRunAuditReader:
    """In-memory `ToolRunAuditReader`. Holds items, records calls, models paging only."""

    def __init__(
        self,
        items: list[ToolRunListItem] | None = None,
        *,
        args: dict[str, Any] | None = None,
    ) -> None:
        self.items: list[ToolRunListItem] = list(items or [])
        # What `get_run` attaches to the item it finds. One payload for the whole double: a test
        # that cares about arguments has one run in it.
        self.args: dict[str, Any] = dict(args or {})
        self.requested_by_user_id: UUID | None = uuid4()
        self.calls: list[RecordedListCall] = []
        self.detail_calls: list[UUID] = []

    # --- `ToolRunAuditReader` ---

    async def list_runs(
        self,
        *,
        filters: ToolRunAuditFilters,
        limit: int,
        cursor: KeysetCursor | None,
    ) -> tuple[list[ToolRunListItem], bool]:
        self.calls.append(RecordedListCall(filters=filters, limit=limit, cursor=cursor))
        # Paging from the cursor's *position* in the list, so a walk over pages behaves like the
        # keyset does without reimplementing its predicate. An unknown cursor pages from the start,
        # which no test relies on — the real refusal for a bad token is `decode_cursor`'s.
        start = 0
        if cursor is not None:
            for index, item in enumerate(self.items):
                if item.tool_run_id == cursor.entity_id:
                    start = index + 1
                    break
        window = self.items[start : start + limit]
        return list(window), len(self.items) > start + len(window)

    async def get_run(self, *, tool_run_id: UUID) -> ToolRunDetailView | None:
        self.detail_calls.append(tool_run_id)
        for item in self.items:
            if item.tool_run_id == tool_run_id:
                return ToolRunDetailView(
                    item=item,
                    args=dict(self.args),
                    requested_by_user_id=self.requested_by_user_id,
                )
        return None

    # --- Assertions ---

    @property
    def only_call(self) -> RecordedListCall:
        """The single recorded `list_runs` call, asserting there is exactly one."""
        assert len(self.calls) == 1, f"expected one list call, got {len(self.calls)}"
        return self.calls[0]


@dataclass
class FilterQuery:
    """A query string and the `ToolRunAuditFilters` field it must reach.

    Used as the table `test_admin_audit_routes.py` walks: the API's accepted parameter names in one
    place, so the panel's query builder (`audit-model.ts`) has one list to be checked against
    rather than seven assertions to drift from.
    """

    param: str
    value: str
    field_name: str
    expected: Any = field(default=None)
