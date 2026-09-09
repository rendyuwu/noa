"""The before→after delta's own rules, and the seam it arrives on (T38 — V85, V86).

Three lanes, and the order they are in is the order the claims depend on each other:

- **the record's own rules** — what `ChangeDelta` refuses to be built as. These come first
  because everything else rests on them: a delta that could be constructed with an invented
  before-value or a reason in it would make every per-runner assertion vacuous.
- **the receipt** — that the delta reaches `receipt_data` under one key, that the key is absent
  when nothing was measured, and that `before`, `after` and `result_summary` are what they were
  without it. The last is the whole reason the delta rides beside the payload instead of in it.
- **the executor's seam** — that a refusal *above* the runner states nothing, driven through the
  production service rather than against `build_receipt`.

What each of the seven runners publishes is `test_change_delta_runners.py`. The two WHM account
runners are asserted in `test_whm_tools_suspend_account.py` and
`test_whm_tools_unsuspend_account.py`, where their WHM endpoint fixtures live: moving them
would mean a second copy of that wiring (V66).

**The absence assertions are the point.** A delta that helpfully fills a gap is the failure mode
the shape exists to prevent, and it is not the kind of bug a green suite finds by accident — a
fabricated `false` reads exactly like a measured one. So a branch that measured nothing is
asserted on the *key being absent*, and one that measured "nothing moved" on the key being
*present and empty*, which are two different claims about the same change (V86).
"""

from __future__ import annotations

import json
from dataclasses import MISSING
from typing import Any

import pytest

from core.approvals.card import receipt_from_data
from core.approvals.delta import (
    VERIFICATION_MISMATCH,
    VERIFICATION_NOT_IN_FORCE,
    VERIFICATION_UNAVAILABLE,
    VERIFICATION_VERIFIED,
    BackendOutcome,
    Bound,
    ChangeDelta,
    ChangeOutcome,
    FieldChange,
    ListDelta,
)
from core.approvals.execution import (
    ERROR_RUNNER_UNAVAILABLE,
    RECEIPT_DELTA_KEY,
    ApprovedChangeExecutionService,
    build_receipt,
)
from core.audit.summaries import result_summary
from core.db.lifecycle import ToolRunStatus
from core.secrets.redaction import REDACTED
from support.approved_change_execution import (
    EVIDENCE,
    RUNNER_OK,
    FakeApprovedChangeExecutionRepository,
    RecordingChangeRunner,
    authorized_change,
)
from support.servers import YOPASS_URL
from support.whm_firewall import SERVER_NAME

# A delta with every optional facet left out, for the assertions about absence. `identity` and
# `verification` are the two a delta cannot be built without.
MINIMAL = ChangeDelta(identity={"server": SERVER_NAME}, verification=VERIFICATION_VERIFIED)

# --------------------------------------------------------------------------------------
# The record's own rules
# --------------------------------------------------------------------------------------


def test_a_delta_that_measured_nothing_carries_nothing() -> None:
    """Absence is structural: an unfilled facet is omitted, never sent as `null`.

    Asserted on the key set rather than on values, because a facet holding `null` is a
    measurement slot a renderer has to know to distrust — and one that a later edit fills in with
    a default without anybody noticing (V86).
    """
    assert MINIMAL.as_payload() == {
        "identity": {"server": SERVER_NAME},
        "verification": VERIFICATION_VERIFIED,
    }


def test_a_measured_empty_diff_is_not_the_same_as_no_diff() -> None:
    """The distinction the whole shape turns on, and it is invisible in a rendered card.

    `()` says NOA compared and nothing moved — a no-op, or a write the target system refused.
    `None` says NOA did not compare. Both would render as "no changes" to a reader who could not
    tell them apart, and only one of them is a measurement.
    """
    measured = ChangeDelta(
        identity={"server": SERVER_NAME},
        verification=VERIFICATION_VERIFIED,
        changed_fields=(),
    ).as_payload()
    unmeasured = ChangeDelta(
        identity={"server": SERVER_NAME},
        verification=VERIFICATION_UNAVAILABLE,
        verification_cause="task_timeout",
        changed_fields=None,
    ).as_payload()

    assert measured["changed_fields"] == []
    assert "changed_fields" not in unmeasured


