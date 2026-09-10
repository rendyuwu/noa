"""Refusals from the CHANGE approval gate and from a decision on one.

**Two trees, not one**, and the split is who is being refused.

`ChangeGateError` is raised on the MCP tool path, before a
change has been submitted for approval at all. Every message in it ends in "nothing was
changed", and the base maps to 503.

`ActionDecisionError` is raised on the HTTP decision
path, against a request that already exists. Sharing one tree would mean an operator whose
approval arrived a minute late reading "this change could not be submitted for approval",
which is a sentence about a different moment, and would put a 404 and a 409 under a base
that means 503.

`ChangeGateError` — one tree, three refusals, and the split is what the caller can do about it:

- `ChangeGateUnavailableError` — NOA could not record the request. The change did not
  happen and must not; retrying is the remedy.
- `ChangeReasonForbiddenError` — a CHANGE tool tried to hand the gate an argument that
  reads as a reason. Not an operator problem at all: it is the gate refusing to let the
  reason boundary be crossed from the inside.
- `ChangeEvidenceRequiredError` — the gate was opened with no in-process preflight evidence,
  so the approval card would ask an operator to authorise a change it cannot describe.
- `ChangeGateBranchUnavailableError` — the request was written and then the approval surface
  could not be shaped, because NOA selected one of the response-shape branches it has not built.

`NoaError` rather than a bare exception for the usual two reasons: `sanitize_tool_errors`
passes a `NoaError`'s own `error_code` through to the model instead of collapsing it into
`tool_execution_failed`, so the code that names the fix survives the boundary; and
`noa_api.api.errors` maps every class here to a status, so the decision endpoints raise rather than
build responses. All of them are mapped explicitly — a subclass-tree test per tree asserts
none falls through to the 503 default.

Messages are operator-safe: they say what NOA declined to do, never which column, key
or argument was involved. That detail rides in `detail`, which is logs only.

One of these messages is read by an operator staring at an approval card, so they say what
to do next: retry, or reload, or nothing at all.
"""

from __future__ import annotations

from core.errors import NoaError


class ChangeGateError(NoaError):
    """Base: a CHANGE was not admitted to the approval gate, so it did not run."""

    error_code: str = "change_gate_failed"
    message: str = "This change could not be submitted for approval. Nothing was changed."


class ChangeGateUnavailableError(ChangeGateError):
    """The pending request could not be written.

    Fail-closed, and the same argument the tool-run writer made for its opening write:
    "may this run?" is answered from a row, so a gate that let the tool proceed without one
    would leave the invariant asserted by prose and held by nothing — the inert pin's shape.
    """

    error_code: str = "change_gate_unavailable"
    message: str = (
        "NOA could not record this change for approval and will not run it. Try again; "
        "contact an administrator if this continues."
    )


class ChangeReasonForbiddenError(ChangeGateError):
    """A gate call carried an argument that reads as a reason.

    The reason is born when an operator types it into the approval card and nowhere else.
    A CHANGE tool whose arguments carry one has either accepted a reason from the model or
    authored one itself, and both are forbidden — so the request is refused
    rather than written with the offending key quietly dropped, which would leave the tool's
    schema wrong and nothing red.
    """

    error_code: str = "change_reason_forbidden"
    message: str = (
        "This change could not be submitted for approval. The reason for a change is typed "
        "by the operator on the approval card and cannot be supplied with the request."
    )


class ChangeEvidenceRequiredError(ChangeGateError):
    """A gate call carried no preflight evidence.

    The preflight runs in-process inside the CHANGE call precisely so the card can show a
    before-state the model never touched. A gate opened without one produces a card that
    asks for authorisation and describes nothing, which is the state the card's provenance
    exists to prevent.
    """

    error_code: str = "change_evidence_required"
    message: str = (
        "This change could not be submitted for approval because NOA gathered no "
        "before-state for it. Contact an administrator if this continues."
    )


class ChangeGateBranchUnavailableError(ChangeGateError):
    """The approval surface was asked for in a branch NOA has not built.

    `build_change_gate_response()` has three branches — link-out text, UI resource, and
    elicitation — and only the first two exist. The third is declared rather than omitted so
    the upgrade path is visible in the code, and declared branches that are not built have to
    refuse rather than fall through to something that *looks* like an approval surface.

    Not reachable from anything a client sends: the branch is a module constant selected by
    the live render-gate run, never a tool argument. Reaching this means NOA's own wiring points
    at a branch with no implementation behind it, which is why the message says nothing about
    branches — an operator can do nothing with that word.

    Sibling of the other two "NOA's bug" refusals rather than of
    `ChangeGateUnavailableError`: the PENDING row was written by then, so retrying the tool
    call would open a *second* request for the same change. The remedy is a fix, not a retry,
    and the message says so.
    """

    error_code: str = "change_gate_branch_unavailable"
    message: str = (
        "NOA recorded this change for approval but could not produce the approval card, so "
        "nothing was changed. Contact an administrator."
    )


class ActionDecisionError(NoaError):
    """Base: a decision on an existing request was refused.

    Sibling of `ChangeGateError`, not a subclass — see the module docstring. Nothing raises
    this class directly; it exists so `noa_api.api.errors` can map the tree and so a
    subclass added later takes a refusal status rather than the unclassified 503.
    """

    error_code: str = "action_decision_failed"
    message: str = "That decision could not be recorded."


