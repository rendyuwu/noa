"""The approval card's read of one request (T41 — V27, V32, V33, V35).

This is what an **operator** is shown before authorising a CHANGE. `core.approvals.results` is
what a **model** is told about the same row, and the two are separate classes for one reason:
the card exists to show the in-process preflight evidence and the model must never see it
(V17), so the difference has to be structural rather than a filter somebody remembers to apply.
`ActionResultView` has no `evidence` field; `ApprovalCardView` has no `reason` one.

**The guard is shared, the projection is not** (`core.approvals.reads`, V66). Both readers use
`select_requester_matched` — the requester-match in the `WHERE`, so a row that is not the
caller's is never fetched and a NULL requester (T34's `SET NULL`) matches nobody — and both run
V32's check-on-read *after* that matched read via `apply_due_expiry`. What each does with the
row it got is its own business, and that is the only part duplicated here.

**Provenance comes off the row, not off a join at render time** (V35). Created-at,
conversation ref, tool name and deadline are columns; the requesting identity and the LibreChat
account behind the call are on `approval_context`, persisted at gate time precisely because the
requester FK is `SET NULL` — a deleted operator would otherwise erase the identity from a
decision that was made (T33's `build_approval_context`). Every key is read through
`core.approvals.context`'s constants: this is JSONB, so a misspelt key reads as an absent one
and answers empty.

**No `reason`, and nowhere to put one.** The reason is written by a decision (T37) and read by
nothing on a render path: the card's job is to collect one, not to replay one. Leaving the
field off means a future edit that wanted to show it has to add it on purpose (C8, V15, V43).

**Writes nothing.** No `commit`, no status parameter, no statement that is not a `SELECT`. The
one write this path can cause is an expiry, and that belongs to `ActionRequestExpiryService`,
whose writer can set exactly one status (V32).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import as_utc
from core.approvals.context import (
    CONTEXT_REQUESTER_KEY,
    arguments_from_context,
    # Lived here until T38, when the executor became its second reader and it moved beside
    # `arguments_from_context` (V66). Re-exported below so this module stays the name T41's
    # card and its tests reach for.
    evidence_from_context,
)
from core.approvals.expiry import ActionRequestExpiryService
from core.approvals.reads import (
    ActionRunView,
    apply_due_expiry,
    run_view,
    select_requester_matched,
)
from core.db.lifecycle import ActionRequestStatus


@dataclass(frozen=True)
class ApprovalCardRequester:
    """Who asked for the change (V35): the operator, and the LibreChat account behind them.

    Both strings, both possibly empty, and neither is an id the card resolves anything with —
    `requested_by_user_id` is the column V27 matches against and it never reaches the browser
    (V26: the URL carries the request id and nothing else).
    """

    email: str
    librechat_user_id: str

    def as_payload(self) -> dict[str, str]:
        """JSON-native fields for the card body."""
        return {"email": self.email, "librechat_user_id": self.librechat_user_id}


@dataclass(frozen=True)
class ApprovalCardView:
    """One request as the operator who opened it may read it (V33, V35).

    Wider than `ActionResultView` by exactly two fields — `requester` and `evidence` — plus the
    `conversation_ref` column. Those three are the provenance and the before-state V35 names,
    and they are the whole reason this class exists rather than reusing the model-facing one.

    `run` is the execution an approval started, or `None`. Present here so one URL can own the
    whole lifecycle (V34): the same card that asked the question reports what the answer did.
    """

    action_request_id: UUID
    tool_name: str
    status: ActionRequestStatus
    conversation_ref: str | None
    requester: ApprovalCardRequester
    arguments: dict[str, Any]
    evidence: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    run: ActionRunView | None

    @property
    def is_pending(self) -> bool:
        """Whether this request is still answerable.

        Read by the route to decide whether to mint a CSRF token at all (V39): a live token on
        a card nobody may decide is a spare key with no door.
        """
        return self.status is ActionRequestStatus.PENDING

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the card body.

        `run` and `decided_at` are `None` rather than omitted when absent: "this change never
        ran" is an answer, and a missing key reads to the renderer as a field it forgot.

        No `reason` key. See the module docstring — the field does not exist to be serialised.
        """
        return {
            "action_request_id": str(self.action_request_id),
            "tool_name": self.tool_name,
            "status": self.status.value,
            "conversation_ref": self.conversation_ref,
            "requester": self.requester.as_payload(),
            "arguments": dict(self.arguments),
            "evidence": dict(self.evidence),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "decided_at": None if self.decided_at is None else self.decided_at.isoformat(),
            "run": None if self.run is None else self.run.as_payload(),
        }