def test_an_unknown_verification_state_is_refused() -> None:
    """Four states, and a fifth spelling of one of them is a state nothing downstream handles."""
    with pytest.raises(ValueError, match="verification"):
        ChangeDelta(identity={"server": SERVER_NAME}, verification="probably")


def test_the_four_verification_states_are_all_constructible() -> None:
    """The negative control for the case above (V87): the guard rejects a fifth, not a fourth.

    Without this, a typo in `VERIFICATION_STATES` that dropped `not_in_force` would leave the
    refusal above passing while making PMG's write-landed-and-sync-failed answer unbuildable.
    """
    for state in (
        VERIFICATION_VERIFIED,
        VERIFICATION_UNAVAILABLE,
        VERIFICATION_MISMATCH,
        VERIFICATION_NOT_IN_FORCE,
    ):
        assert ChangeDelta(identity={"server": SERVER_NAME}, verification=state)


def test_a_delta_about_nothing_is_refused() -> None:
    """A delta with no identity is a change to an unnamed thing, which is not a receipt."""
    with pytest.raises(ValueError, match="what it is about"):
        ChangeDelta(identity={}, verification=VERIFICATION_VERIFIED)


def test_a_field_whose_two_sides_match_is_refused() -> None:
    """ "Changed only" held by construction rather than by every author remembering it.

    A row reading `suspended: true → true` is a card printing a change that did not happen, and
    the branches that reach this case legitimately — an interface edited away and back while the
    request sat pending — are expected to answer an empty diff instead.
    """
    with pytest.raises(ValueError, match="did not change"):
        ChangeDelta(
            identity={"server": SERVER_NAME},
            verification=VERIFICATION_VERIFIED,
            changed_fields=(FieldChange(field="suspended", old=True, new=True),),
        )


def test_a_verified_delta_cannot_name_a_cause() -> None:
    """A cause explains the absence of a measurement, so it cannot ride on one."""
    with pytest.raises(ValueError, match="verification cause"):
        ChangeDelta(
            identity={"server": SERVER_NAME},
            verification=VERIFICATION_VERIFIED,
            verification_cause="task_timeout",
        )


def test_a_negative_bound_is_refused() -> None:
    """A total of minus one is not a bound, and V85's whole point is that the bound is readable."""
    with pytest.raises(ValueError, match="negative"):
        ChangeDelta(
            identity={"server": SERVER_NAME},
            verification=VERIFICATION_VERIFIED,
            bound=Bound(total=-1, truncated=True),
        )


@pytest.mark.parametrize(
    "key",
    ["reason", "Reason", " proposed_reason ", "suspendreason", "note", "comment"],
)
def test_no_reason_bearing_key_reaches_a_delta(key: str) -> None:
    """C8, V15, V43: one reason exists, the operator types it, and it does not come back.

    A receipt key is the same door `result_summary` is — the card and the admin audit surface
    read it, and `noa_get_action_result` reaches the audit trail — so the fence is checked where
    a delta is built rather than in seven runners. `suspendreason` is in the list because WHM
    echoes the note NOA wrote on every later `listaccts` row, so the words can arrive at a runner
    from the *target system* and not only from the request it was handed.

    Case-insensitive and whitespace-stripped, the comparison `core.secrets.redaction` makes for
    the same class of mistake one column over.
    """
    with pytest.raises(ValueError, match="reason-bearing"):
        ChangeDelta(
            identity={"server": SERVER_NAME, key: "why"},
            verification=VERIFICATION_VERIFIED,
        )


def test_a_reason_nested_inside_a_facet_is_refused_too() -> None:
    """A flat scan would pass `{"account": {"suspendreason": ...}}`, which is the shape a WHM
    preflight summary actually has — so the walk recurses, the way the redactor's does (V66)."""
    with pytest.raises(ValueError, match="reason-bearing"):
        ChangeDelta(
            identity={"server": SERVER_NAME, "account": {"user": "acmeco", "suspendreason": "x"}},
            verification=VERIFICATION_VERIFIED,
        )


