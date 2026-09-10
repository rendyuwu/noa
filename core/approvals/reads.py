"""Reading one approval request back, for whoever asked for it.

Two surfaces read an `action_requests` row for someone rather than deciding it: T63's
`noa_get_action_result` answers a model (`core.approvals.results`) and T41's approval card
answers the operator in front of it (`core.approvals.card`). They must render *different*
things — the model may not be shown the preflight evidence and the card exists to show
it — but they must **guard the row identically**, and that is what lives here.

That difference is why the receipt join is a parameter and not the default. `action_receipts`
carries the same before-state one table over, so fetching it on the model's path
would put V17's evidence in that process holding nothing but a projection between it and the
transcript. The card asks for it; `core.approvals.results` does not.

**The access control is one statement.** `select_requester_matched` carries
`requested_by_user_id = :caller` in the `WHERE`, so a row that is not the caller's is never
fetched by either surface and there is no later branch that could forget to drop it. A NULL
requester — the FK is `SET NULL`, so a deleted operator leaves one behind — matches
nobody under SQL's NULL semantics, which is the fail-closed direction V27 names. Two copies of
that clause would be two places for it to be got wrong, and only one of them would be the one
someone reads.

**The expiry ordering is one function.** `apply_due_expiry` runs V32's check-on-read *after*
the requester-matched read, because `expire_if_due` takes an id and no requester: calling it
first lets a prompt-injected identifier make NOA write to a request belonging to somebody the
caller cannot see. V32 allows a surface that resolves the row itself to call it first; both
callers here decline, on purpose — reading first makes a foreign id a pure no-op, no row, no
write, one refusal. What it costs is one poll of freshness (see `apply_due_expiry`).

Nothing here reads `reason`. It is a column of its own, NULL until an operator types one into
the card, and a helper here would be the first place someone reached for it
from a path that must never see it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol, TypeVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import as_utc, now_utc
from core.approvals.expiry import ActionRequestExpiryService
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from core.db.models import ActionReceipt, ActionRequest, ToolRun


@dataclass(frozen=True)
class ActionRunView:
    """The execution an approval started, as far as it has got.

    `result_summary` is already truncated and already redacted by whoever wrote it
    (`noa_api.mcp_audit` for a READ, `core.approvals.execution` for a change, and
    `core.approvals.reaper` for one nobody finished) — nothing here re-derives it, for the reason
    `LockedActionRequest.redacted_arguments` gives one field over.

    `STARTED` with a NULL summary is a change still running: T37 opens this row inside the
    decision's transaction and T38's executor moves it when the change ends. Reporting that
    plainly is the point — a model that is told "started" tells an operator to wait, which is
    true, and a card that says the same is telling them to keep the tab open. A run that stays
    `STARTED` past the reaper's deadline becomes `FAILED` with a summary saying the outcome was
    never observed, so no reader waits forever.
    """

    tool_run_id: UUID
    status: ToolRunStatus
    result_summary: str | None
    created_at: datetime
    completed_at: datetime | None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields, for a tool result or an HTTP body."""
        return {
            "tool_run_id": str(self.tool_run_id),
            "status": self.status.value,
            "result_summary": self.result_summary,
            "created_at": self.created_at.isoformat(),
            "completed_at": None if self.completed_at is None else self.completed_at.isoformat(),
        }


def run_view(run: ToolRun) -> ActionRunView:
    """The `tool_runs` half of the join, as a value object."""
    return ActionRunView(
        tool_run_id=run.id,
        status=ToolRunStatus(run.status),
        result_summary=run.result_summary,
        created_at=as_utc(run.created_at),
        completed_at=None if run.completed_at is None else as_utc(run.completed_at),
    )


