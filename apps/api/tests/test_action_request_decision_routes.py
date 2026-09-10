"""Approve/deny route guards.

No Postgres: `support.action_decisions.decision_harness` swaps the decision repository and
the executor for in-memory doubles and leaves the rest — the router, the error handler,
`JWTService`, the real `AuthService` behind `require_session_user`, the real
`ActionDecisionService`, and the real CSRF mint and verify — as production code. The SQL
gets its own coverage against a live scratch database in
`test_action_request_decisions_live.py`, which is where V28's row lock is actually provable.

Two things this file is careful about, both because they are the whole design:

- **Refusal bodies are compared, not just statuses.** V27 makes a foreign request answer the
  same as an absent one, and "same status" is a much weaker claim than "same body" — an
  `error_code` that differed would be an existence oracle with a 404 painted on it.
- **Order is asserted, not inferred.** The doubles share one journal, so
  `["lock", "run", "decision:APPROVED", "commit", "execute"]` pins that every guard ran
  inside the lock, that the run and the decision commit together, and that the executor was
  handed the run only after that commit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from httpx import Response

from core.approvals.decisions import ActionDecisionService
from core.approvals.errors import (
    ActionDecisionError,
    ActionRequestAlreadyDecidedError,
    ActionRequestExpiredError,
    ActionRequestNotFoundError,
    ChangeReasonRequiredError,
    DecisionCsrfInvalidError,
)
from core.db.lifecycle import ActionRequestStatus
from noa_api.api import deps
from noa_api.api.errors import FALLBACK_STATUS, STATUS_BY_ERROR, error_body, status_for
from noa_api.api.routes.action_requests import MAX_REASON_LENGTH
from support.action_decisions import (
    APPROVAL_CONTEXT,
    CHANGE_TOOL,
    CONVERSATION_ID,
    REASON,
    DecisionHarness,
    RecordingApprovedChangeExecutor,
    decision_harness,
    locked_request,
)
from support.auth import build_settings

OTHER_EMAIL = "second-operator@example.com"


@pytest.fixture
def harness():
    with decision_harness() as built:
        built.sign_in()
        yield built


def pending(harness: DecisionHarness, **overrides):
    """A PENDING request owned by the signed-in operator."""
    overrides.setdefault("requested_by_user_id", harness.operator.id)
    return harness.repository.add(locked_request(**overrides))


def body(response: Response) -> dict[str, str]:
    return response.json()


# --------------------------------------------------------------------------------------
# The decision itself
# --------------------------------------------------------------------------------------


def test_approve_returns_202_with_tool_run_id(harness: DecisionHarness) -> None:
    """V29: the decision is durable when this returns; the change has not run yet.

    202 rather than 200, and the body names the run to poll — the state lives in the
    database, not in this connection.
    """
    request = pending(harness)

    response = harness.approve(request.action_request_id)

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert body(response)["action_request_id"] == str(request.action_request_id)
    assert body(response)["tool_run_id"] == str(harness.repository.only_run.tool_run_id)


def test_approve_writes_the_decision_and_links_the_run(harness: DecisionHarness) -> None:
    """The row is the authorization, so what it says after approval is the claim."""
    request = pending(harness)

    harness.approve(request.action_request_id)

    decision = harness.repository.only_decision
    assert decision.action_request_id == request.action_request_id
    assert decision.status is ActionRequestStatus.APPROVED
    assert decision.reason == REASON
    assert decision.tool_run_id == harness.repository.only_run.tool_run_id
    assert decision.committed == 1


def test_approve_starts_a_change_run_carrying_the_gate_time_facts(
    harness: DecisionHarness,
) -> None:
    """V46, V47: an approved CHANGE writes a `tool_runs` row, and it describes *this* change.

    `tool_name` and `conversation_ref` come off the locked row and the arguments come off
    `approval_context` — already redacted at gate time. Re-deriving any of them
    here would be a second record of one moment that can disagree with the card an operator
    actually read.
    """
    request = pending(harness)

    harness.approve(request.action_request_id)

    run = harness.repository.only_run
    assert run.tool_name == CHANGE_TOOL
    assert run.conversation_ref == CONVERSATION_ID
    assert run.requested_by_user_id == harness.operator.id
    assert run.args == APPROVAL_CONTEXT["arguments"]


def test_approve_hands_the_run_to_the_executor_after_committing(
    harness: DecisionHarness,
) -> None:
    """V28, V29, V31, V46 in one assertion: the order the service did things in.

    `lock` first, so every guard is evaluated through it. `inflight` after the guards and
    *before* `run`, because the run this approval inserts is the row V31's count is counting —
    taken after it, the cap could only ever be checked against a number this call already
    changed. `run` before `decision` and both before `commit`, so an APPROVED row with no
    run is unrepresentable. `execute` last, because a handoff before the commit could start a
    change whose authorization then rolled back.
    """
    request = pending(harness)

    harness.approve(request.action_request_id)

    assert harness.journal == [
        "lock",
        "inflight",
        "run",
        "decision:APPROVED",
        "commit",
        "execute",
    ]
    assert harness.executor.only.tool_run_id == harness.repository.only_run.tool_run_id
    assert harness.executor.only.action_request_id == request.action_request_id


def test_a_failed_handoff_does_not_fail_the_approval(harness: DecisionHarness) -> None:
    """The decision is committed by the time the executor runs, so it stands.

    Raising here would answer 500 for a change that *is* approved and recorded, and the
    operator's only move would be to click again — which lands on V28's 409 and tells them
    nothing. The row is APPROVED, the run is STARTED, and T38's reaper is specified for
    exactly that pair.
    """
    request = pending(harness)
    harness.executor.fail = RuntimeError("event loop is closing")

    response = harness.approve(request.action_request_id)

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert harness.repository.only_decision.status is ActionRequestStatus.APPROVED
    assert harness.repository.commits == ["APPROVED"]


def test_deny_records_the_refusal_and_starts_nothing(harness: DecisionHarness) -> None:
    """A denied change did not run, and there is no branch that could say otherwise."""
    request = pending(harness)

    response = harness.deny(request.action_request_id)

    assert response.status_code == status.HTTP_200_OK
    assert body(response)["status"] == ActionRequestStatus.DENIED.value

    decision = harness.repository.only_decision
    assert decision.status is ActionRequestStatus.DENIED
    assert decision.reason == REASON
    assert decision.tool_run_id is None
    assert harness.repository.runs == []
    assert harness.executor.started == []
    assert harness.journal == ["lock", "decision:DENIED", "commit"]


# --------------------------------------------------------------------------------------
# V15 — the reason is born here
# --------------------------------------------------------------------------------------


def test_approve_without_reason_is_refused(harness: DecisionHarness) -> None:
    """V15 names both: 409 and `change_reason_required`."""
    request = pending(harness)

    response = harness.approve(request.action_request_id, reason="")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert body(response)["error_code"] == "change_reason_required"


def test_approve_with_whitespace_reason_is_refused(harness: DecisionHarness) -> None:
    """A space bar is not an answer to "why is this change being made"."""
    request = pending(harness)

    response = harness.approve(request.action_request_id, reason="   \t\n  ")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert body(response)["error_code"] == "change_reason_required"


def test_deny_without_reason_is_refused(harness: DecisionHarness) -> None:
    """T37's call on what T34 left open: required on both, which is what makes the
    database CHECK expressible. The safe exit stays open — leaving the request alone
    expires it, and an expiry carries no reason precisely because nobody gave one.
    """
    request = pending(harness)

    response = harness.deny(request.action_request_id, reason=" ")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert body(response)["error_code"] == "change_reason_required"


def test_a_blank_reason_never_reaches_the_row(harness: DecisionHarness) -> None:
    """Refused before any I/O, so a submit that cannot succeed does not take a row lock."""
    request = pending(harness)

    harness.approve(request.action_request_id, reason="")

    assert harness.journal == []
    assert harness.repository.decisions == []


def test_the_stored_reason_is_stripped(harness: DecisionHarness) -> None:
    """The operator's words, not their whitespace. One reason, stored once, one shape."""
    request = pending(harness)

    harness.approve(request.action_request_id, reason=f"  {REASON}\n")

    assert harness.repository.only_decision.reason == REASON