def test_an_ordinary_identity_is_not_refused() -> None:
    """The negative control (V87): the fence rejects reason-bearing names, not every name.

    Without it, a predicate that matched too much would pass the refusals above while making the
    seven runners' own identities unbuildable — and every per-runner test below would fail for a
    reason that had nothing to do with what it asserts.
    """
    assert ChangeDelta(
        identity={"server": SERVER_NAME, "node": "pve1", "vmid": 110, "username": "ubuntu"},
        verification=VERIFICATION_VERIFIED,
    )


def delta_with_extra_facet(facet: dict[str, Any]) -> type[ChangeDelta]:
    """A `ChangeDelta` whose serialiser emits one more facet — the eighth tool's edit, in a test.

    A plain subclass rather than a `@dataclass` one, so it declares no field and inherits the
    generated `__init__`: the record built is the real one, with one extra key on the way out.
    """

    class _Extended(ChangeDelta):
        def as_payload(self) -> dict[str, Any]:
            return {**super().as_payload(), "operator_note": facet}

    return _Extended


def test_a_facet_added_to_the_serialiser_is_inside_the_reason_fence() -> None:
    """The fence reads the bytes that will be stored, so a new facet cannot land outside it (C8).

    `__post_init__` scans `as_payload()`. If it scanned a second view of the same record instead
    — a hand-maintained list of the facets that came from a target system — then a facet added to
    the serialiser and forgotten in that list would ride into the receipt unscanned, and nothing
    would fail on the day the two diverged. That is the shape this asserts is impossible: the
    subclass adds a key to the serialiser and only to the serialiser, and the refusal still fires.
    """
    with pytest.raises(ValueError, match="reason-bearing"):
        delta_with_extra_facet({"note": "why the operator approved it"})(
            identity={"server": SERVER_NAME}, verification=VERIFICATION_VERIFIED
        )


def test_an_added_facet_carrying_no_reason_is_left_alone() -> None:
    """The negative control (V87): the refusal above is about the key, not about the extra facet.

    Without it, a fence that refused any unknown facet outright would pass the case above while
    making the eighth CHANGE tool's delta unbuildable.
    """
    assert delta_with_extra_facet({"backend": "csf", "took": True})(
        identity={"server": SERVER_NAME}, verification=VERIFICATION_VERIFIED
    )


# --------------------------------------------------------------------------------------
# The receipt (V46)
# --------------------------------------------------------------------------------------


def test_the_receipt_omits_the_delta_key_when_a_runner_stated_none() -> None:
    """Absent, not `null` — the rule `error_code` already follows on this row.

    The absence is what a reader acts on: it is the one thing that separates an executor refusal
    from a runner failure, both of which are `ok: False`.
    """
    receipt = build_receipt(evidence=EVIDENCE, payload=RUNNER_OK)

    assert RECEIPT_DELTA_KEY not in receipt
    assert set(receipt) == {"ok", "before", "after"}


def test_the_receipt_carries_a_delta_under_one_key() -> None:
    """Exactly one key added, and its content is the runner's own `as_payload`."""
    receipt = build_receipt(evidence=EVIDENCE, payload=RUNNER_OK, delta=MINIMAL)

    assert set(receipt) == {"ok", "before", "after", RECEIPT_DELTA_KEY}
    assert receipt[RECEIPT_DELTA_KEY] == MINIMAL.as_payload()


def test_the_two_halves_are_byte_identical_with_and_without_a_delta() -> None:
    """The guarantee the seam exists for: `before` and `after` do not move.

    Asserted as rendered bytes rather than as dict equality, because dict equality would pass for
    two objects whose keys were reordered — and what is being promised is that a reader of this
    row, including one comparing it against a row written before the delta existed, sees the same
    two halves. Achievable only because the delta never enters the payload: `after` *is* the
    redacted payload, so a delta in there would make this assertion impossible to write.
    """
    without = build_receipt(evidence=EVIDENCE, payload=RUNNER_OK)
    with_delta = build_receipt(evidence=EVIDENCE, payload=RUNNER_OK, delta=MINIMAL)

    for half in ("before", "after"):
        assert json.dumps(with_delta[half]) == json.dumps(without[half])
    assert with_delta["ok"] == without["ok"]


