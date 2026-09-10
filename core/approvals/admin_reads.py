"""Reading the CHANGE authorisation trail for the admin panel (§I.admin-api — V13, V15, V45).

T55 made `tool_runs` queryable and stopped there, so an administrator could see *what ran* and
never *who authorised it or why*. `action_requests.reason` — the one field C8 and V15 exist for,
written by a decision and bounded by a DB CHECK — had no reader on any surface: the embed card
carries no `reason` field at any status by construction, and `tool_runs` has no column for it.
This module is that reader, and `action_receipts` comes with it because a decision and what it
produced are one story an operator reads in one sitting (V46, DECISIONS §6.5).

**Reader beside the writer, split by what it can do.** `core.approvals.decisions` is the single
writer of a terminal status and `core.audit.receipts` writes the receipt; nothing here
commits, nothing here holds a session it could commit through, and no statement here is anything
but a `SELECT`. Same discipline as `core.audit.tool_run_reads` and `core.results.tables`, and the
same reason: the write side is reachable from the MCP tool path, this side only from behind a
session cookie and `require_admin`.

**No requester match, deliberately.** V27 scopes the *embed* read to the operator who asked, which
is why `core.approvals.reads.select_requester_matched` puts the requester in the `WHERE`. An admin
audit surface that could only show the reader's own decisions would be unable to audit anything;
`require_admin` is this surface's scope, and it is a stronger gate than the one it replaces.

**The requester is an outer join**, for the reason `core.audit.tool_run_reads` gives: T34 made
`requested_by_user_id` a `SET NULL`, so an inner join would hide exactly the decisions that
outlived their operator. A missing email is `null` on the wire — "the account is gone", never
"nobody asked".

**The receipt join is a presence bit on the list and a row on the detail.** `action_receipts`
carries `UNIQUE (action_request_id)`, so the outer join cannot multiply rows and
`hasReceipt` needs no second query per row. The body itself is a separate read because it is the
largest JSONB on this path and most list rows will never be opened — T55's argument for leaving
`args` off the audit list, one table over.

**Nothing here redacts, and nothing here un-redacts.** `approval_context` was redacted at gate
time by T33's `build_approval_context`, and `receipt_data` by `build_receipt` on the way in.
A second redactor under the read would be a quieter second home for one rule and the one an
auditor would have to trust without seeing it. `core/approvals/card.py` records the same rule for
the operator-facing reader: the day two redaction policies disagree is the day one of them is
wrong.

**Expiry is not applied here.** V32's check-on-read belongs to `ActionRequestExpiryService`, whose
writer can set exactly one status, and this module cannot write. A PENDING row whose deadline has
passed therefore renders as PENDING with a past `expiresAt`, which is the honest reading: the
sweep has not run yet, and an admin view that silently reported a status the database does not
hold would be inventing a decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import as_utc
from core.approvals.execution import (
    # The keys T38's writer puts in `receipt_data`, read here rather than respelled — a misspelt
    # key in JSONB reads as an absent one, and this surface is the third reader those constants
    # exist for. A sixth key added there reaches this route; a hand-kept mirror would not.
    RECEIPT_AFTER_KEY,
    RECEIPT_BEFORE_KEY,
    RECEIPT_DELTA_KEY,
    RECEIPT_ERROR_CODE_KEY,
    RECEIPT_OK_KEY,
)
from core.audit.cursor import KeysetCursor, encode_cursor, keyset_predicate
from core.audit.tool_run_reads import LIKE_ESCAPE, escape_like
from core.db.lifecycle import ActionRequestStatus
from core.db.models import ActionReceipt, ActionRequest, User

# Page bounds, shared with the route's `Query(ge=…, le=…)` so the API and the statement agree on
# one answer — the same pairing `core.audit.tool_run_reads` keeps, and for the same reason: the
# route is one caller, and a ceiling only a query-string validator holds is one a future caller
# does not have.
DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200


@dataclass(frozen=True)
class ActionRequestAdminFilters:
    """What an admin narrowed the authorisation trail to (§I.admin-api).

    All optional, and an empty object means "everything" — the panel's first load. One frozen
    value rather than five parameters threaded through three layers, so the route maps query
    string to filters once and the statement builder reads one thing.

    `tool_name` is an exact match — it is an identifier from `TOOL_CATALOG` and the panel offers
    the names it already rendered. `requested_by_email` is a contains-match, because an operator
    searching a trail types a fragment of an address.
    """

    status: ActionRequestStatus | None = None
    tool_name: str | None = None
    requested_by_email: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


@dataclass(frozen=True)
class ActionRequestListItem:
    """One authorisation as the admin list renders it (§I.admin-api).

    Carries neither `reason` nor `approval_context`: a fifty-row page would otherwise ship fifty
    JSONB payloads to draw six columns, and both are one click away on the detail read. That is
    T55's argument for leaving `args` off the audit list, made again one table over.

    `tool_run_id` is the edge to `tool_runs` and it lives on this row and only here — a
    matching column on the run would be two truths about one link. `null` on a denied or expired
    request, because no run was ever started.

    `has_receipt` is a presence bit, not a count: `UNIQUE (action_request_id)` means there is at
    most one, so "is there a receipt to open" is the only question this column can answer.
    """

    action_request_id: UUID
    tool_name: str
    status: ActionRequestStatus
    requested_by_email: str | None
    conversation_ref: str | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    tool_run_id: UUID | None
    has_receipt: bool

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the HTTP body, camelCase like the rest of the audit surface.

        One spelling of this body: the route's response model is built from this dict rather than
        re-listing the fields, the way `ToolRunListItem.as_payload()` is T55's single spelling.
        Two hand-maintained copies of one payload is one that can disagree.
        """
        return {
            "actionRequestId": str(self.action_request_id),
            "toolName": self.tool_name,
            "status": self.status.value,
            "requestedByEmail": self.requested_by_email,
            "conversationRef": self.conversation_ref,
            "createdAt": self.created_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "decidedAt": self.decided_at.isoformat() if self.decided_at else None,
            "toolRunId": str(self.tool_run_id) if self.tool_run_id else None,
            "hasReceipt": self.has_receipt,
        }


