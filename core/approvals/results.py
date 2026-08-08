"""Reading one approval request back, for the operator who asked for it (T63 — V27, V76).

T33 opens a request, T37 decides it, T39 expires the ones nobody answered. This is the only
thing that *reads* one from the MCP side: `noa_get_action_result` is how a model finds out
what happened to a change it asked for, without the answer travelling through an LLM claim
(V23 — the row is still the authority) and without it reaching past the operator who opened
it (V27, V76).

**A fourth class, and the reason is the one that split the first three.** `repository` writes
PENDING, `decisions` writes APPROVED/DENIED under a lock, `expiry` writes EXPIRED and nothing
else. This one writes *nothing at all*: it has no `commit`, no status parameter and no
statement that is not a `SELECT`. The one write on this path — expiring a stale PENDING so a
GET cannot serve it (V32) — is delegated to `ActionRequestExpiryService`, whose writer can set
exactly one status.

**The requester-match is in the `WHERE`, not in an `if`.** `id = :id AND
requested_by_user_id = :caller`, so a row that is not the caller's is never fetched and there
is no later branch that could forget to drop it. A NULL requester — the FK is `SET NULL`
(T34), so a deleted operator leaves one behind — matches nobody under SQL's NULL semantics,
which is the fail-closed direction V27 names.

Stated as a preference and not as a proof, because it was measured: moving the comparison
out of the statement and into a Python `!=` after the read leaves **every test in this task
green**, live ones included. The two spellings answer identically — `None != caller` is
`True`, so even the deleted-requester case still refuses. What the statement buys is that the
foreign row does not exist in this process to be logged, returned by a later edit, or half
dropped by a refactor; that is defence in depth, and the honest place to say so is here
rather than in a test name that would imply otherwise (V69: a control asserted by prose is
worth exactly what the prose is worth).

**What the views cannot carry.** `ActionResultView` has no `reason` field and nowhere to put
one. The reason is the operator's own words, typed on the approval card, and C8 says the LLM
never authors it, never relays it and never *sees* it (V15, V43) — and this tool answers into
a transcript that persists in LibreChat's MongoDB (V26). The same is true of the preflight
evidence on `approval_context`: V17 says it is born in-process and stays out of the
transcript, so it is read for the card (V33, V35) and never for the model. Neither is filtered
out downstream; neither is ever loaded. `arguments_from_context` is the only thing this module
takes off that payload.

**The run is `tool_runs`, not `action_receipts`.** §T.63 says "receipt summary", and T36's
`action_receipts` table plus T38's executor are both unbuilt — a join to a table nothing fills
would answer `null` while looking like a feature. `action_requests.tool_run_id` is written in
the same transaction as an approval (T37, V29), so the run row is the execution record that
exists today: status, redacted summary, timing (V47). When T36 lands, the receipt joins here
beside it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import as_utc, now_utc
from core.approvals.context import arguments_from_context
from core.approvals.expiry import ActionRequestExpiryService
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from core.db.models import ActionRequest, ToolRun


@dataclass(frozen=True)
class ActionRunView:
    """The execution an approval started, as far as it has got (V29, V46, V47).

    `result_summary` is already truncated and already redacted by whoever wrote it
    (`noa_api.mcp_audit` for a READ, T38's executor for a change) — nothing here re-derives
    it, for the reason `LockedActionRequest.redacted_arguments` gives one field over.

    `STARTED` with a NULL summary is the normal state today: T37 opens this row inside the
    decision's transaction and T38's executor, which moves it, is unbuilt. Reporting that
    plainly is the point — a model that is told "started" tells an operator to wait, which is
    true.
    """

    tool_run_id: UUID
    status: ToolRunStatus
    result_summary: str | None
    created_at: datetime
    completed_at: datetime | None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the tool result."""
        return {
            "tool_run_id": str(self.tool_run_id),
            "status": self.status.value,
            "result_summary": self.result_summary,
            "created_at": self.created_at.isoformat(),
            "completed_at": None if self.completed_at is None else self.completed_at.isoformat(),
        }


@dataclass(frozen=True)
class ActionResultView:
    """One approval request as its requester may see it (V27, V76).

    Deliberately narrower than `LockedActionRequest`: that one is what a *decision* needs and
    carries the whole `approval_context`; this is what a model may be told. No `reason`, no
    `evidence`, no requester identity — see the module docstring for why each is absent rather
    than filtered.
    """

    action_request_id: UUID
    tool_name: str
    status: ActionRequestStatus
    arguments: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    run: ActionRunView | None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the tool result.

        `run` is `None` rather than omitted when there is none: "this change never ran" is an
        answer, and a missing key reads to a model as a field it forgot to look at.
        """
        return {
            "action_request_id": str(self.action_request_id),
            "tool_name": self.tool_name,
            "status": self.status.value,
            "arguments": dict(self.arguments),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "decided_at": None if self.decided_at is None else self.decided_at.isoformat(),
            "run": None if self.run is None else self.run.as_payload(),
        }


class ActionResultRepository(Protocol):
    """The one read this path may make (V27)."""

    async def get_for_requester(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
    ) -> ActionResultView | None: ...


class SQLActionResultRepository:
    """`ActionResultRepository` over one `AsyncSession`.

    No `commit`, and nothing to commit: every statement here is a `SELECT`. The session is
    the caller's, and on the MCP tool path that is one opened and closed around this read
    (`noa_api.mcp_tools.noa_read`).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_requester(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
    ) -> ActionResultView | None:
        """The caller's request and the run it started, or `None` (V27, V47).

        One statement with an outer join rather than two reads: the request and its run are
        one answer to one question, and two round trips could straddle the moment the
        executor moves the run.

        `None` covers "no such request" *and* "not yours" *and* "its requester was deleted",
        which is V27's whole point — the caller cannot tell those apart, so the tool has one
        refusal for all of them.
        """
        result = await self._session.execute(
            select(ActionRequest, ToolRun)
            .outerjoin(ToolRun, ActionRequest.tool_run_id == ToolRun.id)
            .where(
                ActionRequest.id == action_request_id,
                # The access control, in the statement: a row that is not this caller's is
                # never fetched, and a NULL requester matches nothing, which is the
                # fail-closed direction T34's `SET NULL` needs (V27). That the refusal holds
                # is asserted by test; that it holds *here* rather than after the read is a
                # preference — see the module docstring, where the measurement is recorded.
                ActionRequest.requested_by_user_id == requester_user_id,
            )
        )
        row = result.first()
        if row is None:
            return None

        request, run = row
        return ActionResultView(
            action_request_id=request.id,
            tool_name=request.tool_name,
            status=ActionRequestStatus(request.status),
            arguments=arguments_from_context(request.approval_context or {}),
            created_at=as_utc(request.created_at),
            expires_at=as_utc(request.expires_at),
            decided_at=None if request.decided_at is None else as_utc(request.decided_at),
            run=None if run is None else _run_view(run),
        )


def _run_view(run: ToolRun) -> ActionRunView:
    """The `tool_runs` half of the join, as a value object."""
    return ActionRunView(
        tool_run_id=run.id,
        status=ToolRunStatus(run.status),
        result_summary=run.result_summary,
        created_at=as_utc(run.created_at),
        completed_at=None if run.completed_at is None else as_utc(run.completed_at),
    )


class ActionResultService:
    """Read one request, and never serve a stale PENDING (T63 — V23, V27, V32, V76).

    **Order: the requester-match first, the expiry second.** `core.approvals.expiry` describes
    its check-on-read as running *before* the render path reads the row, and for T41's card —
    which resolves the row itself — that is right. Here it is the other way round on purpose:
    `expire_if_due` takes an id and nothing else, so calling it first would let a prompt-
    injected identifier make NOA write to a request belonging to somebody the caller cannot
    even see. Reading first makes a foreign id a pure no-op — no row, no write, one refusal.

    What that costs is one poll of freshness: if a decision commits between the read and the
    update, the update finds the row no longer PENDING, skips it (which is exactly the
    property T39 relies on) and this answers with the PENDING it read. That window exists for
    any unlocked read, the next call closes it, and V23 keeps the authority in the row rather
    than in this answer.
    """

    def __init__(
        self,
        *,
        repository: ActionResultRepository,
        expiry: ActionRequestExpiryService,
    ) -> None:
        self._repository = repository
        self._expiry = expiry

    async def result_for(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
        now: datetime | None = None,
    ) -> ActionResultView | None:
        """The caller's request, with a passed deadline already made terminal (V32).

        One clock read, handed to the expiry so the status this returns and the `decided_at`
        the `UPDATE` stamped are the same moment rather than two that nearly agree
        (`core.approvals.clock` — the same rule the two decision doors follow).
        """
        moment = now_utc(now)
        view = await self._repository.get_for_requester(
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
        )
        if view is None:
            return None

        expired = await self._expiry.expire_if_due(
            action_request_id=view.action_request_id,
            now=moment,
        )
        if not expired:
            return view

        # The `UPDATE` fired, so `EXPIRED` at `moment` is what is durable — reported from the
        # write that happened rather than re-read, which would be a second round trip that
        # could disagree with it.
        return replace(view, status=ActionRequestStatus.EXPIRED, decided_at=moment)


__all__ = [
    "ActionResultRepository",
    "ActionResultService",
    "ActionResultView",
    "ActionRunView",
    "SQLActionResultRepository",
]
