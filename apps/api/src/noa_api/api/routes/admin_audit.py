"""Admin audit: query the `tool_runs` trail (T55, I.admin-api — V45, V47).

**Two routes, read-only, and there is nothing here to decide.** The audit trail is append-only by
construction — its writers are the MCP tool path (T73) and the approval executor (T37, T38) — so
this router has no POST, no PATCH and no DELETE, and the service behind it can write nothing at
all (`core.audit.tool_run_reads`: no `commit`, no statement that is not a `SELECT`). That is the
same split T56's table surface makes, and the reason a prompt-injected tool name can never reach
this file: the MCP mount cannot see it.

**`require_admin` is a parameter on both handlers**, not a router dependency, for the reason
`admin_users.py` gives: the gate and the actor are one read (V13), and it inherits V6's row
re-read with it, so a demoted or disabled admin loses these routes on their next request rather
than at cookie expiry.

**This closes V45's last clause.** "Every MCP READ writes a `tool_runs` row … queryable in admin
audit" — the row has been written since T73 and until now nothing could ask about it, which made
half that invariant prose (V69). `test_admin_audit_live.py` reads a row written by the real writer
through the real reader, which is the only shape that proves the clause rather than agreeing with
it.

**Nothing here redacts, and nothing here un-redacts.** `args` and `result_summary` were redacted
by their writers (`noa_api.mcp_audit.redacted_args`; `core.approvals.decisions` passes
`redacted_arguments`), so what this surface serves is what was stored. A second redactor on the
read would be a quieter second home for one rule (V66) — see the module docstring in
`core.audit.tool_run_reads`.

**Query params are camelCase, the body is camelCase, and both are the panel's spelling.** Unlike
the rest of `/admin`, which is snake_case in and out: this vertical was ported with its client
(DECISIONS §8.3), and renaming the wire would mean editing the panel to no end. The alias is
declared on the `Query`, so FastAPI documents the name the client actually sends.

**One refusal for two causes on the detail route** — no such run, and an id that is not a UUID —
both 404 `tool_run_not_found`. Not because either is sensitive (this surface is admin-only) but
because a 422 for a malformed id would describe what the validator accepts rather than what
exists; `core.audit.errors` records the argument, and T63(e) made the same call one surface over.
Which is why `tool_run_id` is a plain `str` here rather than a `UUID` path param.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from core.audit.cursor import decode_cursor
from core.audit.errors import ToolRunNotFoundError
from core.audit.tool_run_reads import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ToolRunAuditFilters,
)
from core.db.lifecycle import ToolRisk, ToolRunStatus
from noa_api.api.deps import AdminUserDep, ToolRunAuditServiceDep

router = APIRouter(prefix="/admin/audit", tags=["admin", "audit"])


class AuditToolRunListItemResponse(BaseModel):
    """One run in the list (V47).

    Every field V47 names except `args`: a fifty-row page would otherwise carry fifty JSONB
    payloads to draw five columns, and the arguments are one click away on the detail route.

    `risk` and `status` are separate fields because they are separate columns (V20) — that is what
    makes a *failed READ* representable, and this list is where an operator sees one.

    `conversationRef` is `null` unless `librechat.yaml` supplies the header T57 writes: the tool
    call carries no conversation id (R27), and the label is a grouping aid, never a scope.
    """

    toolRunId: str
    toolName: str
    risk: str
    status: str
    conversationRef: str | None
    requestedByEmail: str | None
    resultSummary: str | None
    createdAt: str
    completedAt: str | None
    durationMs: int | None


class AuditToolRunListResponse(BaseModel):
    """`GET /admin/audit/tool-runs`. One page, and the token for the next.

    `nextCursor` is `null` on the last page, which is the bound this surface owes (V85's family):
    the client can tell "that is all of them" from "there is more" without inferring it from a row
    count against the limit it asked for.
    """

    items: list[AuditToolRunListItemResponse]
    nextCursor: str | None


class AuditToolRunDetailResponse(AuditToolRunListItemResponse):
    """`GET /admin/audit/tool-runs/{id}`: the list item plus the redacted arguments.

    `args` is a loose type on purpose — the keys are whichever tool's own parameters, so a model
    that flattened them here would have to be widened by every tool ever added. `{}` means the
    call took no arguments; the column's server default says the same thing (T35), because "took
    none" and "not recorded" must not read alike in an audit view.

    `requestedByUserId` is `null` once the operator's row is deleted (`SET NULL`, T35) — the run
    survives its requester, which is the whole reason that FK is not a cascade.
    """

    requestedByUserId: str | None
    args: dict[str, Any]


@router.get("/tool-runs", response_model=AuditToolRunListResponse)
async def list_tool_runs(
    admin_user: AdminUserDep,
    audit: ToolRunAuditServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
    tool_name: Annotated[str | None, Query(alias="toolName")] = None,
    status: Annotated[ToolRunStatus | None, Query()] = None,
    risk: Annotated[ToolRisk | None, Query()] = None,
    conversation_ref: Annotated[str | None, Query(alias="conversationRef")] = None,
    requested_by_email: Annotated[str | None, Query(alias="requestedByEmail")] = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> AuditToolRunListResponse:
    """Tool runs, newest first, filtered and cursor-paged (T55 — V45, V47).

    READ and CHANGE alike: `risk` filters, it does not scope, so the default answer is the whole
    trail. Every filter is applied inside the statement (V93) — one applied after the fetch would
    also break paging, because the `LIMIT` would have cut rows the filter was about to remove and
    the page would come back short while `nextCursor` claimed there was more.

    `limit` is bounded here *and* in the service: this route is one caller, and a ceiling that only
    a query-string validator holds is one a future caller does not have.

    Refusals: 403 (V13), 422 for an out-of-range `limit` or an unknown `status`/`risk`, 400
    `invalid_audit_cursor` for a token that does not decode.
    """
    del admin_user

    page = await audit.list_runs(
        filters=ToolRunAuditFilters(
            tool_name=tool_name,
            status=status,
            risk=risk,
            conversation_ref=conversation_ref,
            requested_by_email=requested_by_email,
            created_from=created_from,
            created_to=created_to,
        ),
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
    )

    return AuditToolRunListResponse(
        items=[AuditToolRunListItemResponse(**item.as_payload()) for item in page.items],
        nextCursor=page.next_cursor,
    )


@router.get("/tool-runs/{tool_run_id}", response_model=AuditToolRunDetailResponse)
async def get_tool_run(
    tool_run_id: str,
    admin_user: AdminUserDep,
    audit: ToolRunAuditServiceDep,
) -> AuditToolRunDetailResponse:
    """One run: redacted arguments, result summary, timing (T55 — §I.admin-api, V47).

    `tool_run_id` is a plain `str`, not a `UUID` path param, so a malformed id reaches the same
    404 as an unknown one — see the module docstring for why the split is refused.
    """
    del admin_user

    try:
        parsed = UUID(tool_run_id)
    except ValueError as exc:
        raise ToolRunNotFoundError(f"`{tool_run_id}` is not a tool run id") from exc

    detail = await audit.run_detail(tool_run_id=parsed)
    if detail is None:
        raise ToolRunNotFoundError(f"no `tool_runs` row {parsed}")

    return AuditToolRunDetailResponse(**detail.as_payload())


__all__ = [
    "AuditToolRunDetailResponse",
    "AuditToolRunListItemResponse",
    "AuditToolRunListResponse",
    "get_tool_run",
    "list_tool_runs",
    "router",
]