async def select_requester_matched(
    session: AsyncSession,
    *,
    action_request_id: UUID,
    requester_user_id: UUID,
    include_receipt: bool = False,
) -> tuple[ActionRequest, ToolRun | None, ActionReceipt | None] | None:
    """The caller's request, the run it started and its receipt, or `None`.

    One statement with outer joins rather than two or three reads: the request, its run and
    what that run did are one answer to one question, and separate round trips could straddle
    the moment the executor moves the run and writes the receipt in one commit.

    `None` covers "no such request" *and* "not yours" *and* "its requester was deleted", which
    is V27's whole point — the caller cannot tell those apart, so both surfaces have one
    refusal for all of them.

    That the refusal holds is asserted by test. That it holds *here* rather than after the
    read is a preference, and it was measured: moving the comparison into a Python `!=` after
    the read leaves every test green, live ones included, because `None != caller` is `True`
    so even the deleted-requester case still refuses. What the statement buys is that the
    foreign row does not exist in this process to be logged, returned by a later edit, or half
    dropped by a refactor — defence in depth, said here rather than in a test name that would
    imply otherwise (V69: a control asserted by prose is worth what the prose is worth).

    **`include_receipt` defaults to off, and that is the model path's guard**. A
    receipt's `before` half is the gate's in-process preflight evidence, which V17 keeps out of
    the transcript — so `core.approvals.results` leaves this false and the receipt is never
    *fetched* rather than fetched and then dropped by a projection somebody could widen. The
    join itself is bound by the same `WHERE` as everything else here: a receipt hangs off an
    `action_requests` row that was already requester-matched, so there is no second access
    control to get right. T36's `UNIQUE (action_request_id)` is what keeps this join from
    multiplying the row.
    """
    # Both joins hang off `ActionRequest`, so their order in the chain does not change the SQL.
    # The receipt one is added first only because the entity list has to agree with it.
    statement = (
        select(ActionRequest, ToolRun, ActionReceipt).outerjoin(
            ActionReceipt, ActionReceipt.action_request_id == ActionRequest.id
        )
        if include_receipt
        else select(ActionRequest, ToolRun)
    )
    result = await session.execute(
        statement.outerjoin(ToolRun, ActionRequest.tool_run_id == ToolRun.id).where(
            ActionRequest.id == action_request_id,
            # The access control, in the statement — see the docstring above.
            ActionRequest.requested_by_user_id == requester_user_id,
        )
    )
    row = result.first()
    if row is None:
        return None

    # One shape for both callers, so a reader that does not ask for the receipt still gets a
    # tuple it can unpack the same way — and `None` there means "not asked for", which is why
    # `core.approvals.results` never has to decide what to do with one.
    request, run, *receipt = row
    return request, run, (receipt[0] if receipt else None)


class ExpirableRequestView(Protocol):
    """What `apply_due_expiry` needs of a view: which request, and the two fields it moves."""

    @property
    def action_request_id(self) -> UUID: ...

    @property
    def status(self) -> ActionRequestStatus: ...

    @property
    def decided_at(self) -> datetime | None: ...


ViewT = TypeVar("ViewT", bound=ExpirableRequestView)


async def apply_due_expiry(
    view: ViewT,
    *,
    expiry: ActionRequestExpiryService,
    now: datetime | None = None,
) -> ViewT:
    """Make a read view terminal if its deadline has passed.

    Called *after* the requester-matched read, never before — the module docstring says why.

    What that ordering costs is one poll of freshness: if a decision commits between the read
    and this `UPDATE`, the update finds the row no longer PENDING, skips it (which is exactly
    the property T39 relies on) and the caller answers with the PENDING it read. That window
    exists for any unlocked read, the next call closes it, and V23 keeps the authority in the
    row rather than in this answer.

    The returned view reports `EXPIRED` at `now` — the moment the `UPDATE` stamped — rather
    than re-reading, which would be a second round trip that could disagree with the write
    that just happened.
    """
    moment = now_utc(now)
    expired = await expiry.expire_if_due(
        action_request_id=view.action_request_id,
        now=moment,
    )
    if not expired:
        return view

    # `replace` needs a dataclass instance; the Protocol above cannot say so, and every caller
    # passes a frozen dataclass. The cast is the narrowest way to keep the bound structural.
    return cast(
        "ViewT",
        replace(
            cast("Any", view),
            status=ActionRequestStatus.EXPIRED,
            decided_at=moment,
        ),
    )


__all__ = [
    "ActionRunView",
    "ExpirableRequestView",
    "apply_due_expiry",
    "run_view",
    "select_requester_matched",
]
