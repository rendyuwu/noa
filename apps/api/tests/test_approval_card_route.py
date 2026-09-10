"""The approval card's GET.

No Postgres: `support.approval_cards.card_harness` swaps the card repository and the expiry
writer for in-memory doubles and leaves the router, the error handler, `JWTService`, the real
`AuthService` behind `require_session_user`, the real `ApprovalCardService` and the real CSRF
mint and verify as production code. The SQL gets its own coverage in
`test_approval_cards_live.py`, which is where the requester-match's `WHERE` is actually
provable.

Three things this file is careful about, and all three are the design:

- **Refusal bodies are compared, not just statuses.** The requester-match rule makes another
  operator's request answer the same as an absent one, and "same status" is a much weaker claim
  than "same body" — an `error_code` that differed would be an existence oracle with a 404
  painted on it (a differing code leaking existence, and the reason the decision endpoints'
  route tests compare bodies too).
- **The CSRF token is verified through the production verifier**, never string-matched. What
  matters about it is what `verify_decision_csrf_token` accepts, so that is what is asked.
- **Order is asserted, not inferred.** The doubles share one journal, so a foreign id
  producing `["read"]` and nothing else is what pins "the requester-match runs before anything
  that writes".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import Response

from core.approvals.csrf import verify_decision_csrf_token
from core.approvals.errors import ActionRequestNotFoundError, DecisionCsrfInvalidError
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from support.approval_cards import (
    ARGUMENTS,
    CONVERSATION_ID,
    CREATED_AT,
    EVIDENCE,
    LIBRECHAT_USER_ID,
    RECEIPT_AFTER,
    RECEIPT_DELTA,
    CardHarness,
    card_harness,
    receipt_view,
    run_view,
)
from support.auth import OPERATOR_EMAIL

OTHER_EMAIL = "second-operator@example.com"


@pytest.fixture
def harness():
    with card_harness() as built:
        built.sign_in()
        yield built


def body(response: Response) -> dict:
    return response.json()


# --------------------------------------------------------------------------------------
# What the card says
# --------------------------------------------------------------------------------------


def test_the_card_carries_the_provenance_fields(harness: CardHarness) -> None:
    """Created-at, conversation ref, origin, requesting identity.

    All four in one response, because the operator authorising a change is being asked to
    recognise it: which tool, asked for by whom, from which conversation, when. The requester
    and the LibreChat account come off `approval_context`, persisted at gate time — the FK is
    `SET NULL`, so a join at render time would lose the identity the moment the operator
    was deleted.
    """
    card = harness.add_card()

    response = harness.get_card(card.action_request_id)

    assert response.status_code == status.HTTP_200_OK
    payload = body(response)
    assert payload["created_at"] == CREATED_AT.isoformat()
    assert payload["conversation_ref"] == CONVERSATION_ID
    assert payload["requester"] == {
        "email": OPERATOR_EMAIL,
        "librechat_user_id": LIBRECHAT_USER_ID,
    }
    assert payload["tool_name"] == card.tool_name


def test_the_card_carries_the_gate_time_before_state(harness: CardHarness) -> None:
    """The in-process preflight is what the card describes the change with.

    Built once at gate time and persisted, never rebuilt from a transcript — and shown
    here and nowhere else: `noa_get_action_result` has no field for it
    (`test_noa_tools_action_result.py`), which is what keeps it out of the model's context.
    """
    card = harness.add_card()

    payload = body(harness.get_card(card.action_request_id))

    assert payload["evidence"] == EVIDENCE
    assert payload["arguments"] == ARGUMENTS


def test_the_card_body_carries_no_reason(harness: CardHarness) -> None:
    """Exactly one reason exists and the operator types it into this card.

    Asserted on the key set rather than on a value: a `reason` field that happened to be
    `None` today is a field a later edit fills in, and the point is that there is nowhere for
    one to be read back from on a render path.
    """
    card = harness.add_card()

    payload = body(harness.get_card(card.action_request_id))

    assert "reason" not in payload
    assert set(payload) == {
        "action_request_id",
        "tool_name",
        "status",
        "conversation_ref",
        "requester",
        "arguments",
        "evidence",
        "created_at",
        "expires_at",
        "decided_at",
        "run",
        "receipt",
        "csrf",
    }


def test_the_card_reports_the_run_an_approval_started(harness: CardHarness) -> None:
    """One URL owns the lifecycle, so the card reports what the answer did.

    `STARTED` with no summary is the honest state between the decision and the executor's
    terminal write, and a card that says so tells the operator to keep the tab open, which is
    true. What that run *did* is the receipt, below.
    """
    run = run_view(status=ToolRunStatus.STARTED)
    card = harness.add_card(
        status=ActionRequestStatus.APPROVED,
        decided_at=datetime(2026, 8, 8, 9, 30, tzinfo=UTC),
        run=run,
    )

    payload = body(harness.get_card(card.action_request_id))

    assert payload["status"] == ActionRequestStatus.APPROVED.value
    assert payload["decided_at"] == "2026-08-08T09:30:00+00:00"
    assert payload["run"]["tool_run_id"] == str(run.tool_run_id)
    assert payload["run"]["status"] == ToolRunStatus.STARTED.value
    assert payload["run"]["result_summary"] is None


def test_a_request_with_no_run_says_so_rather_than_omitting_the_key(
    harness: CardHarness,
) -> None:
    """`null`, not absent: "this change never ran" is an answer, a missing key is a gap."""
    card = harness.add_card(status=ActionRequestStatus.DENIED, decided_at=CREATED_AT)

    payload = body(harness.get_card(card.action_request_id))

    assert payload["run"] is None
    assert payload["receipt"] is None


def test_the_card_body_carries_the_receipts_two_halves(harness: CardHarness) -> None:
    """DECISIONS.md section 6.5: before-state and after-state, each on its own.

    The two halves are asserted as two keys with two different payloads, which is the property
    the requirement is about — a body that answered `{"outcome": "done"}` would satisfy "the
    card reports the receipt" and none of what that was for. The fixture's halves share no
    value, so a response that sent one of them twice separates from this.

    Five keys, and the fifth is what the runner said the change moved. It is asserted in the
    same equality as the other four because the two are one contract: the body enumerated here
    is the executable form of the row the embed app's contract carries for this route, and a
    body that grew a
    key the row does not name is the pair disagreeing. The delta is a third distinct value for
    the same reason the halves are two, so a card that echoed one of them here would pass a
    weaker test than this one.

    That nothing reading `before` and `after` can *compute* the delta is a property of the seven
    real tools' two vocabularies, and it is measured against those tools in
    `test_change_receipt_halves.py`. This fixture is a rendering one and holds only that the three
    values travel.
    """
    card = harness.add_card(
        status=ActionRequestStatus.APPROVED,
        decided_at=datetime(2026, 8, 8, 9, 30, tzinfo=UTC),
        run=run_view(status=ToolRunStatus.COMPLETED),
        receipt=receipt_view(delta=RECEIPT_DELTA),
    )

    payload = body(harness.get_card(card.action_request_id))

    assert payload["receipt"] == {
        "ok": True,
        "before": EVIDENCE,
        "after": RECEIPT_AFTER,
        "error_code": None,
        "delta": RECEIPT_DELTA,
    }
    assert payload["receipt"]["before"] != payload["receipt"]["after"]


def test_a_receipt_with_no_delta_says_so_rather_than_omitting_the_key(
    harness: CardHarness,
) -> None:
    """`null`, not absent — the opposite of the stored row's rule, and deliberately so.

    The approved-change executor's writer omits the key when a runner measured nothing, so that
    the absence on the row is
    itself the fact. A renderer switches on the field, and a missing key there reads as one it
    forgot, so the card turns that absence into `null` — the rule `run`, `receipt` and
    `error_code` already follow on this body.
    """
    card = harness.add_card(
        status=ActionRequestStatus.APPROVED,
        decided_at=CREATED_AT,
        run=run_view(status=ToolRunStatus.FAILED),
        receipt=receipt_view(ok=False, error_code="change_runner_unavailable"),
    )

    payload = body(harness.get_card(card.action_request_id))

    assert "delta" in payload["receipt"]
    assert payload["receipt"]["delta"] is None


def test_a_failed_changes_receipt_keeps_its_before_state_and_names_the_cause(
    harness: CardHarness,
) -> None:
    """A receipt with a before-state and a refused after-state is the truthful record.

    The half that must not disappear is `before` — a change that failed is exactly when an
    operator needs to read what the system looked like when they authorised it.
    """
    card = harness.add_card(
        status=ActionRequestStatus.APPROVED,
        decided_at=CREATED_AT,
        run=run_view(status=ToolRunStatus.FAILED),
        receipt=receipt_view(
            ok=False,
            after={"ok": False, "error_code": "ssh_sudo_required", "message": "refused"},
            error_code="ssh_sudo_required",
        ),
    )

    payload = body(harness.get_card(card.action_request_id))

    assert payload["receipt"]["ok"] is False
    assert payload["receipt"]["error_code"] == "ssh_sudo_required"
    assert payload["receipt"]["before"] == EVIDENCE


@pytest.mark.parametrize(
    "request_status",
    [
        ActionRequestStatus.PENDING,
        ActionRequestStatus.APPROVED,
        ActionRequestStatus.DENIED,
        ActionRequestStatus.EXPIRED,
    ],
)
def test_the_card_answers_for_every_status(
    harness: CardHarness,
    request_status: ActionRequestStatus,
) -> None:
    """One URL through the whole lifecycle, not a PENDING-only surface.

    A card that 404'd once a decision landed would send an operator who clicked twice to a
    dead page, and the second click is exactly when they want to read the outcome.
    """
    decided = None if request_status is ActionRequestStatus.PENDING else CREATED_AT
    card = harness.add_card(status=request_status, decided_at=decided)

    response = harness.get_card(card.action_request_id)

    assert response.status_code == status.HTTP_200_OK
    assert body(response)["status"] == request_status.value


# --------------------------------------------------------------------------------------
# Who may read it
# --------------------------------------------------------------------------------------


def test_another_operators_request_and_an_unknown_id_answer_identically(
    harness: CardHarness,
) -> None:
    """The requester-match rule, as amended by the decision endpoints: "never leak" is bound at
    the **body**, not at the status.

    A differing `error_code` is a 403 spelled differently, and a status-only test would not
    catch it (a differing code leaking existence). Only `request_id` may differ, and the shared
    request-id rule says why it must.
    """
    other = harness.add_operator(OTHER_EMAIL)
    foreign = harness.add_card_for(other.id)

    foreign_response = harness.get_card(foreign.action_request_id)
    unknown_response = harness.get_card(uuid4())

    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    assert unknown_response.status_code == status.HTTP_404_NOT_FOUND

    foreign_body, unknown_body = body(foreign_response), body(unknown_response)
    assert foreign_body["error_code"] == ActionRequestNotFoundError.error_code
    assert foreign_body["message"] == unknown_body["message"]
    assert foreign_body.pop("request_id") != unknown_body.pop("request_id")
    assert foreign_body == unknown_body


def test_a_request_whose_requester_was_deleted_belongs_to_nobody(
    harness: CardHarness,
) -> None:
    """The table's FK is `SET NULL`, so a deleted operator leaves a NULL requester behind.

    It matches nobody, which is the fail-closed direction — the alternative is a row anyone
    can read because it belongs to no one.
    """
    orphan = harness.add_card_for(None)

    assert harness.get_card(orphan.action_request_id).status_code == status.HTTP_404_NOT_FOUND


def test_the_read_asks_about_the_cookies_operator_never_the_url(
    harness: CardHarness,
) -> None:
    """The requester compared against is the session's identity.

    Asserted on what the repository was *asked*, not only on the answer: a route that passed
    something else and still 404'd for the wrong reason would look identical from outside.
    """
    card = harness.add_card()

    harness.get_card(card.action_request_id)

    assert harness.repository.lookups == [(card.action_request_id, harness.operator.id)]


def test_without_a_session_cookie_the_card_is_not_read_at_all(harness: CardHarness) -> None:
    """`require_session_user` runs before the handler, so an anonymous GET touches nothing.

    The empty journal is the assertion that matters — a 401 produced *after* a read would look
    the same from outside and would mean an unauthenticated caller had reached the row.
    """
    card = harness.add_card()
    harness.sign_out()

    response = harness.get_card(card.action_request_id)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert harness.journal == []


def test_a_disabled_operator_loses_the_card_on_the_next_request(harness: CardHarness) -> None:
    """The session JWT has no revocation path, so the row re-read is the only bound."""
    card = harness.add_card()
    harness.operator.is_active = False

    response = harness.get_card(card.action_request_id)

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert harness.journal == []


# --------------------------------------------------------------------------------------
# The deadline
# --------------------------------------------------------------------------------------


def test_a_card_past_its_deadline_reads_expired_and_is_written_expired(
    harness: CardHarness,
) -> None:
    """A render path never serves a stale PENDING, and it makes the row terminal.

    Both halves, because either alone is the bug: reporting `EXPIRED` without the write leaves
    a row the next reader has to rediscover, and writing without reporting hands the operator a
    live Approve button over a request the decision door would refuse.
    """
    card = harness.add_card(expires_in_seconds=-1)

    payload = body(harness.get_card(card.action_request_id))

    assert payload["status"] == ActionRequestStatus.EXPIRED.value
    assert payload["decided_at"] is not None
    assert harness.journal == ["read", "expire", "commit"]
    row = harness.expiry_repository.rows[card.action_request_id]
    assert row.status is ActionRequestStatus.EXPIRED
    assert row.reason is None


def test_a_live_card_is_not_expired_by_being_read(harness: CardHarness) -> None:
    """The separating case: the check runs, and on a live row it changes nothing.

    Without this, the assertion above passes just as well against a path that expires
    everything it reads.
    """
    card = harness.add_card(expires_in_seconds=3600)

    payload = body(harness.get_card(card.action_request_id))

    assert payload["status"] == ActionRequestStatus.PENDING.value
    assert payload["decided_at"] is None
    assert harness.expiry_repository.rows[card.action_request_id].status is (
        ActionRequestStatus.PENDING
    )


def test_a_foreign_id_never_reaches_the_expiry_writer(harness: CardHarness) -> None:
    """Requester-match before check-on-read, and this is the whole reason for that order.

    `expire_if_due` takes an id and no requester, and the id in this URL reaches an operator
    through a tool result that persists in LibreChat's MongoDB — so an expiry-first card
    would let a prompt-injected id make NOA write to a row its reader cannot see. Reading first
    makes that a pure no-op: one read, no write.
    """
    other = harness.add_operator(OTHER_EMAIL)
    foreign = harness.add_card_for(other.id, expires_in_seconds=-1)

    assert harness.get_card(foreign.action_request_id).status_code == status.HTTP_404_NOT_FOUND
    assert harness.journal == ["read"]
    assert harness.expiry_repository.rows[foreign.action_request_id].status is (
        ActionRequestStatus.PENDING
    )


# --------------------------------------------------------------------------------------
# The CSRF token
# --------------------------------------------------------------------------------------


def _verify(harness: CardHarness, token: str, *, action_request_id: UUID) -> None:
    """The production verifier, against the operator the cookie resolved to."""
    verify_decision_csrf_token(
        settings=harness.settings,
        token=token,
        user_id=harness.operator.id,
        action_request_id=action_request_id,
    )


def test_the_card_carries_a_token_the_production_verifier_accepts(
    harness: CardHarness,
) -> None:
    """The card is where a decision token comes from — there is no minting route.

    Verified rather than pattern-matched: what matters about the token is that
    `verify_decision_csrf_token` accepts it for this operator and this request, which is
    exactly what the two POSTs will ask.
    """
    card = harness.add_card()

    token = body(harness.get_card(card.action_request_id))["csrf"]

    assert isinstance(token, str) and token
    _verify(harness, token, action_request_id=card.action_request_id)


def test_a_token_from_one_card_does_not_authorise_another(harness: CardHarness) -> None:
    """The CSRF token binds to the request as well as the session.

    So a second card left open in another tab is not a spare key. The separating half of the
    test above: a token bound to the operator alone would pass both.
    """
    first = harness.add_card()
    second = harness.add_card()

    token = body(harness.get_card(first.action_request_id))["csrf"]

    with pytest.raises(DecisionCsrfInvalidError):
        _verify(harness, token, action_request_id=second.action_request_id)


def test_a_token_minted_for_one_operator_does_not_verify_for_another(
    harness: CardHarness,
) -> None:
    """The CSRF token's session binding, from the other side."""
    card = harness.add_card()
    token = body(harness.get_card(card.action_request_id))["csrf"]

    with pytest.raises(DecisionCsrfInvalidError):
        verify_decision_csrf_token(
            settings=harness.settings,
            token=token,
            user_id=uuid4(),
            action_request_id=card.action_request_id,
        )


