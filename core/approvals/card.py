"""The approval card's read of one request.

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

**Provenance comes off the row, not off a join at render time**. Created-at,
conversation ref, tool name and deadline are columns; the requesting identity and the LibreChat
account behind the call are on `approval_context`, persisted at gate time precisely because the
requester FK is `SET NULL` — a deleted operator would otherwise erase the identity from a
decision that was made (T33's `build_approval_context`). Every key is read through
`core.approvals.context`'s constants: this is JSONB, so a misspelt key reads as an absent one
and answers empty.

**The receipt joins here and nowhere else yet** (T42(b) — V34, V46). T38's executor writes what
an approved change did, in two halves DECISIONS §6.5 refuses to let collapse: the before-state
the operator authorised against, and what the change answered. This is where those render, and
`select_requester_matched` is asked for them by this repository only — the model-facing reader
next door leaves the join off, because a receipt's `before` half *is* V17's preflight evidence
and the point of that separation is that it is never loaded on the transcript's path.

That this one is a join at render time while the provenance above is not is the difference
between the two FKs, not an inconsistency: `requested_by_user_id` is `SET NULL`, so the identity
had to be copied onto `approval_context` before it could vanish, whereas
`action_receipts.action_request_id` is NOT NULL with `UNIQUE` on it — the row cannot
detach from the request, and there is at most one of it.

**No `reason`, and nowhere to put one.** The reason is written by a decision and read by
nothing on a render path: the card's job is to collect one, not to replay one. Leaving the
field off means a future edit that wanted to show it has to add it on purpose.

**Writes nothing.** No `commit`, no status parameter, no statement that is not a `SELECT`. The
one write this path can cause is an expiry, and that belongs to `ActionRequestExpiryService`,
whose writer can set exactly one status.
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
    # `arguments_from_context`. Re-exported below so this module stays the name T41's
    # card and its tests reach for.
    evidence_from_context,
)
from core.approvals.execution import (
    # The keys T38's writer puts in `receipt_data`, read here rather than respelled: a misspelt
    # key in JSONB reads as an absent one, and the constants' own comment names this card as one
    # of the three readers they exist for. The import is of five strings — nothing on this
    # read path executes anything.
    RECEIPT_AFTER_KEY,
    RECEIPT_BEFORE_KEY,
    RECEIPT_DELTA_KEY,
    RECEIPT_ERROR_CODE_KEY,
    RECEIPT_OK_KEY,
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
    """Who asked for the change: the operator, and the LibreChat account behind them.

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
class ApprovalCardReceipt:
    """What the change actually did, in the two halves it was written as.

    DECISIONS §6.5 is the requirement this shape serves: an operator reads back the state they
    authorised against **and** what the change did to it, each on its own, never collapsed into
    a single "done". So `before` and `after` are two fields here and two sections on the card —
    a single "outcome" string would be exactly the collapse that was refused.

    `ok` is lifted off the receipt rather than inferred from `after` being non-empty: the writer
    took it from the runner's own envelope (`build_receipt`), and re-deciding it here would be a
    second answer to whether the change worked. `error_code` is `None` when the receipt carries
    none, matching the writer's rule that an absent field beats an empty one.

    **Neither half is re-derived and neither is re-redacted.** `after` was redacted by the
    writer on the way in and `before` is the gate's own `approval_context` evidence;
    this class copies. A reader that redacted again would be a second redaction policy, and the
    day the two disagree is the day one of them is wrong.

    **`delta` is the third half, and it is the only one an operator can read as a sentence.**
    The other two are a preflight reading and a tool envelope, written minutes apart in two
    vocabularies that meet only on identity — the keys they share carry the same value in both,
    and the field that moved is never under a shared key — so "what did this change actually
    move" is a question neither half answers and nothing downstream can compute. The runner that
    moved it states it instead (`core.approvals.delta`). `None` when the writer stored none,
    which is a claim rather than a gap: nothing was measured, so nothing is stated.

    Copied, not re-parsed. The delta is stored as JSONB by one writer that already validated and
    redacted it, and rebuilding a `ChangeDelta` from that row here would be a second reading of
    the same bytes — the same argument the two halves above are copied by, and the day the two
    readings disagree is the day one of them is wrong.

    No timestamp. `action_receipts.created_at` exists on the row and is deliberately not carried:
    `tool_runs` already reports when the run started and finished, and a third stamp for
    one moment is the third truth T34 refused when it dropped its own duplicate columns.
    """

    ok: bool
    before: dict[str, Any]
    after: dict[str, Any]
    error_code: str | None
    delta: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the card body.

        `error_code` and `delta` are `None` rather than omitted, for the reason `run` is: this
        body is parsed by a renderer that switches on the field, and a missing key reads as one
        it forgot. That is the opposite of the stored receipt's rule, and deliberately so — the
        row omits a key nothing measured so that the absence is a fact, and this turns that
        absence into the `null` a renderer branches on.
        """
        return {
            RECEIPT_OK_KEY: self.ok,
            RECEIPT_BEFORE_KEY: dict(self.before),
            RECEIPT_AFTER_KEY: dict(self.after),
            RECEIPT_ERROR_CODE_KEY: self.error_code,
            RECEIPT_DELTA_KEY: None if self.delta is None else dict(self.delta),
        }


@dataclass(frozen=True)
class ApprovalCardView:
    """One request as the operator who opened it may read it.

    Wider than `ActionResultView` by exactly three fields — `requester`, `evidence` and
    `receipt` — plus the `conversation_ref` column. The first two are the provenance and the
    before-state V35 names; the third is what the change did, and all three are the reason this
    class exists rather than reusing the model-facing one.

    `run` is the execution an approval started, or `None`. `receipt` is what that execution
    recorded, or `None`. Both are present here so one URL can own the whole lifecycle **through
    the receipt**: the same card that asked the question reports what the answer did.
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
    receipt: ApprovalCardReceipt | None

    @property
    def is_pending(self) -> bool:
        """Whether this request is still answerable.

        Read by the route to decide whether to mint a CSRF token at all: a live token on
        a card nobody may decide is a spare key with no door.
        """
        return self.status is ActionRequestStatus.PENDING

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the card body.

        `run`, `receipt` and `decided_at` are `None` rather than omitted when absent: "this
        change never ran" and "nothing recorded what it did" are answers, and a missing key
        reads to the renderer as a field it forgot.

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
            "receipt": None if self.receipt is None else self.receipt.as_payload(),
        }