def test_an_empty_delta_on_the_row_reads_as_nothing_measured() -> None:
    """The card's own rule for a delta NOA did not write, driven writer-to-reader.

    `build_receipt` omits the key rather than storing an empty object, so `{}` on this row came
    from something else — and between "a measurement with no content" and "nothing was measured",
    only the second is a reading that cannot be wrong. A card branching on the first would be
    rendering a delta with no identity and no verification, which is a shape `ChangeDelta` refuses
    to be built as.

    Untested until here: dropping `and delta` from the reader left the whole suite green, so the
    conservative half of the behaviour was resting on nothing.
    """
    row = build_receipt(evidence=EVIDENCE, payload=RUNNER_OK, delta=MINIMAL)

    assert receipt_from_data({**row, RECEIPT_DELTA_KEY: {}}).delta is None
    assert receipt_from_data(row).delta == MINIMAL.as_payload()


def test_a_credential_in_a_delta_is_redacted_on_its_own_line() -> None:
    """V8, V45: the delta goes through the redactor separately, not on the payload's pass.

    Two values, redacted twice, because they are two values. A delta lifted off the payload the
    way `ok` is would be lifted *unredacted* — `ok` and `error_code` are read from the raw
    envelope on purpose — and a delta redacted only by inheriting the payload's pass would be one
    policy away from a receipt holding a password nobody meant to store.
    """
    receipt = build_receipt(
        evidence=EVIDENCE,
        payload={"ok": True, "password": "hunter2"},
        delta=ChangeDelta(
            identity={"server": SERVER_NAME},
            verification=VERIFICATION_VERIFIED,
            new_values={"password": "hunter2"},
        ),
    )

    assert "hunter2" not in json.dumps(receipt)
    assert receipt[RECEIPT_DELTA_KEY]["new_values"]["password"] == REDACTED
    assert receipt["after"]["password"] == REDACTED


def test_a_delivery_url_in_a_delta_is_treated_exactly_as_the_payloads_is() -> None:
    """`delivered_credential` is not a redacted key, and neither is `yopass_url` (V49, V50).

    Stated as a test rather than left implicit, because the two fields hold the same string on
    the same change and a reader comparing them has to be able to. Whoever holds the whole link
    holds the secret, which is why it is kept out of logs — and why the receipt, behind the
    operator's own cookie, is where it is allowed to be.
    """
    receipt = build_receipt(
        evidence=EVIDENCE,
        payload={"ok": True, "yopass_url": YOPASS_URL},
        delta=ChangeDelta(
            identity={"server": SERVER_NAME},
            verification=VERIFICATION_VERIFIED,
            delivered_credential=YOPASS_URL,
        ),
    )

    assert receipt["after"]["yopass_url"] == YOPASS_URL
    assert receipt[RECEIPT_DELTA_KEY]["delivered_credential"] == YOPASS_URL


# --------------------------------------------------------------------------------------
# The executor's seam (V23, V86)
# --------------------------------------------------------------------------------------


def build_service(
    repository: FakeApprovedChangeExecutionRepository,
    runner: RecordingChangeRunner | None,
) -> ApprovedChangeExecutionService:
    """The production service over the doubles, with or without a runner for the tool."""
    return ApprovedChangeExecutionService(
        repository=repository,
        runners={} if runner is None else {authorized_change().tool_name: runner},
    )


async def test_an_executor_refusal_states_no_delta() -> None:
    """Nothing ran, so nothing is claimed — and the receipt says so by omission (V86).

    Driven through the real service rather than against `build_receipt`, because the claim is
    about a path: the three refusals above a runner all return the envelope and no delta, and a
    unit test of the builder would pass with that wiring cut.
    """
    authorized = authorized_change()
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized)

    status = await build_service(repository, None).execute_approved_tool_run(
        action_request_id=authorized.action_request_id,
        tool_run_id=authorized.tool_run_id,
    )

    assert status is ToolRunStatus.FAILED
    receipt = repository.only_receipt.receipt_data
    assert receipt["error_code"] == ERROR_RUNNER_UNAVAILABLE
    assert RECEIPT_DELTA_KEY not in receipt


