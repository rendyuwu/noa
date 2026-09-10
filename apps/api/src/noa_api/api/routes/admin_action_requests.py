"""Admin: the CHANGE authorisation trail and its receipts (§I.admin-api — V13, V15, V46).

**The half `/admin/audit/tool-runs` does not have.** T55 made `tool_runs` queryable — what ran,
with what arguments, and how it ended. It could not answer *who authorised it, or why*, because
that lives on `action_requests` and nothing read that table for an administrator. The single most
load-bearing field on this surface is `reason`: C8 and V15 exist for it, a DB CHECK holds it
against every writer, and until this router it had no reader anywhere. The embed card carries no
`reason` field at any status by construction, and `tool_runs` has no column for it.

**Three routes, read-only, and there is nothing here to decide.** No POST, no PATCH, no DELETE,
and the service behind them can write nothing at all — `core.approvals.admin_reads` has no
`commit` and issues no statement that is not a `SELECT`. That matters more here than on the audit
surface: `action_requests.status` has exactly one writer for a terminal value
(`core.approvals.decisions`, V22, V28) and V32's expiry sweep has the other, so a second path that
could move a status would be a second answer to "may this run?". This router is structurally
incapable of being one.

**`require_admin` is a parameter on every handler**, not a router dependency, for the reason
`admin_users.py` gives: the gate and the actor are one read (V13), and it inherits V6's row
re-read with it, so a demoted or disabled admin loses these routes on their next request rather
than at cookie expiry.

**No requester-match, and that is not a weakening of V27.** V27 scopes the *embed* read to the
operator who asked, because that surface hands out an approve button. This one hands out nothing,
sits behind a strictly stronger gate, and exists to let an administrator read a decision they did
not make — an audit surface that could only show the reader's own decisions could audit nothing.

**camelCase in and out**, matching `admin_audit.py` next door rather than the snake_case rest of
`/admin`: the panel's audit vertical was ported with its client (DECISIONS §8.3) and this is the
same client's second view.

**Nothing here redacts and nothing here un-redacts.** `approval_context` was redacted at gate time
(T33) and `receipt_data` by `build_receipt` (V8); what this serves is what was stored. A second
redactor on the read would be a quieter second home for one rule (V66) — `core/approvals/card.py`
records the same argument for the operator-facing reader.

**One refusal for two causes on the detail routes** — no such request, and an id that is not a
UUID — both 404 `action_request_not_found`. Not because either is sensitive (this surface is
admin-only) but because a 422 for a malformed id would describe what the validator accepts rather
than what exists; `core.audit.errors` records the argument and T63(e) made the same call one
surface over. Which is why `action_request_id` is a plain `str` here rather than a `UUID` path
param. The receipt route adds a *third*, genuinely different refusal — the request is real and
carries no receipt — and it gets its own code, because "no such request" and "that decision
started no run" are two different things to tell an administrator.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from core.approvals.admin_reads import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ActionRequestAdminFilters,
)
from core.approvals.errors import ActionReceiptNotFoundError, ActionRequestNotFoundError
from core.audit.cursor import decode_cursor
from core.db.lifecycle import ActionRequestStatus
from noa_api.api.deps import ActionRequestAdminServiceDep, AdminUserDep

router = APIRouter(prefix="/admin/action-requests", tags=["admin", "audit"])


class AdminActionRequestListItemResponse(BaseModel):
    """One authorisation in the list (§I.admin-api).

    Neither `reason` nor `approvalContext`: a fifty-row page would otherwise carry fifty JSONB
    payloads to draw six columns, and both are one click away on the detail route — the same call
    `admin_audit.py` makes about `args`.

    `toolRunId` is `null` for every denied and expired request, because no run was ever started.
    `hasReceipt` answers whether the detail's receipt route has anything to serve, off a presence
    bit in the same statement rather than a query per row.

    `expiresAt` is carried on a decided request too. It is the historical fact of what the
    operator was working against, not a countdown, and dropping it once `decidedAt` is set would
    lose the one number that says whether a decision was made with time to spare.
    """

    actionRequestId: str
    toolName: str
    status: str
    requestedByEmail: str | None
    conversationRef: str | None
    createdAt: str
    expiresAt: str
    decidedAt: str | None
    toolRunId: str | None
    hasReceipt: bool


class AdminActionRequestListResponse(BaseModel):
    """`GET /admin/action-requests`. One page, and the token for the next.

    `nextCursor` is `null` on the last page, which is the bound this surface owes (V85's family):
    the client can tell "that is all of them" from "there is more" without inferring it from a row
    count against the limit it asked for.
    """

    items: list[AdminActionRequestListItemResponse]
    nextCursor: str | None


class AdminActionRequestDetailResponse(AdminActionRequestListItemResponse):
    """`GET /admin/action-requests/{id}`: the list item, the reason, and the gate-time context.

    **`reason` is why this router exists.** It is the operator's own words authorising a change
    (C8, V15), written at decision time by the one writer that may write it, and `null` while
    PENDING and after an expiry because nobody typed one. Never `''` on a decided request — the
    endpoints refuse a blank one with a 409 and `ck_action_requests_decided_reason` holds the same
    line against any other writer — so a client that finds an empty string here has found a bug,
    not an operator who said nothing.

    `approvalContext` is a loose type on purpose, exactly like `AuditToolRunDetailResponse.args`:
    the keys under `evidence` are whichever tool's own preflight vocabulary, so a model that
    flattened them would have to be widened by every tool ever added. Served as the object T33
    persisted — `requester`, `arguments`, `evidence` — and this is the surface on which
    `librechat_user_id`, `server_id` and `api_username` stay readable.
    """

    reason: str | None
    approvalContext: dict[str, Any]


class AdminActionReceiptResponse(BaseModel):
    """`GET /admin/action-requests/{id}/receipt`: what the approved change actually did.

    The two halves, uncollapsed. DECISIONS §6.5 refuses to let `before` and `after` become a
    single "done", so they are two fields here as they are two on the operator's card, and `delta`
    is the runner's own statement of what moved between them — the only one available, because the
    halves are written minutes apart in two vocabularies that meet on identity alone.

    `delta` is `null` when the writer stored none, and that absence is the *only* discriminator
    between an executor refusal, where nothing was measured so nothing is claimed, and a runner
    failure, which publishes a delta carrying earned falses. Both are `ok: false`.

    **A `yopass_url` in `after` or in `delta.delivered_credential` is text and never a link.** The
    secret behind it is one-time consumption (C15, V49): a hover preview, a prefetch or a stray
    click burns the operator's own delivery, and rendering it as text costs nothing. The rule is
    the renderer's to keep, and it is stated here because this is the response that carries the
    value to it.
    """

    actionRequestId: str
    toolRunId: str | None
    createdAt: str
    ok: bool
    before: dict[str, Any]
    after: dict[str, Any]
    errorCode: str | None
    delta: dict[str, Any] | None


def _parse_request_id(action_request_id: str) -> UUID:
    """The path segment as a UUID, or the same 404 an unknown id gets.

    One place rather than three, so the two detail routes and the receipt route cannot drift on
    which malformed input they refuse (V66). See the module docstring for why this is not a 422.
    """
    try:
        return UUID(action_request_id)
    except ValueError as exc:
        raise ActionRequestNotFoundError(
            f"`{action_request_id}` is not an action request id"
        ) from exc


@router.get("", response_model=AdminActionRequestListResponse)
async def list_action_requests(
    admin_user: AdminUserDep,
    requests: ActionRequestAdminServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
    status: Annotated[ActionRequestStatus | None, Query()] = None,
    tool_name: Annotated[str | None, Query(alias="toolName")] = None,
    requested_by_email: Annotated[str | None, Query(alias="requestedByEmail")] = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> AdminActionRequestListResponse:
    """CHANGE authorisations, newest first, filtered and cursor-paged (§I.admin-api — V13, V15).

    Every filter is applied inside the statement (V93). One applied after the fetch would also
    break paging, because the `LIMIT` would have cut rows the filter was about to remove and the
    page would come back short while `nextCursor` claimed there was more.

    `limit` is bounded here *and* in the service: this route is one caller, and a ceiling that
    only a query-string validator holds is one a future caller does not have.

    Refusals: 403 (V13), 422 for an out-of-range `limit` or an unknown `status`, 400
    `invalid_audit_cursor` for a token that does not decode — the same codec the audit list uses,
    so one page-token format serves both views (V66).
    """
    del admin_user

    page = await requests.list_requests(
        filters=ActionRequestAdminFilters(
            status=status,
            tool_name=tool_name,
            requested_by_email=requested_by_email,
            created_from=created_from,
            created_to=created_to,
        ),
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
    )

    return AdminActionRequestListResponse(
        items=[AdminActionRequestListItemResponse(**item.as_payload()) for item in page.items],
        nextCursor=page.next_cursor,
    )


@router.get("/{action_request_id}", response_model=AdminActionRequestDetailResponse)
async def get_action_request(
    action_request_id: str,
    admin_user: AdminUserDep,
    requests: ActionRequestAdminServiceDep,
) -> AdminActionRequestDetailResponse:
    """One authorisation: the operator's reason and the gate-time context (§I.admin-api, V15).

    `action_request_id` is a plain `str`, not a `UUID` path param, so a malformed id reaches the
    same 404 as an unknown one — see the module docstring for why the split is refused.
    """
    del admin_user

    parsed = _parse_request_id(action_request_id)
    detail = await requests.request_detail(action_request_id=parsed)
    if detail is None:
        raise ActionRequestNotFoundError(f"no `action_requests` row {parsed}")

    return AdminActionRequestDetailResponse(**detail.as_payload())


@router.get("/{action_request_id}/receipt", response_model=AdminActionReceiptResponse)
async def get_action_receipt(
    action_request_id: str,
    admin_user: AdminUserDep,
    requests: ActionRequestAdminServiceDep,
) -> AdminActionReceiptResponse:
    """What that decision's run recorded, in the halves it was written as (V46, V34).

    Two refusals with two codes. An unknown or malformed id is `action_request_not_found`; a real
    request that carries no receipt is `action_receipt_not_found`, which is the answer for every
    deny, every expiry and every request still pending. Collapsing them would tell an
    administrator that a decision they can see on the list does not exist.

    Telling them apart takes a second read, because the receipt lookup answers `None` for both:
    the route asks whether the request exists first. That read is `request_exists`, which selects
    the id and nothing else — not the detail read, which would carry `approval_context` back over
    two outer joins to answer a yes/no question, and that is the largest JSONB on this path. One
    extra `SELECT` on a path that is already a click, and it buys the difference between a dead
    link and a fact.
    """
    del admin_user

    parsed = _parse_request_id(action_request_id)
    if not await requests.request_exists(action_request_id=parsed):
        raise ActionRequestNotFoundError(f"no `action_requests` row {parsed}")

    receipt = await requests.receipt_detail(action_request_id=parsed)
    if receipt is None:
        raise ActionReceiptNotFoundError(f"no `action_receipts` row for request {parsed}")

    return AdminActionReceiptResponse(**receipt.as_payload())


__all__ = [
    "AdminActionReceiptResponse",
    "AdminActionRequestDetailResponse",
    "AdminActionRequestListItemResponse",
    "AdminActionRequestListResponse",
    "get_action_receipt",
    "get_action_request",
    "list_action_requests",
    "router",
]
