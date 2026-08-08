"""Refusals from the CHANGE approval gate (T33 — C8, V17, V22, V43, V73).

One tree, three refusals, and the split is what the caller can do about it:

- `ChangeGateUnavailableError` — NOA could not record the request. The change did not
  happen and must not; retrying is the remedy.
- `ChangeReasonForbiddenError` — a CHANGE tool tried to hand the gate an argument that
  reads as a reason. Not an operator problem at all: it is the gate refusing to let C8's
  boundary be crossed from the inside.
- `ChangeEvidenceRequiredError` — the gate was opened with no in-process preflight evidence
  (V17), so the approval card would ask an operator to authorise a change it cannot
  describe (V35).

`NoaError` rather than a bare exception for the usual two reasons (V73): `sanitize_tool_errors`
passes a `NoaError`'s own `error_code` through to the model instead of collapsing it into
`tool_execution_failed` (V19), so the code that names the fix survives the boundary; and
T37's endpoints will raise from the same tree into an HTTP response, where one handler shapes
every body. Both statuses are mapped explicitly in `noa_api.api.errors` — a subclass-tree
test asserts none of these falls through to the 503 default.

Messages are operator-safe (V8): they say what NOA declined to do, never which column, key
or argument was involved. That detail rides in `detail`, which is logs only.
"""

from __future__ import annotations

from core.errors import NoaError


class ChangeGateError(NoaError):
    """Base: a CHANGE was not admitted to the approval gate, so it did not run."""

    error_code: str = "change_gate_failed"
    message: str = "This change could not be submitted for approval. Nothing was changed."


class ChangeGateUnavailableError(ChangeGateError):
    """The pending request could not be written (T33, V23).

    Fail-closed, and the same argument T73(c) made for the opening `tool_runs` write: V23
    answers "may this run?" from a row, so a gate that let the tool proceed without one
    would leave the invariant asserted by prose and held by nothing (V69's shape, B2).
    """

    error_code: str = "change_gate_unavailable"
    message: str = (
        "NOA could not record this change for approval and will not run it. Try again; "
        "contact an administrator if this continues."
    )


class ChangeReasonForbiddenError(ChangeGateError):
    """A gate call carried an argument that reads as a reason (C8, V15, V43).

    The reason is born when an operator types it into the approval card and nowhere else.
    A CHANGE tool whose arguments carry one has either accepted a reason from the model or
    authored one itself, and both are the thing C8 forbids — so the request is refused
    rather than written with the offending key quietly dropped, which would leave the tool's
    schema wrong and nothing red.
    """

    error_code: str = "change_reason_forbidden"
    message: str = (
        "This change could not be submitted for approval. The reason for a change is typed "
        "by the operator on the approval card and cannot be supplied with the request."
    )


class ChangeEvidenceRequiredError(ChangeGateError):
    """A gate call carried no preflight evidence (C9, V17, V33, V35).

    C9 puts the preflight in-process inside the CHANGE call precisely so the card can show a
    before-state the model never touched. A gate opened without one produces a card that
    asks for authorisation and describes nothing, which is the state V35 exists to prevent.
    """

    error_code: str = "change_evidence_required"
    message: str = (
        "This change could not be submitted for approval because NOA gathered no "
        "before-state for it. Contact an administrator if this continues."
    )


__all__ = [
    "ChangeEvidenceRequiredError",
    "ChangeGateError",
    "ChangeGateUnavailableError",
    "ChangeReasonForbiddenError",
]