async def test_a_runner_that_states_a_delta_leaves_the_summary_alone() -> None:
    """The audit field is derived from the payload, and the payload did not grow.

    The two are asserted together because they are one claim about one seam: the receipt gained
    the delta, and `result_summary` — read by the admin surface, by the card's execution line and
    by `noa_get_action_result` — is byte-for-byte what it was.
    """
    authorized = authorized_change()
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized)
    runner = RecordingChangeRunner(delta=MINIMAL)

    await build_service(repository, runner).execute_approved_tool_run(
        action_request_id=authorized.action_request_id,
        tool_run_id=authorized.tool_run_id,
    )

    assert repository.only_receipt.receipt_data[RECEIPT_DELTA_KEY] == MINIMAL.as_payload()
    assert repository.only_finish.result_summary == result_summary(RUNNER_OK)


async def test_a_runner_answering_a_bare_envelope_is_still_a_complete_answer() -> None:
    """The union `ChangeRunner` declares, exercised: a dict is a runner's answer too.

    Not a compatibility shim. A runner with nothing to state should not have to wrap an empty
    object, and the executor normalising it in one place is what keeps that from being seven
    authors' decision (V66).
    """
    authorized = authorized_change()
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized)

    status = await build_service(repository, RecordingChangeRunner()).execute_approved_tool_run(
        action_request_id=authorized.action_request_id,
        tool_run_id=authorized.tool_run_id,
    )

    assert status is ToolRunStatus.COMPLETED
    assert RECEIPT_DELTA_KEY not in repository.only_receipt.receipt_data


def test_the_facet_records_are_all_optional_in_the_type() -> None:
    """Absence is held by the type, not by seven authors remembering to pass `None`.

    Read off the dataclass fields rather than asserted in prose: every facet has a default, so a
    renderer that fabricates a measurement has to work at it, and a new facet added without one
    fails here instead of in a card six months later.
    """
    required = {
        name
        for name, field in ChangeDelta.__dataclass_fields__.items()
        if field.default is field.default_factory is MISSING
    }

    assert required == {"identity", "verification"}


def test_every_facet_the_record_holds_reaches_the_payload() -> None:
    """The other half of the fence: the coupling this would otherwise have moved rather than closed.

    `__post_init__` scans `as_payload()`, which is the right value to scan — but it makes the
    record and its serialiser the pair that can now disagree, where the record and `as_facets`
    were before. A facet added to the dataclass and forgotten in `as_payload` is unscanned by the
    fence **and** unstored in the receipt, which is a quieter failure than the one it replaced,
    not a louder one. So the two are compared directly here: the mechanism is bound at the
    mechanism rather than left to a reader noticing two lists.

    Every facet is filled, because absence is omission — an unset one is legitimately missing from
    the payload, so only a fully populated record can say the two sets are the same set.
    """
    complete = ChangeDelta(
        identity={"server": SERVER_NAME},
        verification=VERIFICATION_UNAVAILABLE,
        verification_cause="task_timeout",
        changed_fields=(FieldChange(field="suspended", old=False, new=True),),
        list_delta=ListDelta(added=("1.2.3.4/32",), total_entries=4),
        backends=(BackendOutcome(name="csf", driven=True, answered=True, verdict="allowlisted"),),
        unanswered=("imunify",),
        delivered_credential=YOPASS_URL,
        new_values={"duration_minutes": 137},
        bound=Bound(total=20, truncated=True),
    )

    assert set(complete.as_payload()) == set(ChangeDelta.__dataclass_fields__)


def test_a_change_outcome_defaults_to_stating_nothing() -> None:
    """The seam's own default, which is the fail-closed direction (V86)."""
    assert ChangeOutcome(payload={"ok": True}).delta is None
    assert ChangeOutcome(payload={"ok": True}, delta=MINIMAL).delta is MINIMAL


def test_the_backend_and_list_facets_omit_what_nothing_measured() -> None:
    """Every nested record follows the top level's rule, so absence is absence all the way down."""
    assert BackendOutcome(name="csf", driven=True, answered=False).as_payload() == {
        "name": "csf",
        "driven": True,
        "answered": False,
    }
    assert ListDelta().as_payload() == {"added": [], "removed": []}
