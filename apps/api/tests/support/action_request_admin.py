"""In-memory reader for the admin action-request route tests (the admin API's contract).

The double holds `ActionRequestListItem`s beside the reason, the gate context and the receipt each
one carries, and answers every read on the `ActionRequestAdminReader` Protocol. That lets a route
test assert on status codes, on the payload shape and — the part a live database makes awkward —
on **exactly which filters the route handed down**, and on **which read a route chose to make**.
`calls` records every `ActionRequestAdminFilters` it was
given, which is how `test_admin_action_request_routes.py` proves the query string maps to the
filter object rather than to nothing.

What it deliberately does **not** do is apply those filters or the keyset predicate.
`core.approvals.admin_reads.select_action_request_page` puts both in the SQL, so a Python
re-implementation here would be a second, more forgiving answer to a question the statement
already answers — a test passing against it would be agreeing with the double.
`test_action_request_admin_read.py` reads the compiled statement and
`test_admin_action_requests_live.py` runs it against Postgres.

Paging *is* modelled, because it is the service's decision rather than the SQL's: the reader
contract is "return at most `limit` items and say whether there was another", and
`ActionRequestAdminService` mints the cursor from that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from core.approvals.admin_reads import (
    ActionReceiptAdminView,
    ActionRequestAdminFilters,
    ActionRequestDetailView,
    ActionRequestListItem,
)
from core.audit.cursor import KeysetCursor
from core.db.lifecycle import ActionRequestStatus

# A fixed instant so timing assertions are about the code's arithmetic and not the clock.
REQUEST_CREATED_AT = datetime(2026, 9, 9, 10, 0, 0, tzinfo=UTC)

# The keys the gate persists (`core.approvals.context`), spelled here as the fixture a card would
# have been rendered from. `evidence` deliberately carries `server_id` and `api_username`: those
# are two of the fields the operator's card stops rendering, and this surface is where they stay
# readable, so the fixture has to contain them for that claim to be testable at all.
GATE_CONTEXT: dict[str, Any] = {
    "requester": {
        "email": "operator@example.com",
        "librechat_user_id": "lc-user-77",
    },
    "arguments": {"user": "acmecorp", "server_ref": "web-01"},
    "evidence": {
        "server_id": "3f9d0a2e-0000-4000-8000-000000000001",
        "api_username": "noa-automation",
        "account": {"user": "acmecorp", "domain": "acme.example", "suspended": False},
    },
}


def build_list_item(
    *,
    action_request_id: UUID | None = None,
    tool_name: str = "whm_suspend_account",
    status: ActionRequestStatus = ActionRequestStatus.APPROVED,
    requested_by_email: str | None = "operator@example.com",
    conversation_ref: str | None = "conv-1",
    created_at: datetime = REQUEST_CREATED_AT,
    expires_minutes: int = 15,
    decided_after_seconds: int | None = 42,
    tool_run_id: UUID | None = None,
    has_receipt: bool = True,
) -> ActionRequestListItem:
    """One list item, built the way the reader would build it.

    `decided_after_seconds` rather than a `decided_at`: a decision stamped before the request was
    created is a state the real reader cannot produce, so the fixture derives it.
    """
    return ActionRequestListItem(
        action_request_id=action_request_id or uuid4(),
        tool_name=tool_name,
        status=status,
        requested_by_email=requested_by_email,
        conversation_ref=conversation_ref,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=expires_minutes),
        decided_at=(
            created_at + timedelta(seconds=decided_after_seconds)
            if decided_after_seconds is not None
            else None
        ),
        tool_run_id=tool_run_id or (uuid4() if has_receipt else None),
        has_receipt=has_receipt,
    )


def build_receipt(
    *,
    action_request_id: UUID,
    tool_run_id: UUID | None = None,
    ok: bool = True,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    error_code: str | None = None,
    delta: dict[str, Any] | None = None,
) -> ActionReceiptAdminView:
    """One receipt as the reader would return it, with both halves populated by default."""
    return ActionReceiptAdminView(
        action_request_id=action_request_id,
        tool_run_id=tool_run_id or uuid4(),
        created_at=REQUEST_CREATED_AT + timedelta(seconds=45),
        ok=ok,
        before=dict(before if before is not None else GATE_CONTEXT["evidence"]),
        after=dict(after if after is not None else {"suspended": True, "user": "acmecorp"}),
        error_code=error_code,
        delta=None if delta is None else dict(delta),
    )


@dataclass
class RecordedListCall:
    """One `list_requests` call as the route made it."""

    filters: ActionRequestAdminFilters
    limit: int
    cursor: KeysetCursor | None


class FakeActionRequestAdminReader:
    """In-memory `ActionRequestAdminReader`. Holds items, records calls, models paging only."""

    def __init__(
        self,
        items: list[ActionRequestListItem] | None = None,
        *,
        reason: str | None = "Customer confirmed the abuse report on ticket OPS-4412.",
        approval_context: dict[str, Any] | None = None,
        receipts: dict[UUID, ActionReceiptAdminView] | None = None,
    ) -> None:
        self.items: list[ActionRequestListItem] = list(items or [])
        # One reason and one context for the whole double: a test that cares about either has one
        # request in it, the way `FakeToolRunAuditReader` carries one `args` payload.
        self.reason = reason
        self.approval_context: dict[str, Any] = dict(
            approval_context if approval_context is not None else GATE_CONTEXT
        )
        self.receipts: dict[UUID, ActionReceiptAdminView] = dict(receipts or {})
        self.calls: list[RecordedListCall] = []
        self.detail_calls: list[UUID] = []
        self.receipt_calls: list[UUID] = []
        self.exists_calls: list[UUID] = []

    # --- `ActionRequestAdminReader` ---

    async def list_requests(
        self,
        *,
        filters: ActionRequestAdminFilters,
        limit: int,
        cursor: KeysetCursor | None,
    ) -> tuple[list[ActionRequestListItem], bool]:
        self.calls.append(RecordedListCall(filters=filters, limit=limit, cursor=cursor))
        # Paging from the cursor's *position* in the list, so a walk over pages behaves like the
        # keyset does without reimplementing its predicate.
        start = 0
        if cursor is not None:
            for index, item in enumerate(self.items):
                if item.action_request_id == cursor.entity_id:
                    start = index + 1
                    break
        window = self.items[start : start + limit]
        return list(window), len(self.items) > start + len(window)

    async def get_request(self, *, action_request_id: UUID) -> ActionRequestDetailView | None:
        self.detail_calls.append(action_request_id)
        for item in self.items:
            if item.action_request_id == action_request_id:
                return ActionRequestDetailView(
                    item=item,
                    reason=self.reason,
                    approval_context=dict(self.approval_context),
                )
        return None

    async def get_receipt(self, *, action_request_id: UUID) -> ActionReceiptAdminView | None:
        self.receipt_calls.append(action_request_id)
        return self.receipts.get(action_request_id)

    async def request_exists(self, *, action_request_id: UUID) -> bool:
        # Recorded separately from `detail_calls`, so a route test can say which of the two reads
        # the receipt path made — the whole point of the cheap one is that the expensive one is
        # not issued to answer a yes/no question.
        self.exists_calls.append(action_request_id)
        return any(item.action_request_id == action_request_id for item in self.items)

    # --- Assertions ---

    @property
    def only_call(self) -> RecordedListCall:
        """The single recorded `list_requests` call, asserting there is exactly one."""
        assert len(self.calls) == 1, f"expected one list call, got {len(self.calls)}"
        return self.calls[0]


__all__ = [
    "GATE_CONTEXT",
    "REQUEST_CREATED_AT",
    "FakeActionRequestAdminReader",
    "RecordedListCall",
    "build_list_item",
    "build_receipt",
]