def test_an_over_long_reason_is_refused_by_the_schema(harness: DecisionHarness) -> None:
    """T34 left `reason` unbounded `Text` on purpose; this endpoint is where "long" ends.

    422 rather than 409: this one *is* a malformed body — the operator did not type two
    thousand and one characters into a card.
    """
    request = pending(harness)

    response = harness.approve(request.action_request_id, reason="x" * (MAX_REASON_LENGTH + 1))

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert harness.repository.decisions == []


def test_a_reason_at_the_bound_is_accepted(harness: DecisionHarness) -> None:
    """The boundary, asserted rather than assumed."""
    request = pending(harness)

    response = harness.approve(request.action_request_id, reason="x" * MAX_REASON_LENGTH)

    assert response.status_code == status.HTTP_202_ACCEPTED


def test_a_missing_reason_field_is_refused(harness: DecisionHarness) -> None:
    """The field is required, so an omitted one is not an empty one by default."""
    request = pending(harness)

    response = harness.approve(
        request.action_request_id,
        body={"csrf": harness.csrf_for(request.action_request_id)},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --------------------------------------------------------------------------------------
# V22, V23 — only a cookie POST decides, and the body cannot claim a status
# --------------------------------------------------------------------------------------


def test_no_session_cookie_cannot_decide(harness: DecisionHarness) -> None:
    """V22: the decision arrives as a cookie POST or it does not arrive."""
    request = pending(harness)
    harness.sign_out()

    response = harness.approve(request.action_request_id)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert harness.repository.decisions == []


def test_a_disabled_operator_cannot_decide(harness: DecisionHarness) -> None:
    """V6's re-read, one boundary over: the cookie is still valid, the operator is not.

    The session JWT has no revocation path before `exp`, so this row read is the only thing
    standing between a disabled operator and an approved production change.
    """
    request = pending(harness)
    harness.operator.is_active = False

    response = harness.approve(request.action_request_id)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert harness.repository.decisions == []


def test_body_cannot_claim_a_status(harness: DecisionHarness) -> None:
    """V23: "may this run?" is answered from the row, never from what the caller sent.

    An extra `status` in the body is ignored — pydantic drops it — and the decision written
    is the one the *route* chose. There is nowhere in `DecisionRequest` for a claim to land.
    """
    request = pending(harness, status=ActionRequestStatus.PENDING)

    response = harness.deny(
        request.action_request_id,
        body={
            "reason": REASON,
            "csrf": harness.csrf_for(request.action_request_id),
            "status": "APPROVED",
            "action_request_id": str(uuid4()),
        },
    )

    assert response.status_code == status.HTTP_200_OK
    decision = harness.repository.only_decision
    assert decision.status is ActionRequestStatus.DENIED
    assert decision.action_request_id == request.action_request_id


def test_the_mcp_tool_context_exposes_no_decision_writer() -> None:
    """V22: the LLM-reachable path has no way to write a terminal status.

    `McpToolContext` carries `SQLActionRequestRepository`, which writes PENDING and nothing
    else. This asserts the decision repository never joins it — a second door on the
    authorization, on the side a bearer token reaches, would not otherwise be visible from
    any test in this file.
    """
    from core.approvals.decisions import (
        ActionDecisionRepository,
        ActionDecisionService,
        SQLActionDecisionRepository,
    )
    from noa_api.mcp_tools.context import McpToolContext

    forbidden = {
        ActionDecisionRepository,
        ActionDecisionService,
        SQLActionDecisionRepository,
    }
    annotations = {str(field.type) for field in McpToolContext.__dataclass_fields__.values()}

    for klass in forbidden:
        assert not any(klass.__name__ in annotation for annotation in annotations), (
            f"{klass.__name__} reached `McpToolContext` — that is the second door V22 closes"
        )


# --------------------------------------------------------------------------------------
# V39 — CSRF
# --------------------------------------------------------------------------------------


def test_invalid_csrf_is_refused(harness: DecisionHarness) -> None:
    request = pending(harness)

    response = harness.approve(request.action_request_id, csrf="v1.0.forged")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert body(response)["error_code"] == "csrf_token_invalid"


def test_a_missing_csrf_field_is_refused(harness: DecisionHarness) -> None:
    request = pending(harness)

    response = harness.approve(request.action_request_id, body={"reason": REASON})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert harness.repository.decisions == []


def test_a_csrf_token_for_another_card_is_refused(harness: DecisionHarness) -> None:
    """Request-bound (V39, stronger): a second open card is not a spare key."""
    request = pending(harness)
    other = pending(harness)

    response = harness.approve(
        request.action_request_id,
        csrf=harness.csrf_for(other.action_request_id),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_another_operators_csrf_token_is_refused(harness: DecisionHarness) -> None:
    """Session-bound. A cookie a sibling host planted does not come with one of these."""
    request = pending(harness)
    intruder = harness.add_operator(OTHER_EMAIL)

    response = harness.approve(
        request.action_request_id,
        csrf=harness.csrf_for(request.action_request_id, user_id=intruder.id),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_csrf_is_checked_before_the_row_is_touched(harness: DecisionHarness) -> None:
    """A forged cross-site POST never reaches the database.

    Ordering, not politeness: it is also what stops the endpoint from being a probe for
    which request ids exist, since every id answers 403 alike without a lookup.
    """
    request = pending(harness)

    harness.approve(request.action_request_id, csrf="nope")

    assert harness.journal == []
    assert harness.repository.locks == []


def test_an_expired_csrf_token_is_refused(harness: DecisionHarness) -> None:
    """A card left open past the pending TTL. Honest, and still refused — reload it."""
    request = pending(harness)
    stale = datetime.now(UTC) - timedelta(
        seconds=harness.settings.approval_pending_ttl_seconds + 60
    )

    response = harness.approve(
        request.action_request_id,
        csrf=harness.csrf_for(request.action_request_id, issued_at=stale),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


# --------------------------------------------------------------------------------------
# V27 — requester-match, and existence does not leak
# --------------------------------------------------------------------------------------


def test_foreign_request_and_unknown_id_answer_identically(harness: DecisionHarness) -> None:
    """V27: a mismatch is a 404, and it is the *same* 404 an absent request gets.

    Bodies compared, not just statuses. A differing `error_code` or message would make this
    an existence oracle with a 404 painted on it — which is exactly what a 403 would have
    been, spelled differently.
    """
    intruder = harness.add_operator(OTHER_EMAIL)
    foreign = harness.repository.add(locked_request(requested_by_user_id=intruder.id))
    unknown = uuid4()

    foreign_response = harness.approve(foreign.action_request_id)
    unknown_response = harness.approve(unknown)

    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    assert unknown_response.status_code == status.HTTP_404_NOT_FOUND

    # `request_id` differs per request by design; everything else must not.
    assert _without_request_id(foreign_response) == _without_request_id(unknown_response)
    assert harness.repository.decisions == []


def test_a_request_whose_requester_was_deleted_is_refused(harness: DecisionHarness) -> None:
    """The FK is `SET NULL`, so a deleted operator's request matches nobody.

    Fail-closed: NULL is not "anyone may decide this", it is "no one".
    """
    orphan = harness.repository.add(locked_request(requested_by_user_id=None))  # type: ignore[arg-type]

    response = harness.approve(orphan.action_request_id)

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_deny_enforces_the_same_requester_match(harness: DecisionHarness) -> None:
    """Both routes, or the weaker one is the whole access control."""
    intruder = harness.add_operator(OTHER_EMAIL)
    foreign = harness.repository.add(locked_request(requested_by_user_id=intruder.id))

    assert harness.deny(foreign.action_request_id).status_code == status.HTTP_404_NOT_FOUND


def test_a_malformed_id_is_not_a_lookup(harness: DecisionHarness) -> None:
    """A non-UUID path segment cannot name a row, so it is a 422 and never a database hit."""
    response = harness.client.post(
        "/action-requests/not-a-uuid/approve",
        json={"reason": REASON, "csrf": "irrelevant"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert harness.repository.locks == []


# --------------------------------------------------------------------------------------
# V28, V32 — one transition, and a deadline
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "already",
    [ActionRequestStatus.APPROVED, ActionRequestStatus.DENIED, ActionRequestStatus.EXPIRED],
)
def test_an_already_decided_request_is_refused(
    harness: DecisionHarness, already: ActionRequestStatus
) -> None:
    """V28: exactly one `pending → decided` transition, whatever it transitioned to."""
    request = pending(harness, status=already)

    response = harness.approve(request.action_request_id)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert body(response)["error_code"] == "action_request_already_decided"
    assert harness.repository.decisions == []


def test_expired_request_is_refused_and_made_terminal(harness: DecisionHarness) -> None:
    """V32's check-on-read, and it *writes*.

    Refusing without the write would leave a row that still reads PENDING, so the next
    reader would have to make the same discovery again — and a request nobody answered would
    keep looking answerable. The reason stays NULL: an expiry is the absence of an answer.
    """
    request = pending(harness, expires_in_seconds=-1)

    response = harness.approve(request.action_request_id)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert body(response)["error_code"] == "action_request_expired"

    decision = harness.repository.only_decision
    assert decision.status is ActionRequestStatus.EXPIRED
    assert decision.reason is None
    assert decision.tool_run_id is None
    assert decision.committed == 1
    assert harness.repository.runs == []
    assert harness.executor.started == []


def test_expiry_is_checked_under_the_lock(harness: DecisionHarness) -> None:
    """The deadline is read from the locked row, not from anything the caller sent."""
    request = pending(harness, expires_in_seconds=-1)

    harness.approve(request.action_request_id)

    assert harness.journal == ["lock", "decision:EXPIRED", "commit"]


def test_deny_on_an_expired_request_is_also_refused(harness: DecisionHarness) -> None:
    request = pending(harness, expires_in_seconds=-1)

    response = harness.deny(request.action_request_id)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert body(response)["error_code"] == "action_request_expired"


def test_a_request_at_its_deadline_is_expired(harness: DecisionHarness) -> None:
    """`expires_at <= now` — a request whose deadline is this instant has passed it.

    The boundary is asserted because `<` versus `<=` here is the difference between "may run
    at exactly its deadline" and "may not", and V32 makes the deadline terminal.
    """
    request = pending(harness, expires_in_seconds=0)

    assert harness.approve(request.action_request_id).status_code == status.HTTP_409_CONFLICT


# --------------------------------------------------------------------------------------
# V31 — the per-user cap on in-flight changes
# --------------------------------------------------------------------------------------


def test_an_operator_at_the_cap_is_refused_409(harness: DecisionHarness) -> None:
    """V31: over the limit is a refusal with its own code, never a silent queue.

    A queued approval is an authorisation whose moment has passed by the time it runs, and the
    operator would have no way to tell a slow change from a stuck one. Nothing is lost by
    refusing: the request stays PENDING until its TTL, so approving again is the remedy.
    """
    request = pending(harness)
    harness.repository.set_inflight(harness.operator.id, 1)

    response = harness.approve(request.action_request_id)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert body(response)["error_code"] == "change_execution_limit_reached"


def test_an_operator_below_the_cap_is_allowed(harness: DecisionHarness) -> None:
    """The negative control: the cap has to admit the ordinary case, or "refused at the
    limit" is a claim about a door that is simply shut."""
    request = pending(harness)
    harness.repository.set_inflight(harness.operator.id, 0)

    assert harness.approve(request.action_request_id).status_code == status.HTTP_202_ACCEPTED


def test_the_cap_is_counted_per_operator_and_not_globally() -> None:
    """Somebody else's change must not spend this operator's allowance.

    The count is scoped by `requested_by_user_id` in the statement; here it is scoped by the
    double's own map, so what this pins is that the service passes the *caller* down.
    """
    with decision_harness() as built:
        built.sign_in()
        other = built.add_operator(OTHER_EMAIL)
        built.repository.set_inflight(other.id, 99)
        request = pending(built)

        assert built.approve(request.action_request_id).status_code == status.HTTP_202_ACCEPTED


def test_the_cap_refuses_before_a_run_is_started(harness: DecisionHarness) -> None:
    """Ordering: the run this approval would insert is the row being counted.

    Counted after it, the cap could only ever be compared against a number this call already
    changed — so a limit of one would admit every first approval and then refuse forever.
    """
    request = pending(harness)
    harness.repository.set_inflight(harness.operator.id, 1)

    harness.approve(request.action_request_id)

    assert harness.journal == ["lock", "inflight"]
    assert harness.repository.runs == []
    assert harness.repository.decisions == []
    assert harness.executor.started == []


def test_the_cap_is_checked_after_the_row_is_found(harness: DecisionHarness) -> None:
    """A request that is not this operator's answers 404 whatever their allowance is.

    The cap is the last guard, so being at the limit never becomes a way to learn that somebody
    else's request exists — and an operator at their limit is told about a request that was
    actually theirs to approve.
    """
    harness.repository.set_inflight(harness.operator.id, 99)
    other = harness.add_operator(OTHER_EMAIL)
    foreign = pending(harness, requested_by_user_id=other.id)

    response = harness.approve(foreign.action_request_id)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert body(response)["error_code"] == "action_request_not_found"


def test_a_blank_reason_is_refused_without_taking_the_user_lock(
    harness: DecisionHarness,
) -> None:
    """V15 before V31: a submit that cannot succeed does not serialize behind anyone."""
    harness.repository.set_inflight(harness.operator.id, 99)
    request = pending(harness)

    harness.approve(request.action_request_id, reason="   ")

    assert harness.journal == []


def test_a_denial_ignores_the_cap(harness: DecisionHarness) -> None:
    """Nothing starts, so there is nothing to bound.

    An operator at their limit must still be able to say no — refusing a denial would leave the
    request to expire, which records less about a decision that was actually made.
    """
    request = pending(harness)
    harness.repository.set_inflight(harness.operator.id, 99)

    response = harness.deny(request.action_request_id)

    assert response.status_code == status.HTTP_200_OK
    assert harness.journal == ["lock", "decision:DENIED", "commit"]


def test_the_production_dependency_reads_the_cap_off_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`get_action_decision_service` is what production builds, and the harness overrides it.

    So the test below proves the *harness* plumbs a cap, and this one proves `deps` does — the
    gap was real and was found by mutation, not by reading: hardcoding `max_inflight_per_user=1`
    in the dependency left every other test in this file green.

    The service is spied on rather than inspected: what is asserted is the argument the
    dependency passed, which is the whole of the wiring claim, and it needs no access to
    anything private.
    """
    passed: list[int] = []

    class RecordingService(ActionDecisionService):
        def __init__(self, *, max_inflight_per_user: int, **kwargs: object) -> None:
            passed.append(max_inflight_per_user)
            super().__init__(max_inflight_per_user=max_inflight_per_user, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(deps, "ActionDecisionService", RecordingService)

    deps.get_action_decision_service(
        # The session is only handed to `SQLActionDecisionRepository`, which stores it and issues
        # nothing here.
        session=None,  # type: ignore[arg-type]
        settings=build_settings(approval_max_inflight_per_user=7),
        executor=RecordingApprovedChangeExecutor(),
    )

    assert passed == [7]


def test_the_cap_comes_from_settings() -> None:
    """`APPROVAL_MAX_INFLIGHT_PER_USER` is the one answer to "how many".

    Asserted with a value that is not the production default, so a service that hardcoded the
    default would pass a test written against 1 and fail this one (T33(e)'s argument, one setting
    over). Two changes in flight, a limit of two, and the third is what is refused.
    """
    with decision_harness(settings=build_settings(approval_max_inflight_per_user=2)) as built:
        built.sign_in()
        request = pending(built)

        built.repository.set_inflight(built.operator.id, 1)
        assert built.approve(request.action_request_id).status_code == status.HTTP_202_ACCEPTED

        built.repository.set_inflight(built.operator.id, 2)
        second = pending(built)
        assert built.approve(second.action_request_id).status_code == status.HTTP_409_CONFLICT


# --------------------------------------------------------------------------------------
# V73 — error shape
# --------------------------------------------------------------------------------------


def test_every_decision_error_is_mapped_explicitly() -> None:
    """V73: no decision refusal may reach the 503 *fallback* — that means "unclassified"."""

    def subclasses(klass: type[ActionDecisionError]) -> set[type[ActionDecisionError]]:
        found = {klass}
        for child in klass.__subclasses__():
            found |= subclasses(child)
        return found

    for klass in subclasses(ActionDecisionError):
        assert klass in set(STATUS_BY_ERROR), f"{klass.__name__} has no explicit status"
        assert status_for(klass.__new__(klass)) != FALLBACK_STATUS


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ActionRequestNotFoundError, status.HTTP_404_NOT_FOUND),
        (ActionRequestAlreadyDecidedError, status.HTTP_409_CONFLICT),
        (ActionRequestExpiredError, status.HTTP_409_CONFLICT),
        (ChangeReasonRequiredError, status.HTTP_409_CONFLICT),
        (DecisionCsrfInvalidError, status.HTTP_403_FORBIDDEN),
    ],
)
def test_each_refusal_takes_the_status_its_remedy_implies(error, expected: int) -> None:
    """404 hides existence; 409 says "not this one, not now"; 403 says re-auth
    changes nothing. Pinned per class, because these are the statuses the embed
    branches on.
    """
    assert status_for(error("diagnostic")) == expected


def test_a_refusal_body_carries_no_diagnostic(harness: DecisionHarness) -> None:
    """V8: `detail` is for the log. The body is `error_code`, `message`, `request_id`."""
    intruder = harness.add_operator(OTHER_EMAIL)
    foreign = harness.repository.add(locked_request(requested_by_user_id=intruder.id))

    response = harness.approve(foreign.action_request_id)

    assert set(body(response)) == {"error_code", "message", "request_id"}
    assert str(intruder.id) not in response.text
    assert error_body(ActionRequestNotFoundError("x"))["message"] == body(response)["message"]


def test_a_refusal_carries_the_request_id_header(harness: DecisionHarness) -> None:
    """V73: same value in the body and in `x-request-id`, on every error response."""
    request = pending(harness)

    response = harness.approve(request.action_request_id, reason="")

    assert response.headers["x-request-id"] == body(response)["request_id"]


# --------------------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------------------


def test_the_production_app_mounts_the_decision_routes() -> None:
    """The routes exist on the app `noa_api.main` builds, not only in a test harness.

    A route tested exclusively through its own harness is a route that can be left out of
    `create_app` with every test still green (T13's mount lesson, one router over).
    """
    from noa_api.main import create_app

    # Read off the OpenAPI schema, not `app.routes`: the pinned FastAPI keeps an included
    # router as one `_IncludedRouter` entry and resolves its routes lazily, so iterating
    # `app.routes` finds `/health` and nothing that arrived through `include_router` — a
    # route check written that way passes whether or not either router is mounted.
    paths = set(create_app().openapi()["paths"])

    assert "/action-requests/{action_request_id}/approve" in paths
    assert "/action-requests/{action_request_id}/deny" in paths
    # T41's card, on the same router and the same session cookie.
    assert "/action-requests/{action_request_id}" in paths
    # The negative control: this set genuinely distinguishes mounted from absent. There is
    # no collection route and §I.embed lists none — one URL per request, reached by an id
    # the operator was given, never by listing what exists.
    assert "/action-requests" not in paths


def test_the_real_executor_is_what_production_wires() -> None:
    """T38's seam, filled with the asyncio host that actually runs the change.

    Asserted rather than assumed because "approved changes silently never run" is precisely the
    failure this arrangement risks: between T37 and T38 the seam held a placeholder that logged
    and started nothing, and nothing in the request path would have looked different.

    Nothing is in flight on a freshly built runtime — the host owns tasks only once an approval
    hands it a run, which is what keeps `build_runtime` free of a connection.
    """
    from core.approvals.execution_host import AsyncioApprovedChangeExecutor
    from noa_api.main import build_runtime

    runtime = build_runtime(build_settings())

    assert isinstance(runtime.approved_change_executor, AsyncioApprovedChangeExecutor)
    assert runtime.approved_change_executor.outstanding == 0


def _without_request_id(response: Response) -> dict[str, object]:
    """A refusal body minus the one field that is different by design."""
    payload = dict(response.json())
    assert payload.pop("request_id", None), "every error body carries a request_id"
    return {"status_code": response.status_code, **payload}