def requester_from_context(approval_context: Mapping[str, Any]) -> ApprovalCardRequester:
    """The requesting identity as the gate persisted it (V35).

    Empty strings when the key is missing or is not an object, matching
    `arguments_from_context`'s rule one field over: a context written by something other than
    the gate cannot put a list where a mapping is expected, and a card whose provenance line is
    blank says less than one that claims an identity nobody recorded.
    """
    requester = approval_context.get(CONTEXT_REQUESTER_KEY)
    if not isinstance(requester, dict):
        return ApprovalCardRequester(email="", librechat_user_id="")

    email = requester.get("email")
    librechat_user_id = requester.get("librechat_user_id")
    return ApprovalCardRequester(
        email=email if isinstance(email, str) else "",
        librechat_user_id=librechat_user_id if isinstance(librechat_user_id, str) else "",
    )


class ApprovalCardRepository(Protocol):
    """The one read this path may make (V27)."""

    async def get_for_requester(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
    ) -> ApprovalCardView | None: ...


class SQLApprovalCardRepository:
    """`ApprovalCardRepository` over one `AsyncSession`.

    The session is the request's (`noa_api.api.deps`), and there is nothing to commit: every
    statement on this path is a `SELECT`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_requester(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
    ) -> ApprovalCardView | None:
        """The caller's request as a card, or `None` (V27, V35).

        `None` covers absent, another operator's, and one whose requester was deleted — one
        answer for all three, which is what keeps the route from being an existence oracle.
        """
        row = await select_requester_matched(
            self._session,
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
        )
        if row is None:
            return None

        request, run = row
        approval_context = request.approval_context or {}
        return ApprovalCardView(
            action_request_id=request.id,
            tool_name=request.tool_name,
            status=ActionRequestStatus(request.status),
            conversation_ref=request.conversation_ref,
            requester=requester_from_context(approval_context),
            arguments=arguments_from_context(approval_context),
            evidence=evidence_from_context(approval_context),
            created_at=as_utc(request.created_at),
            expires_at=as_utc(request.expires_at),
            decided_at=None if request.decided_at is None else as_utc(request.decided_at),
            run=None if run is None else run_view(run),
        )


class ApprovalCardService:
    """Read one request for its card, and never serve a stale PENDING (T41 — V23, V27, V32).

    Sibling of `ActionResultService`, and the same two steps in the same order: the
    requester-matched read, then V32's check-on-read (`core.approvals.reads.apply_due_expiry`,
    which holds the argument for that ordering).

    V32 says a surface that resolves the row itself *may* run the expiry first. This one
    declines: `expire_if_due` takes an id and no requester, and the id in this URL reaches the
    operator through a tool result that persists in LibreChat's MongoDB (V26) — so an
    expiry-first card would let an id its reader cannot see be written to. Reading first makes a
    foreign id a pure no-op, and the card still never shows a live PENDING past its deadline,
    because the view it returns reports the write that just happened.
    """

    def __init__(
        self,
        *,
        repository: ApprovalCardRepository,
        expiry: ActionRequestExpiryService,
    ) -> None:
        self._repository = repository
        self._expiry = expiry

    async def card_for(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
        now: datetime | None = None,
    ) -> ApprovalCardView | None:
        """The caller's card, with a passed deadline already made terminal (V32)."""
        view = await self._repository.get_for_requester(
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
        )
        if view is None:
            return None

        return await apply_due_expiry(view, expiry=self._expiry, now=now)


__all__ = [
    "ApprovalCardRepository",
    "ApprovalCardRequester",
    "ApprovalCardService",
    "ApprovalCardView",
    "SQLApprovalCardRepository",
    "evidence_from_context",
    "requester_from_context",
]