class ActionRequestNotFoundError(ActionDecisionError):
    """No such request, *or* not this operator's.

    One class for both, because requester-match makes a mismatch a 404 rather than a 403: an
    operator who is told "forbidden" has been told the request exists. The requester-match is the
    access control on this surface (the table dropped `decided_by_user_id` for the same reason —
    the decider is the requester), so "yours and absent" and "someone else's" have to be
    indistinguishable from outside, down to the response body.

    The requester FK is `SET NULL`, so a deleted operator's request matches nobody and
    lands here too. That is the fail-closed direction.

    **A third cause joined these two on the admin side** (the admin API's `/admin/action-requests`
    rows): an id that is not a UUID at all. That surface is behind `require_admin` and does no
    requester-match, so nothing there is being hidden — the shared 404 is there because a 422
    would describe what the path validator accepts rather than what exists, which is the call
    the action-result tool made. Same class rather than a second code, because "no
    such approval request" is one fact and two spellings of it would be one that can drift.
    """

    error_code: str = "action_request_not_found"
    message: str = "That approval request does not exist, or it is not yours to decide."


class ActionReceiptNotFoundError(ActionDecisionError):
    """The request is real and carries no receipt.

    Distinct from `ActionRequestNotFoundError` above, and the distinction is the whole point:
    "there is no such request" and "that decision produced no run" are two different things to
    tell an administrator, and only one of them is a dead link. Denied, expired and still-pending
    requests all land here by design — a receipt exists only where an approval started a run
    (the receipt table's `UNIQUE`, the executor's writer).

    404 rather than 204 or an empty body, because the panel navigates to this address from a
    `hasReceipt` bit that may have been true when the list was drawn: an explicit refusal with its
    own code is what lets that stale link say what happened instead of rendering blank.
    """

    error_code: str = "action_receipt_not_found"
    message: str = "That approval request has no receipt: no run was started for it."


class ActionRequestAlreadyDecidedError(ActionDecisionError):
    """The request left PENDING before this decision reached the lock.

    Exactly one `pending → decided` transition is permitted, under the row lock. The loser of a
    double-click gets
    this, and so does a second operator's tab that had the card open — the first answer
    stands, and the second is refused rather than silently overwriting it.

    409 rather than 404: the request is real and the caller may decide requests in general,
    just not this one any more. The remedy is to reload the card and read the decision.
    """

    error_code: str = "action_request_already_decided"
    message: str = "That approval request has already been decided. Reload to see the outcome."


class ActionRequestExpiredError(ActionDecisionError):
    """The request's TTL passed before anyone answered.

    Raised by the decision path's check-on-read, which also makes the row terminal rather
    than leaving something that still reads PENDING. Distinct from
    `ActionRequestAlreadyDecidedError` because the remedy differs: nobody said no here, so
    the change can simply be asked for again.
    """

    error_code: str = "action_request_expired"
    message: str = "That approval request expired before it was answered. Ask for the change again."


class ChangeReasonRequiredError(ActionDecisionError):
    """A decision arrived without the operator's reason.

    The reason rule names both the status and this code: a decision without a non-blank reason
    is a 409 `change_reason_required`, and the gate is *this endpoint* — not the tool call,
    which cannot carry a reason at all (`assert_no_reason_argument`). The reason is born here
    and only here.

    Whitespace counts as blank. A space bar is not an answer to "why is this change being
    made", and the field that authorises a change is the last one to accept a placeholder.
    """

    error_code: str = "change_reason_required"
    message: str = "A reason is required. Type why this change is being made or refused."


class ChangeExecutionLimitReachedError(ActionDecisionError):
    """The operator already has their allowance of changes running.

    409, and the same reading as `ActionRequestAlreadyDecidedError`: the caller may approve
    changes in general, just not one more right now. Not a 429 — nothing is rate-limiting the
    operator, and `Retry-After` would be a number NOA cannot honestly produce (it depends on
    how long someone else's SSH round trip takes).

    The per-user in-flight cap forbids a silent queue, which is what makes this a refusal rather
    than a wait: a queued
    approval is an authorisation whose moment has passed by the time it runs. Nothing is lost by
    refusing — the request stays PENDING until its TTL, so the remedy is to approve it again once
    the running change finishes.

    A change stranded by a dead process counts towards the limit until the reaper resolves it
    (`core.approvals.reaper`), which is why that deadline exists rather than being left to
    "eventually".
    """

    error_code: str = "change_execution_limit_reached"
    message: str = (
        "You already have a change running. Wait for it to finish, then approve this one again."
    )


class DecisionCsrfInvalidError(ActionDecisionError):
    """The decision POST carried no valid CSRF token.

    403 rather than 401: the session is fine and re-authenticating changes nothing. The
    token is missing, malformed, minted for a different operator or a different request, or
    older than the pending TTL — all one code and one body, because telling a caller *which*
    is telling them how much closer they got.

    Reachable honestly, too: a card left open past the TTL. Hence a message that says to
    reload rather than one that reads as an accusation.
    """

    error_code: str = "csrf_token_invalid"
    message: str = "This approval card is no longer valid. Reload it and try again."


__all__ = [
    "ActionDecisionError",
    "ActionReceiptNotFoundError",
    "ActionRequestAlreadyDecidedError",
    "ActionRequestExpiredError",
    "ActionRequestNotFoundError",
    "ChangeEvidenceRequiredError",
    "ChangeExecutionLimitReachedError",
    "ChangeGateBranchUnavailableError",
    "ChangeGateError",
    "ChangeGateUnavailableError",
    "ChangeReasonForbiddenError",
    "ChangeReasonRequiredError",
    "DecisionCsrfInvalidError",
]
