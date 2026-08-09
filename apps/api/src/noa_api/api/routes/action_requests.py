"""The approval card's routes: read one request, then approve or deny it (T37, T41, I.embed).

**This is the only door on the authorization** (C18, V22). A held MCP bearer token can open
an `action_requests` row; what it cannot do is decide one, because deciding happens here —
behind the `noa_session` cookie, from a document on NOA's own origin, with a server-minted
CSRF token. Nothing on this path passes through the LLM, and nothing on the LLM's path
reaches this module.

**The `GET` is the card, and it is where the CSRF token comes from** (T41, T46). One request in,
one card out: the provenance V35 names, the gate-time before-state (C9, V17), the redacted
arguments, the receipt once the change has run (T42(b), V46) — and, when the request is still
PENDING, a freshly minted token for the two POSTs below. One URL through the whole lifecycle,
question and answer alike, is V34.

There is no minting *route*: a token that arrived separately from the thing it authorises
is a token a page could hold without ever having been allowed to read the request, and the read
is where V27's requester-match happens. `csrf` is `null` for anything already terminal, because
a live token on a card nobody may decide is a spare key with no door (V39).

Reading is a strictly weaker capability than deciding and is wired that way rather than
promised: the `GET` holds `ApprovalCardService`, whose repository has no `commit` and no
statement that is not a `SELECT`, while the POSTs hold `ActionDecisionService`, the one writer
of a terminal status. Sharing a router does not share a writer.

**The reason is born here.** C8 puts it in exactly one place: an operator types it into the
approval card and it arrives in this body. A CHANGE tool's schema carries no reason parameter
of any name and the gate refuses one that shows up anyway
(`noa_api.mcp_tools.change_gate.assert_no_reason_argument`), so by the time a reason exists
at all, it exists because a human wrote it at approve time (V15, V43).

**Order of checks, and why.** CSRF first, then the reason, then the row lock:

- CSRF before anything else, so a forged cross-site POST never reaches the database. The
  token is bound to `(user_id, action_request_id)`, and the only way to hold one is to have
  been served the card — which already required passing V27's requester-match.
- Blank reason before the lock (V15), so a submit that cannot succeed does not queue behind
  someone else's transaction.
- Everything after that is inside `SELECT … FOR UPDATE` and belongs to
  `core.approvals.decisions` — the ordering lives in the service, in one place, rather than
  being re-established by each of these two handlers.

Neither ordering leaks existence: a caller without a valid CSRF token gets 403 for every id
alike, and a caller with one already knows the request exists because they were shown it.

`reason` is bounded here and only here — `action_requests.reason` is unbounded `Text` on
purpose (T34: truncating the field that authorises a change is worse than storing a long
one), so the endpoint is where "long" becomes "too long". Over the bound is a 422 through the
shared envelope; *blank* is deliberately not a pydantic constraint, because `min_length=1`
would answer 422 and V15 names 409 `change_reason_required`.

Failures raise `ActionDecisionError` subclasses; `api.errors` owns the status mapping and the
body shape, so no handler here builds an `HTTPException`.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from core.approvals.csrf import mint_decision_csrf_token, verify_decision_csrf_token
from core.approvals.decisions import ApprovalOutcome, DenialOutcome
from core.approvals.errors import ActionRequestNotFoundError
from core.db.lifecycle import ActionRequestStatus
from noa_api.api.deps import (
    ActionDecisionServiceDep,
    ApprovalCardServiceDep,
    SessionUserDep,
    SettingsDep,
)

# Long enough that no operator writing a real justification hits it, short enough that a body
# cannot be used to push megabytes into a column with no length of its own (T34).
MAX_REASON_LENGTH: Final = 2000

router = APIRouter(prefix="/action-requests", tags=["action-requests"])


class DecisionRequest(BaseModel):
    """What an approval card POSTs (I.embed).

    Two fields, and neither is a status: V23 answers "may this run?" from the row, so there
    is nowhere in this body to claim a request is already approved. `action_request_id` is
    not here either — it is the path, and a body copy would be a second answer to which
    request is being decided.
    """

    # No `min_length`: blank is a 409 with V15's own code, decided in the service, not a 422
    # about a malformed body. See the module docstring.
    reason: str = Field(max_length=MAX_REASON_LENGTH)
    # Server-minted, signed, session-bound (V39). Carried in the body rather than a hidden
    # form field because the in-frame POST is a JS `fetch` — the sandbox at LibreChat's pin
    # omits `allow-forms`, so a native form submit dies silently in the frame (V80, R13).
    csrf: str


class ApprovalResponse(BaseModel):
    """202 body (V29). The run to poll, not the run's result."""

    action_request_id: str
    tool_run_id: str


class DenialResponse(BaseModel):
    """Denial body. No `tool_run_id`, and nowhere to put one: nothing ran."""

    action_request_id: str
    status: str


class ApprovalCardResponse(BaseModel):
    """200 body for the card (T41, T42 — V34, V35, V39, V46).

    Shaped by `ApprovalCardView.as_payload()` rather than re-listed field by field: two
    spellings of one payload is one that can disagree, and the view is where the decision about
    what an operator may see was made. `dict[str, Any]` on `requester`/`arguments`/`evidence`
    for the same reason — the arguments and the preflight evidence are a CHANGE tool's own
    shapes, and a model that flattened them here would have to be widened by every tool.

    No `reason` field, and `ApprovalCardView` has none to serialise (C8, V15, V43).
    """

    action_request_id: str
    tool_name: str
    status: str
    conversation_ref: str | None
    requester: dict[str, Any]
    arguments: dict[str, Any]
    evidence: dict[str, Any]
    created_at: str
    expires_at: str
    decided_at: str | None
    run: dict[str, Any] | None
    # What the change did, once something recorded it (T38's executor or its reaper — V46).
    # `None` until then, and the two halves stay two keys inside it: DECISIONS §6.5 refuses a
    # single "done", and a body that flattened them would make the card's render a choice
    # rather than the contract's (T42(b)).
    receipt: dict[str, Any] | None
    # `None` once the request is terminal: there is nothing left to authorise, so there is no
    # token to hold (V39). The card reads this to decide whether to render live buttons at all.
    csrf: str | None


