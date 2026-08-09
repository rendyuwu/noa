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

**The row guard is `core.approvals.reads`, shared with T41's card.** The requester-match sits
in the `WHERE` (`select_requester_matched`) and V32's check-on-read runs after it
(`apply_due_expiry`); both live one module over because the *other* reader of an approval
request has to guard it identically, and two spellings of an access control is how it ends up
holding at one surface and not the other (V66). What is not shared is what each surface
renders — see the next paragraph.

**What the views cannot carry.** `ActionResultView` has no `reason` field and nowhere to put
one. The reason is the operator's own words, typed on the approval card, and C8 says the LLM
never authors it, never relays it and never *sees* it (V15, V43) — and this tool answers into
a transcript that persists in LibreChat's MongoDB (V26). The same is true of the preflight
evidence on `approval_context`: V17 says it is born in-process and stays out of the
transcript, so it is read for the card (V33, V35) and never for the model. Neither is filtered
out downstream; neither is ever loaded. `arguments_from_context` is the only thing this module
takes off that payload.

**The run is `tool_runs`, not `action_receipts`.** §T.63 says "receipt summary". T36 has since
built the `action_receipts` table, but T38's executor — its only writer — is still unbuilt, so
a join there answers `null` for every request and would look like a feature while being one
table's worth of nothing. `action_requests.tool_run_id` is written in the same transaction as
an approval (T37, V29), so the run row is the execution record that exists today: status,
redacted summary, timing (V47). When T38 lands and receipts start being written, the receipt
joins here beside it.
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
    select_requester_matched,
)
from core.db.lifecycle import ActionRequestStatus


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

        The statement is `core.approvals.reads.select_requester_matched` — one outer join with
        the requester-match in the `WHERE`, shared with T41's card so the access control has
        one spelling (V66). What is local to this class is the *projection*: only
        `arguments_from_context` comes off `approval_context`, and `ActionResultView` has
        nowhere to put the rest.
        """
        row = await select_requester_matched(
            self._session,
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
        )
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
            run=None if run is None else run_view(run),
        )


class ActionResultService:
    """Read one request, and never serve a stale PENDING (T63 — V23, V27, V32, V76).

    **Order: the requester-match first, the expiry second** — `core.approvals.reads`
    (`apply_due_expiry`) holds that ordering and the argument for it, shared with T41's card so
    a second reader cannot arrive at the other order (V66).
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
