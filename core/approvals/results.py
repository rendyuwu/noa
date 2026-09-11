"""Reading one approval request back, for the operator who asked for it.

The gate opens a request, the decision endpoint decides it, the expiry loop expires the ones nobody
answered. This is the only thing that *reads* one from the MCP side: `noa_get_action_result` is how
a model finds out what happened to a change it asked for, without the answer travelling through an
LLM claim (the row is still the authority) and without it reaching past the operator who opened it.

**A fourth class, and the reason is the one that split the first three.** `repository` writes
PENDING, `decisions` writes APPROVED/DENIED under a lock, `expiry` writes EXPIRED and nothing
else. This one writes *nothing at all*: it has no `commit`, no status parameter and no
statement that is not a `SELECT`. The one write on this path — expiring a stale PENDING so a
GET cannot serve it — is delegated to `ActionRequestExpiryService`, whose writer can set
exactly one status.

**The row guard is `core.approvals.reads`, shared with the approval card.** The requester-match sits
in the `WHERE` (`select_requester_matched`) and the TTL check-on-read runs after it
(`apply_due_expiry`); both live one module over because the *other* reader of an approval
request has to guard it identically, and two spellings of an access control is how it ends up
holding at one surface and not the other. What is not shared is what each surface
renders — see the next paragraph.

**What the views cannot carry.** `ActionResultView` has no `reason` field and nowhere to put one.
The reason is the operator's own words, typed on the approval card, and the reason rule says the LLM
never authors it, never relays it and never *sees* it — and this tool answers into a transcript that
persists in LibreChat's MongoDB. The same is true of the preflight evidence on `approval_context`:
it is born in-process and stays out of the transcript, so it is read for the card and never for the
model. Neither is filtered out downstream; neither is ever loaded. `arguments_from_context` is the
only thing this module takes off that payload.

**The run is `tool_runs`, not `action_receipts`.** The action-result tool's design says "receipt
summary". The run row is what this reads: `action_requests.tool_run_id` is written in the same
transaction as an approval (approve means async run, state in DB), so it exists for every approved
change, and it carries status, redacted summary and timing.

The executor has since built the receipt's writer, so a receipt now exists for every approved change
that reached a terminal state — and joining it *here* is still the action-result tool's own decision
rather than something the executor did on its behalf. What a model may be told is narrower than what
the card shows (requester-match), and a receipt's before-state is the gate's in-process preflight,
which stays out of the transcript. So the receipt joins this view when someone decides which of its
halves a model may see; the approval card is where it renders first, and it has since done so.

That decision is now expressible rather than merely stated: `select_requester_matched` takes an
`include_receipt` flag, the card passes it and this reader does not. So the separation is a
statement that was never issued, not a field this class remembers to drop.

**And the receipt has since been split rather than let in.** A change that timed out leaves NOA
holding no reading — the runner records `verification: "unavailable"` rather than claiming a
re-read it never made — and a model told only `status: failed` reports to the operator that the
change did not happen, which is a claim NOA's own record does not support. So two values do
cross: `verification` and `verification_cause`, and they cross **as a SQL projection** rather
than as a joined row (`select_requester_matched_with_change_verification`). The halves stay on
the far side of the statement. That is the same argument the `include_receipt` split makes, held
one level finer: the receipt's `before` is the gate's in-process preflight evidence, and on a WHM
account WHM echoes the operator's typed reason back inside it as `suspendreason`, so a join taken
for convenience here would hand a transcript the one field the whole reason rule exists to keep
out of one. Two columns, named in the statement. Never the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import as_utc
from core.approvals.context import arguments_from_context
from core.approvals.expiry import ActionRequestExpiryService
from core.approvals.reads import (
    ActionRunView,
    apply_due_expiry,
    run_view,
    select_requester_matched_with_change_verification,
)
from core.db.lifecycle import ActionRequestStatus


@dataclass(frozen=True)
class ActionResultView:
    """One approval request as its requester may see it.

    Deliberately narrower than `LockedActionRequest`: that one is what a *decision* needs and
    carries the whole `approval_context`; this is what a model may be told. No `reason`, no
    `evidence`, no requester identity — see the module docstring for why each is absent rather
    than filtered.

    The two `change_verification*` fields are the one thing the receipt contributes, and they
    are NOA's own vocabulary — a state out of a fixed set and a named cause — never a value
    read off a target system. Anything else from a receipt belongs on the approval card, which
    an operator reads and a transcript does not keep.
    """

    action_request_id: UUID
    tool_name: str
    status: ActionRequestStatus
    arguments: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    run: ActionRunView | None
    # Whether the runner could measure what its change did, and the named reason when it could
    # not. Two strings off the receipt's delta and nothing else off the receipt — see
    # `as_payload` for why the pair is here at all, and the module docstring for the fence that
    # keeps it at two.
    change_verification: str | None = None
    change_verification_cause: str | None = None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the tool result.

        `run` is `None` rather than omitted when there is none: "this change never ran" is an
        answer, and a missing key reads to a model as a field it forgot to look at. The two
        verification fields follow that rule for the same reason.

        **The verification pair is additive, and `status` keeps its old meaning.** A model that
        knows only this view's older shape reads `status` and the run exactly as before; one
        that reads the new keys learns the thing `status` cannot say. Redefining `status` would
        have been the shorter diff and the wrong one — it is the approval request's lifecycle,
        answered from `action_requests.status` by the verdict-from-status rule, and overloading
        it with what the *change* did would put two facts in one field.

        Why the pair exists: a mutation that timed out leaves NOA holding no reading, and the
        runner says exactly that rather than guessing (`verification: "unavailable"`). Without
        these keys the model sees a failed run, tells the operator the change did not happen,
        and NOA's own record says neither confirmed nor refuted. The incident that produced this
        was a WHM suspension that completed while the caller was told it had failed.
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
            "change_verification": self.change_verification,
            "change_verification_cause": self.change_verification_cause,
        }


class ActionResultRepository(Protocol):
    """The one read this path may make."""

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
        """The caller's request and the run it started, or `None`.

        The statement is `core.approvals.reads`' — the requester-match in the `WHERE`, written
        once and shared with the approval card so the access control has one spelling. What is
        local to this class is the *projection*: only `arguments_from_context` comes off
        `approval_context`, and `ActionResultView` has nowhere to put the rest.

        **The receipt row is still never fetched, and now two of its values are.**
        `select_requester_matched`'s `include_receipt` — which loads `receipt_data` whole,
        `before` half included — is what the card passes and what this path must never pass: the
        before-state is the gate's in-process preflight evidence, and on a WHM account it
        carries the operator's own typed reason back as `suspendreason`. So this asks instead
        for a statement that lifts the delta's two verification fields **as text, in SQL**. The
        halves are not filtered downstream; they do not arrive. Widening that is adding a column
        to a statement, not forgetting to drop a field.
        """
        row = await select_requester_matched_with_change_verification(
            self._session,
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
        )
        if row is None:
            return None

        request, run, verification, verification_cause = row
        return ActionResultView(
            action_request_id=request.id,
            tool_name=request.tool_name,
            status=ActionRequestStatus(request.status),
            arguments=arguments_from_context(request.approval_context or {}),
            created_at=as_utc(request.created_at),
            expires_at=as_utc(request.expires_at),
            decided_at=None if request.decided_at is None else as_utc(request.decided_at),
            run=None if run is None else run_view(run),
            change_verification=verification,
            change_verification_cause=verification_cause,
        )


class ActionResultService:
    """Read one request, and never serve a stale PENDING.

    **Order: the requester-match first, the expiry second** — `core.approvals.reads`
    (`apply_due_expiry`) holds that ordering and the argument for it, shared with the approval card
    so a second reader cannot arrive at the other order.
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
        """The caller's request, with a passed deadline already made terminal.

        One clock read, handed to the expiry so the status this returns and the `decided_at`
        the `UPDATE` stamped are the same moment rather than two that nearly agree
        (`core.approvals.clock` — the same rule the two decision doors follow).
        """
        view = await self._repository.get_for_requester(
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
        )
        if view is None:
            return None

        return await apply_due_expiry(view, expiry=self._expiry, now=now)


__all__ = [
    "ActionResultRepository",
    "ActionResultService",
    "ActionResultView",
    "ActionRunView",
    "SQLActionResultRepository",
]