@router.get("/{action_request_id}", response_model=ApprovalCardResponse)
async def read_card(
    action_request_id: UUID,
    settings: SettingsDep,
    current_user: SessionUserDep,
    cards: ApprovalCardServiceDep,
) -> ApprovalCardResponse:
    """One approval card, for the operator who opened the request (T41 — V27, V32, V35, V39).

    `SessionUserDep` is the access control's first half and V6's row re-read: a disabled
    operator loses the card on their next request rather than at cookie expiry. The second half
    is the requester-match, and it is inside the statement — a request that is not this
    caller's is never fetched (`core.approvals.reads`).

    404 for absent, another operator's, *and* one whose requester was deleted, through the same
    class the decision doors raise, so all four surfaces answer with one body. V27 bounds that
    at the body and not the status: a code that varied by cause would be a 403 spelled
    differently, and only `request_id` may differ between two of these responses (V73).

    The deadline is checked on read (V32), so a card past its TTL renders `EXPIRED` with no
    token rather than a live Approve button over a request the decision door would refuse.
    """
    card = await cards.card_for(
        action_request_id=action_request_id,
        requester_user_id=current_user.user_id,
    )
    if card is None:
        raise ActionRequestNotFoundError(
            f"no `action_requests` row {action_request_id} for requester {current_user.user_id}"
        )

    # Minted here, from the identity the cookie resolved to and the id in the path — never from
    # anything in the request body. The token is bound to both, so it authorises this operator
    # on this card and nothing else (V39).
    csrf = (
        mint_decision_csrf_token(
            settings=settings,
            user_id=current_user.user_id,
            action_request_id=card.action_request_id,
        )
        if card.is_pending
        else None
    )
    return ApprovalCardResponse(**card.as_payload(), csrf=csrf)


def _authorize(
    *,
    settings: SettingsDep,
    current_user: SessionUserDep,
    action_request_id: UUID,
    payload: DecisionRequest,
) -> None:
    """The checks both handlers share, before either touches a row (V22, V39).

    A function rather than a dependency: it needs the path parameter *and* the body, and a
    dependency that reached for the body would parse it twice. Shared so the two routes
    cannot drift into checking different things (V66) — which, on a decision endpoint, would
    mean one of them is unguarded.
    """
    verify_decision_csrf_token(
        settings=settings,
        token=payload.csrf,
        user_id=current_user.user_id,
        action_request_id=action_request_id,
    )


@router.post(
    "/{action_request_id}/approve",
    response_model=ApprovalResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def approve(
    action_request_id: UUID,
    payload: DecisionRequest,
    settings: SettingsDep,
    current_user: SessionUserDep,
    decisions: ActionDecisionServiceDep,
) -> ApprovalResponse:
    """Authorise the change and start its run (V15, V27, V28, V29).

    202, not 200: the decision is durable when this returns, but the change has not run yet.
    The embed polls `tool_run_id` to a terminal state (V29, T42) — the state lives in the
    database, never in this connection.

    Refusals: 403 (CSRF), 409 `change_reason_required` (V15), 404 for absent *or* another
    operator's request (V27), 409 already decided (V28), 409 expired (V32).
    """
    _authorize(
        settings=settings,
        current_user=current_user,
        action_request_id=action_request_id,
        payload=payload,
    )

    outcome: ApprovalOutcome = await decisions.approve(
        action_request_id=action_request_id,
        caller_user_id=current_user.user_id,
        reason=payload.reason,
    )
    return ApprovalResponse(
        action_request_id=str(outcome.action_request_id),
        tool_run_id=str(outcome.tool_run_id),
    )


@router.post("/{action_request_id}/deny", response_model=DenialResponse)
async def deny(
    action_request_id: UUID,
    payload: DecisionRequest,
    settings: SettingsDep,
    current_user: SessionUserDep,
    decisions: ActionDecisionServiceDep,
) -> DenialResponse:
    """Refuse the change (V15, V27, V28).

    200, not 202: a denial is complete when it commits. Nothing was started, so there is
    nothing to poll.

    A reason is required here too, and that is T37's call on the question T34 left open. The
    card has one reason box either way; the audit trail needs to explain a refused change as
    much as an approved one; and requiring it on both is what makes the database CHECK
    expressible at all (`ck_action_requests_decided_reason`). An operator who does not want
    to type one still has a safe exit — leaving the request alone expires it (V32), and an
    expiry carries no reason precisely because nobody gave one.
    """
    _authorize(
        settings=settings,
        current_user=current_user,
        action_request_id=action_request_id,
        payload=payload,
    )

    outcome: DenialOutcome = await decisions.deny(
        action_request_id=action_request_id,
        caller_user_id=current_user.user_id,
        reason=payload.reason,
    )
    return DenialResponse(
        action_request_id=str(outcome.action_request_id),
        status=ActionRequestStatus.DENIED.value,
    )


__all__ = [
    "MAX_REASON_LENGTH",
    "ApprovalCardResponse",
    "ApprovalResponse",
    "DecisionRequest",
    "DenialResponse",
    "router",
]