@pytest.mark.parametrize(
    "request_status",
    [
        ActionRequestStatus.APPROVED,
        ActionRequestStatus.DENIED,
        ActionRequestStatus.EXPIRED,
    ],
)
def test_a_terminal_card_carries_no_token(
    harness: CardHarness,
    request_status: ActionRequestStatus,
) -> None:
    """No live token for a card nobody may decide.

    The decision door would refuse the POST anyway, so this is not the guard — it is
    the absence of a key with no door, and what the renderer reads to decide whether to draw
    live buttons at all (never a live Approve button on something inert).
    """
    card = harness.add_card(status=request_status, decided_at=CREATED_AT)

    assert body(harness.get_card(card.action_request_id))["csrf"] is None


def test_an_expired_on_read_card_carries_no_token(harness: CardHarness) -> None:
    """The status the token is minted against is the one the expiry just wrote, not the one
    the row had a moment ago."""
    card = harness.add_card(expires_in_seconds=-1)

    payload = body(harness.get_card(card.action_request_id))

    assert payload["status"] == ActionRequestStatus.EXPIRED.value
    assert payload["csrf"] is None


# --------------------------------------------------------------------------------------
# Failure shape
# --------------------------------------------------------------------------------------


def test_a_broken_read_answers_the_shared_envelope(harness: CardHarness) -> None:
    """Every error body carries `request_id`, and no route builds its own shape."""
    card = harness.add_card()
    harness.repository.fail = RuntimeError("connection reset")

    with harness.client_that_reports_server_errors() as client:
        response = client.get(f"/action-requests/{card.action_request_id}")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert body(response)["request_id"] == response.headers["x-request-id"]
    assert "connection reset" not in response.text


def test_a_malformed_id_does_not_reach_the_repository(harness: CardHarness) -> None:
    """A non-UUID path segment is a 422 from the path parameter, before any read.

    Unlike the action-result tool, where a malformed id had to answer with the *same* shape as
    an unknown
    one because a model would otherwise learn which ids are well-formed, this is a
    browser on NOA's own origin reaching a URL NOA itself built. The 422 says the URL is
    wrong, which is the truth and the useful thing to say.
    """
    response = harness.client.get("/action-requests/not-a-uuid")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert harness.journal == []


def test_the_deadline_the_card_reports_is_the_rows_own(harness: CardHarness) -> None:
    """The card renders a countdown from this, so it is the row's value, not a re-derivation."""
    moment = datetime.now(UTC)
    card = harness.add_card(now=moment, expires_in_seconds=900)

    payload = body(harness.get_card(card.action_request_id))

    assert payload["expires_at"] == (moment + timedelta(seconds=900)).isoformat()