@dataclass(frozen=True)
class ActionRequestDetailView:
    """One authorisation in full: the list item, the operator's reason, and the gate's context.

    Composed rather than subclassed so the extra fields are visible at the call site and so the
    list item's payload keys cannot drift from the detail's — `ToolRunDetailView`'s shape.

    `reason` is `None` while PENDING and `None` forever after an expiry: nobody typed one, and the
    DB CHECK `ck_action_requests_decided_reason` is what keeps that honest. It is never `''` for a
    decided request, which is why the wire spelling is `null` rather than an empty string — an
    empty reason on an approved change would read as "the operator wrote nothing" and the gate
    refuses that with a 409 before it can happen.

    `approval_context` is the object T33 persisted, carried whole. Not re-projected into named
    fields: the keys are the gate's own vocabulary per tool, so a model that flattened them here
    would have to be widened by every tool ever added — `ToolRunDetailView.args`' argument. This
    is the surface on which `librechat_user_id`, `server_id` and `api_username` remain readable.
    """

    item: ActionRequestListItem
    reason: str | None
    approval_context: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        """The list item's fields, plus the reason and the gate-time context."""
        return {
            **self.item.as_payload(),
            "reason": self.reason,
            "approvalContext": dict(self.approval_context),
        }


@dataclass(frozen=True)
class ActionReceiptAdminView:
    """What an approved CHANGE actually did, as an administrator may read it.

    The stored halves, uncollapsed. DECISIONS §6.5 refuses to let `before` and `after` become a
    single "done", so they are two fields here as they are two fields on the operator's card, and
    `delta` is the runner's own sentence about what moved between them.

    `delta` is `None` when the writer stored none, and that absence is the only discriminator
    between an executor refusal — nothing was measured, so nothing is claimed — and a runner
    failure, which publishes a delta carrying earned falses. Both are `ok: false`, so a reader
    that switched on `ok` alone would merge two different stories.

    `created_at` is the receipt row's own stamp and it is carried here where the operator's card
    deliberately drops it: an audit reader is placing this against `tool_runs` timings and a
    ticket, and the run's start and finish are on a different surface.
    """

    action_request_id: UUID
    tool_run_id: UUID | None
    created_at: datetime
    ok: bool
    before: dict[str, Any]
    after: dict[str, Any]
    error_code: str | None
    delta: dict[str, Any] | None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the HTTP body.

        `errorCode` and `delta` are `null` rather than omitted, for the reason the card's payload
        gives: this body is parsed by a renderer that switches on the field, and a missing key
        reads as one it forgot. The opposite of the stored receipt's rule, and deliberately so —
        the row omits what nothing measured so the absence is a fact, and this turns that absence
        into the `null` a renderer branches on.
        """
        return {
            "actionRequestId": str(self.action_request_id),
            "toolRunId": str(self.tool_run_id) if self.tool_run_id else None,
            "createdAt": self.created_at.isoformat(),
            "ok": self.ok,
            "before": dict(self.before),
            "after": dict(self.after),
            "errorCode": self.error_code,
            "delta": None if self.delta is None else dict(self.delta),
        }


@dataclass(frozen=True)
class ActionRequestPage:
    """One page of authorisations, and the token for the next — `None` on the last page.

    `next_cursor` is the bound this surface owes (V85's family): a client can tell "that is all of
    them" from "there is more" without counting rows against the limit it asked for.
    """

    items: list[ActionRequestListItem]
    next_cursor: str | None


def _apply_filters(statement: Select[Any], filters: ActionRequestAdminFilters) -> Select[Any]:
    """Add one predicate per set filter. Every one lands in the `WHERE`.

    A filter applied after the fetch is not a filter here — it also breaks paging, because the
    `LIMIT` would have cut the rows the filter was about to remove, so a page comes back short
    while `nextCursor` insists there is more.
    """
    if filters.status is not None:
        statement = statement.where(ActionRequest.status == filters.status)
    if filters.tool_name:
        statement = statement.where(ActionRequest.tool_name == filters.tool_name)
    if filters.requested_by_email:
        # Both halves of the escaping come from `core.audit.tool_run_reads`: `escape_like` inserts
        # the character and `LIKE_ESCAPE` is the character the `ESCAPE` clause declares. Imported
        # rather than respelled here — two copies that must agree is one that can drift, and
        # a declared escape that does not match the inserted one turns a caller's `%` back into a
        # wildcard while the response still reads as filtered.
        statement = statement.where(
            User.email.ilike(f"%{escape_like(filters.requested_by_email)}%", escape=LIKE_ESCAPE)
        )
    if filters.created_from is not None:
        statement = statement.where(ActionRequest.created_at >= filters.created_from)
    if filters.created_to is not None:
        statement = statement.where(ActionRequest.created_at <= filters.created_to)
    return statement


def _joined() -> Select[Any]:
    """`action_requests` with the requester's email and the receipt's presence bit.

    Two outer joins and no inner one. `requested_by_user_id` is `SET NULL`, so an inner join
    to `users` would hide the decisions that outlived their operator; `action_receipts` is absent
    for every denied, expired and still-pending request, which is most of the table. The receipt
    join cannot multiply rows — `uq_action_receipts_action_request_id` allows at most one.
    """
    return (
        select(
            ActionRequest,
            User.email.label("requested_by_email"),
            ActionReceipt.id.label("receipt_id"),
        )
        .outerjoin(User, User.id == ActionRequest.requested_by_user_id)
        .outerjoin(ActionReceipt, ActionReceipt.action_request_id == ActionRequest.id)
    )


def select_action_request_page(
    *,
    filters: ActionRequestAdminFilters,
    limit: int,
    cursor: KeysetCursor | None,
) -> Select[Any]:
    """The statement one page of authorisations is read with (§I.admin-api, V85, V92, V93).

    Built as a function for the reason `select_tool_run_page` is: the claims worth asserting
    — that the filters and the cursor are *in* the statement, and that the order carries its
    tie-break — are claims about the SQL, and a check made after the rows arrive would leave every
    payload test green while being no check at all.

    One order, and the tie-break is part of it: `created_at DESC, id DESC`. `created_at` is not
    unique — a burst of gate insertions in one millisecond is ordinary — and paging by a
    non-unique key splits a tied group differently per call (V92(c)).

    `limit + 1`: the extra row answers "is there another page" without a second `COUNT` over a
    table being appended to while it is read. It is never serialised.
    """
    statement = _apply_filters(_joined(), filters)

    if cursor is not None:
        statement = statement.where(
            keyset_predicate(
                timestamp_column=ActionRequest.created_at,
                id_column=ActionRequest.id,
                cursor=cursor,
            )
        )

    return statement.order_by(ActionRequest.created_at.desc(), ActionRequest.id.desc()).limit(
        limit + 1
    )


def select_action_request(*, action_request_id: UUID) -> Select[Any]:
    """The statement one authorisation's detail is read with.

    Same two outer joins, no filters, no cursor and no limit: an id names at most one row, and a
    `LIMIT 1` on a primary-key lookup would be a bound with nothing to bound.
    """
    return _joined().where(ActionRequest.id == action_request_id)


def select_action_request_id(*, action_request_id: UUID) -> Select[Any]:
    """Does a row with this id exist — one column, no joins, nothing loaded.

    The receipt route has to tell "no such request" from "that decision started no run", and the
    receipt read answers `None` to both. Asking the detail statement would settle it, but the
    detail statement is two outer joins returning `approval_context` — the largest JSONB on this
    path, and the one this module's own list item drops for exactly that reason. This read exists
    so that a question about presence costs a presence-sized query: the id is already known, so
    the answer is one row of one column that the caller only tests for `None`.
    """
    return select(ActionRequest.id).where(ActionRequest.id == action_request_id)


def select_action_receipt(*, action_request_id: UUID) -> Select[Any]:
    """The statement one receipt is read with — by the request's id, never the receipt's.

    The panel navigates from a decision to what it did, so the request id is the only id it holds;
    and `UNIQUE (action_request_id)` means that lookup is as precise as a primary key would be.
    """
    return select(ActionReceipt).where(ActionReceipt.action_request_id == action_request_id)


def _to_list_item(
    request: ActionRequest, requested_by_email: str | None, receipt_id: UUID | None
) -> ActionRequestListItem:
    """One row → one list item. The only place the mapping lives."""
    return ActionRequestListItem(
        action_request_id=request.id,
        tool_name=request.tool_name,
        status=ActionRequestStatus(request.status),
        requested_by_email=requested_by_email,
        conversation_ref=request.conversation_ref,
        created_at=as_utc(request.created_at),
        expires_at=as_utc(request.expires_at),
        decided_at=as_utc(request.decided_at) if request.decided_at else None,
        tool_run_id=request.tool_run_id,
        has_receipt=receipt_id is not None,
    )


def _to_receipt_view(receipt: ActionReceipt) -> ActionReceiptAdminView:
    """One `action_receipts` row → the admin view of it.

    Every key comes off the writer's constants. `ok` is `is True` rather than truthy for the
    reason `build_receipt` writes it that way: a receipt whose `ok` is a string is a malformed
    receipt, and reading it as success would be this surface inventing an outcome.
    """
    data = dict(receipt.receipt_data or {})
    delta = data.get(RECEIPT_DELTA_KEY)
    before = data.get(RECEIPT_BEFORE_KEY)
    after = data.get(RECEIPT_AFTER_KEY)
    error_code = data.get(RECEIPT_ERROR_CODE_KEY)

    return ActionReceiptAdminView(
        action_request_id=receipt.action_request_id,
        tool_run_id=receipt.tool_run_id,
        created_at=as_utc(receipt.created_at),
        ok=data.get(RECEIPT_OK_KEY) is True,
        before=dict(before) if isinstance(before, dict) else {},
        after=dict(after) if isinstance(after, dict) else {},
        error_code=error_code if isinstance(error_code, str) else None,
        delta=dict(delta) if isinstance(delta, dict) else None,
    )


class ActionRequestAdminReader(Protocol):
    """The reads this surface may make. No write, and no `commit` to make one with.

    Every method here is driven by `test_action_request_admin_read.py`'s read-only walk, and that
    file asserts this Protocol's method names against the set it drives — a read added here and
    not driven there is caught by name rather than being left outside the two properties.
    """

    async def list_requests(
        self,
        *,
        filters: ActionRequestAdminFilters,
        limit: int,
        cursor: KeysetCursor | None,
    ) -> tuple[list[ActionRequestListItem], bool]: ...

    async def get_request(self, *, action_request_id: UUID) -> ActionRequestDetailView | None: ...

    async def get_receipt(self, *, action_request_id: UUID) -> ActionReceiptAdminView | None: ...

    async def request_exists(self, *, action_request_id: UUID) -> bool: ...


class SQLActionRequestAdminReader:
    """`ActionRequestAdminReader` over one `AsyncSession`.

    Returns `(items, has_more)` rather than a cursor: encoding one is a decision about the wire,
    and the row a cursor points at is the last item this method just returned — so the service
    above can mint it from the item without this class knowing the token format.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_requests(
        self,
        *,
        filters: ActionRequestAdminFilters,
        limit: int,
        cursor: KeysetCursor | None,
    ) -> tuple[list[ActionRequestListItem], bool]:
        """One page, and whether the statement saw a row beyond it."""
        result = await self._session.execute(
            select_action_request_page(filters=filters, limit=limit, cursor=cursor)
        )
        rows = list(result.all())

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        return [
            _to_list_item(request, email, receipt_id) for request, email, receipt_id in rows
        ], has_more

    async def get_request(self, *, action_request_id: UUID) -> ActionRequestDetailView | None:
        """One authorisation in full, or `None` when no row carries that id."""
        result = await self._session.execute(
            select_action_request(action_request_id=action_request_id)
        )
        row = result.first()
        if row is None:
            return None

        request, email, receipt_id = row
        return ActionRequestDetailView(
            item=_to_list_item(request, email, receipt_id),
            reason=request.reason,
            approval_context=dict(request.approval_context or {}),
        )

    async def get_receipt(self, *, action_request_id: UUID) -> ActionReceiptAdminView | None:
        """The receipt for one request, or `None` when the decision produced no run."""
        result = await self._session.execute(
            select_action_receipt(action_request_id=action_request_id)
        )
        receipt = result.scalars().first()
        if receipt is None:
            return None
        return _to_receipt_view(receipt)

    async def request_exists(self, *, action_request_id: UUID) -> bool:
        """Whether a row carries this id. Reads the id and nothing else."""
        result = await self._session.execute(
            select_action_request_id(action_request_id=action_request_id)
        )
        return result.scalars().first() is not None


class ActionRequestAdminService:
    """Query the CHANGE authorisation trail (§I.admin-api — V13, V15, V46).

    Thin, like `ToolRunAuditService`: it bounds the page size, decides the continuation token, and
    holds a *reader* rather than any of the three writers next door (`decisions`, `expiry`,
    `receipts`). The bound is enforced here as well as in the route's `Query(le=…)`, because the
    route is one caller and a limit only a query-string validator holds is one a CLI or a
    background export does not have.
    """

    def __init__(self, *, repository: ActionRequestAdminReader) -> None:
        self._repository = repository

    async def list_requests(
        self,
        *,
        filters: ActionRequestAdminFilters | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: KeysetCursor | None = None,
    ) -> ActionRequestPage:
        """One page of authorisations, newest first, with the token for the next one.

        `next_cursor` is minted from the last item on *this* page — never from the row beyond it,
        which the reader dropped: the cursor means "continue strictly after what you have seen",
        and taking it from the extra row would skip that row on the next page.
        """
        bounded = max(1, min(int(limit), MAX_PAGE_SIZE))
        items, has_more = await self._repository.list_requests(
            filters=filters or ActionRequestAdminFilters(),
            limit=bounded,
            cursor=cursor,
        )

        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(
                KeysetCursor(timestamp=last.created_at, entity_id=last.action_request_id)
            )

        return ActionRequestPage(items=items, next_cursor=next_cursor)

    async def request_detail(self, *, action_request_id: UUID) -> ActionRequestDetailView | None:
        """One authorisation with its reason and gate-time context, or `None`."""
        return await self._repository.get_request(action_request_id=action_request_id)

    async def receipt_detail(self, *, action_request_id: UUID) -> ActionReceiptAdminView | None:
        """What that decision's run recorded, or `None` when it recorded nothing."""
        return await self._repository.get_receipt(action_request_id=action_request_id)

    async def request_exists(self, *, action_request_id: UUID) -> bool:
        """Whether the request is there at all, without loading what it holds.

        The receipt route's two refusals need this and nothing more: `receipt_detail` answers
        `None` both for a request that does not exist and for one that started no run, so the
        route asks this first to tell them apart. `request_detail` would answer the same question
        and carry `approval_context` back to do it.
        """
        return await self._repository.request_exists(action_request_id=action_request_id)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "ActionReceiptAdminView",
    "ActionRequestAdminFilters",
    "ActionRequestAdminReader",
    "ActionRequestAdminService",
    "ActionRequestDetailView",
    "ActionRequestListItem",
    "ActionRequestPage",
    "SQLActionRequestAdminReader",
    "select_action_receipt",
    "select_action_request",
    "select_action_request_id",
    "select_action_request_page",
]