def requester_from_context(approval_context: Mapping[str, Any]) -> ApprovalCardRequester:
    """The requesting identity as the gate persisted it.

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


def receipt_from_data(receipt_data: Any) -> ApprovalCardReceipt:
    """One `action_receipts.receipt_data` payload as the card reads it.

    Called only when a receipt **row** exists, which is what "the change recorded an outcome"
    means — so this never answers `None`. What it is permissive about is the payload's shape:
    `receipt_data` is unversioned JSONB, and a half that is not an object renders as
    "nothing recorded" rather than raising, because a `KeyError` in front of an operator is a
    blank iframe and V38 says that is not an acceptable state.

    Every key goes through the writer's own constants, so a rename there fails the import rather
    than quietly reading as an absent key.

    `ok` is `True` only for a literal `True`, the same comparison `build_receipt` makes when it
    lifts the field off the runner's envelope: a truthy string on this row is a payload nothing
    NOA wrote, and reading it as success would be the one direction that must not fail open.
    """
    data = receipt_data if isinstance(receipt_data, dict) else {}
    error_code = data.get(RECEIPT_ERROR_CODE_KEY)
    delta = data.get(RECEIPT_DELTA_KEY)
    return ApprovalCardReceipt(
        ok=data.get(RECEIPT_OK_KEY) is True,
        before=_receipt_half(data.get(RECEIPT_BEFORE_KEY)),
        after=_receipt_half(data.get(RECEIPT_AFTER_KEY)),
        # Empty string reads as no code: the writer omits the key when there is none, and a
        # blank one on the card would be a labelled row saying nothing.
        error_code=error_code if isinstance(error_code, str) and error_code else None,
        # `None` for an absent key *and* for a half that is not an object, which is the two
        # halves' rule one key over — except that here an empty object is `None` too, because
        # the writer omits the key rather than storing an empty delta, so `{}` on this row is a
        # payload nothing NOA wrote and "nothing was measured" is the reading that cannot be
        # wrong.
        delta=dict(delta) if isinstance(delta, dict) and delta else None,
    )


def _receipt_half(value: Any) -> dict[str, Any]:
    """One half of a receipt, or an empty mapping — `arguments_from_context`'s rule, one table
    over: a payload written by something other than T38's two writers cannot put a list where
    an object belongs and make the card raise for it."""
    return dict(value) if isinstance(value, dict) else {}


class ApprovalCardRepository(Protocol):
    """The one read this path may make."""

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
        """The caller's request as a card, or `None`.

        `None` covers absent, another operator's, and one whose requester was deleted — one
        answer for all three, which is what keeps the route from being an existence oracle.

        `include_receipt=True` is this surface's half of the split `core.approvals.reads`
        describes: the card is where a receipt renders, and the model-facing reader next
        door leaves it off so V17's before-state is never loaded on that path.
        """
        row = await select_requester_matched(
            self._session,
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
            include_receipt=True,
        )
        if row is None:
            return None

        request, run, receipt = row
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
            receipt=None if receipt is None else receipt_from_data(receipt.receipt_data),
        )


class ApprovalCardService:
    """Read one request for its card, and never serve a stale PENDING.

    Sibling of `ActionResultService`, and the same two steps in the same order: the
    requester-matched read, then V32's check-on-read (`core.approvals.reads.apply_due_expiry`,
    which holds the argument for that ordering).

    V32 says a surface that resolves the row itself *may* run the expiry first. This one
    declines: `expire_if_due` takes an id and no requester, and the id in this URL reaches the
    operator through a tool result that persists in LibreChat's MongoDB — so an
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
        """The caller's card, with a passed deadline already made terminal."""
        view = await self._repository.get_for_requester(
            action_request_id=action_request_id,
            requester_user_id=requester_user_id,
        )
        if view is None:
            return None

        return await apply_due_expiry(view, expiry=self._expiry, now=now)


__all__ = [
    "ApprovalCardReceipt",
    "ApprovalCardRepository",
    "ApprovalCardRequester",
    "ApprovalCardService",
    "ApprovalCardView",
    "SQLApprovalCardRepository",
    "evidence_from_context",
    "receipt_from_data",
    "requester_from_context",
]
