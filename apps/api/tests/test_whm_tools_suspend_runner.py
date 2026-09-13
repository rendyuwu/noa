"""`whm_suspend_account` after an operator approved — the runner and the delta it publishes.

Split out of `test_whm_tools_suspend_account.py`, which holds the other two lanes: the preflight,
the refusals, the no-op, the gate response, and the mounted `tools/call`. The two files are one
suite in two parts, and the seam between them is the cookie/CSRF boundary the tool and the runner
sit on opposite sides of — everything here runs only because a decision was already made, and is
driven with a `ChangeExecutionRequest` built the way `core.approvals.execution` builds one,
because that is what the executor hands it.

**Why two files and not one.** The repo caps a `.py` file at 900 lines, and
`apps/api/tests/test_config.py` enforces that over `git ls-files` rather than leaving it to a
reviewer's eye. The partner file reached the cap, so the next assertion either lands here or does
not land at all — and trimming an assertion to hold a file under a number is how a check leaves
without anyone deciding it should. The unsuspend direction was divided at this same seam earlier,
into `test_whm_tools_unsuspend_account.py` and `test_whm_tools_unsuspend_runner.py`, and the two
directions are worth the same shape: a reader who has found a test in one then knows where its
mirror lives in the other.

The fixtures below are this file's own copies rather than an import from the partner: a test
module that imports another test module's helpers makes the two collectible only together, and
what each half needs has already diverged — nothing here builds a request context, opens a
session or reads a tool schema.

Seams are the account suite's own, unchanged: the real `WHMClient` over a doubled socket
(`support.whm_api`), because WHM reports a refusal as **HTTP 200** with `metadata.result: 0` and a
doubled client would let this pass against error shapes WHM never sends; a real `SecretCipher`, so
the `Authorization` header proves a decrypt happened; the real resolver. Only the socket, the SQL
and the directory are doubles.

**The reason is the thing to watch, and this half is where it moves.** `suspendacct` has a
suspension-note field, so the operator's typed reason leaves NOA here and nowhere else in the
family — asserted on the wire, where WHM receives it, and asserted absent from every path that
leads back to a model.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from core.approvals.delta import (
    VERIFICATION_MISMATCH,
    VERIFICATION_VERIFIED,
)
from core.approvals.execution import ChangeExecutionRequest
from core.audit.summaries import result_summary
from core.db.models import WHMServer
from noa_api.mcp_tools.whm_account_change import (
    ERROR_POSTFLIGHT_FAILED,
    ERROR_SERVER_UNAVAILABLE,
    ERROR_SUSPENSION_STATE_UNREADABLE,
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    STATUS_CHANGED,
    TOOL_WHM_SUSPEND_ACCOUNT,
    VERIFICATION_UNAVAILABLE,
)
from noa_api.mcp_tools.whm_account_change_runner import build_whm_suspend_runner
from support.action_decisions import REASON
from support.change_delta import delta_of, outcome_of, payload_runner
from support.servers import ToolFixture, build_tool_context, whm_server
from support.whm_api import (
    LISTACCTS_PATH,
    SUSPENDACCT_PATH,
    FakeWHMApi,
    listaccts_body,
    whm_account,
    whm_api_failure_body,
    whm_api_success_body,
)

SERVER_NAME = "alpha"
ACCOUNT = "acmeco"

# Who WHM says owns the account, and it has to equal the row's `api_username` or the preflight
# refuses before a card exists — the owner-match check: cPanel gates an account write on ownership,
# so an account with no owner is one NOA cannot prove this credential may change. `whm_server`'s
# credential is `root`, and root owning accounts directly is the measured case — 56 of the 451 rows
# on the live host that ownership finding was measured on. `test_whm_account_owner_gate.py` is where
# the mismatch and the unreported-owner refusals are asserted; here the owner is fixture, not
# subject.
OWNER = "root"

# The plaintext behind the row's `api_token`, encrypted into the column so a header assertion
# proves a decrypt rather than a passthrough.
WHM_API_TOKEN = "whm-api-token-plaintext"

# What WHM echoes back in `suspendreason` once NOA has written the operator's reason there.
# Planted on the *already suspended* row, so a payload that carried the field would be carrying
# an operator's words back to the model.
SUSPEND_NOTE_ECHO = "operator words WHM would echo back"


def live_account(**extra: Any) -> dict[str, Any]:
    """One `listaccts` row for a running account, as WHM sends it (`suspended` as `0`)."""
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=0, owner=OWNER, **extra)


def suspended_account(**extra: Any) -> dict[str, Any]:
    """The same account after a suspension, note included."""
    return whm_account(
        ACCOUNT,
        domain="acme.example.com",
        suspended=1,
        suspendreason=SUSPEND_NOTE_ECHO,
        owner=OWNER,
        **extra,
    )


def unreadable_state_account() -> dict[str, Any]:
    """The same account with a `suspended` the normaliser does not read, note included.

    `account_suspension_state` answers `None` for `maybe`, so the postflight holds a matched row
    and no state — which makes this the one could-not-confirm shape that *has* a row, and
    therefore the only one a note can be planted on. The spelling is the guard against an unseen
    cPanel version `test_whm_account_non_answers.py` names; here it is staging, not subject.
    """
    return whm_account(
        ACCOUNT,
        domain="acme.example.com",
        suspended="maybe",
        owner=OWNER,
        suspendreason=SUSPEND_NOTE_ECHO,
    )


def whm_endpoint(
    *,
    listings: list[list[dict[str, Any]]] | None = None,
    listaccts_bodies: list[dict[str, Any]] | None = None,
    suspend_body: dict[str, Any] | None = None,
) -> FakeWHMApi:
    """A WHM endpoint that answers `listaccts` from a queue and `suspendacct` once.

    A queue rather than one body because a CHANGE workflow reads the same endpoint twice and
    needs two answers: the account was live when the operator was asked, and suspended when the
    runner checked. One body cannot express that, and a test that reused it would pass against a
    postflight that never ran.
    """
    bodies = listaccts_bodies or [listaccts_body(rows) for rows in (listings or [[live_account()]])]
    return FakeWHMApi(
        body=listaccts_body([]),
        scripted={
            LISTACCTS_PATH: bodies,
            SUSPENDACCT_PATH: [suspend_body or whm_api_success_body()],
        },
    )


def suspend_context(
    endpoint: FakeWHMApi | None = None,
    *,
    servers: list[WHMServer] | None = None,
) -> tuple[ToolFixture, FakeWHMApi]:
    """A tool context whose WHM endpoint is a `MockTransport` over the production client."""
    api = endpoint or whm_endpoint()
    cipher = build_tool_context().cipher
    rows = servers if servers is not None else [whm_server(SERVER_NAME)]
    for row in rows:
        row.api_token = cipher.encrypt_text(WHM_API_TOKEN)
    fixture = build_tool_context(servers=rows, cipher=cipher, whm_transport=api.transport)
    return fixture, api


def execution_request(
    *,
    server_id: UUID | str,
    username: str = ACCOUNT,
    reason: str = REASON,
    server_ref: str = SERVER_NAME,
) -> ChangeExecutionRequest:
    """What `core.approvals.execution` hands a runner for an approved suspension."""
    return ChangeExecutionRequest(
        action_request_id=uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
        arguments={"server_ref": server_ref, "username": username},
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            # The runner re-compares this against the row's live `api_username` — the owner
            # check, held from gate time, not rebuilt — so evidence without it is an approved
            # change NOA refuses to run.
            EVIDENCE_OWNER: OWNER,
            EVIDENCE_ACCOUNT: {"user": username, "suspended": False},
        },
        reason=reason,
    )


def query_of(request: Any) -> dict[str, str]:
    """One captured request's query parameters."""
    return dict(request.url.params)


# --------------------------------------------------------------------------------------
# The runner: what happens after an operator approved (the far side of the cookie/CSRF boundary)
# --------------------------------------------------------------------------------------


async def test_the_runner_sends_the_operator_reason_as_whms_suspension_note() -> None:
    """The reason field, written where WHM keeps a suspension note.

    The reason is the operator's own words, typed on the card after the model was done. This is
    the one place it leaves NOA, and it leaves as `suspendacct`'s `reason` parameter — asserted
    on the wire, because "the runner passed it along" is a claim about the request WHM receives.
    """
    api = whm_endpoint(listings=[[suspended_account()]])
    fixture, api = suspend_context(api)
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    mutation = api.requests_to(SUSPENDACCT_PATH)
    assert len(mutation) == 1
    assert query_of(mutation[0]) == {"api.version": "1", "user": ACCOUNT, "reason": REASON}
    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is True
    # The card's heading, composed here rather than derived from the tool name: `Whm Suspend
    # Account` names the machinery, and this names what happened to the account.
    assert payload["headline"] == f"Account suspended — {ACCOUNT}"
    # The owner's own words for what a suspension does, stated once. Nothing mirrors this on the
    # unsuspend runner — lifting a suspension produces no new consequence to state — and that
    # absence is asserted in `test_whm_tools_unsuspend_account.py`.
    assert "The whole account — nothing on it is reachable." in str(payload["message"])


async def test_the_runner_payload_never_carries_the_reason_back() -> None:
    """A value kept from the LLM must stay unreadable on every path back: `result_summary` is
    derived from this payload, and `noa_get_action_result` returns the summary to a model — so a
    runner echoing the note it just wrote would hand the LLM the one field the reason rule keeps
    from it, through the audit row rather than through a tool schema.

    Asserted on the derived summary as well as on the payload, because the summary is the thing a
    model actually reads: a payload assertion alone would still pass if `result_summary` ever
    started composing its own text from fields this one happens not to carry.

    **Not through the receipt** — the action-result tool's reader takes two scalars off
    `action_receipts`, the delta's `verification` and `verification_cause` lifted out of the JSONB
    in SQL, so no receipt row enters that process; rendering the receipt is the approval card's
    own job. Naming that door here would point a future runner's author at the wrong field.
    """
    api = whm_endpoint(listings=[[suspended_account()]])
    fixture, _ = suspend_context(api)
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert REASON not in json.dumps(payload)
    assert SUSPEND_NOTE_ECHO not in json.dumps(payload)
    assert REASON not in (result_summary(payload) or "")


async def test_the_runner_acts_on_the_server_the_card_named() -> None:
    """Inventory can change between a request and its approval, and `server_ref` is a
    string the model supplied. The evidence carries the id of the machine the preflight read and
    the operator saw, so that is what the change reaches — asserted on the host WHM was called
    at, which is the only way "it ran somewhere else" would show."""
    alpha = whm_server(SERVER_NAME)
    beta = whm_server("beta")
    api = whm_endpoint(listings=[[suspended_account()]])
    fixture, api = suspend_context(api, servers=[alpha, beta])
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    # The arguments name the other server; only the evidence names alpha.
    await runner(execution_request(server_id=alpha.id, server_ref="beta"))

    hosts = {request.url.host for request in api.requests_to(SUSPENDACCT_PATH)}
    assert hosts == {f"{SERVER_NAME}.example.net"}


async def test_a_change_that_did_not_take_is_a_failure() -> None:
    """WHM accepted the call and the account is still live. Reporting that as done is the
    fabrication the postflight exists to stop."""
    api = whm_endpoint(listings=[[live_account()]])
    fixture, _ = suspend_context(api)
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_POSTFLIGHT_FAILED
    assert payload["headline"] == f"Account not suspended — {ACCOUNT}"
    # Asserted whole rather than by substring, because the absence is half the claim: this
    # sentence **is** the measurement, so no before-clause line follows it. Two statements of one
    # reading read as two readings.
    assert payload["message"] == (
        f"NOA read the account back on {SERVER_NAME}: {ACCOUNT} is not suspended."
    )


async def test_a_change_whm_accepted_but_could_not_confirm_says_unverified() -> None:
    """The third answer, and the reason `_verify_account_state` is a function rather than a bool
    (the verdict-on-verify rule, one system over). A failure here would send an operator to
    re-suspend an account
    that may already be suspended; a plain success would claim a confirmation nobody has."""
    api = whm_endpoint(listaccts_bodies=[whm_api_failure_body("Access denied")])
    fixture, api = suspend_context(api)
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    # The heading is the change's own, because the commands were accepted — what is unconfirmed is
    # stated in the sentence, and the corner reads it off the verification state.
    assert payload["headline"] == f"Account suspended — {ACCOUNT}"
    # Nothing was compared on this branch by construction — the postflight produced no reading to
    # compare against — so the before-clause never takes the measured-empty spelling, which would
    # claim a comparison that could not have happened. It does still name the gate-time reading:
    # "when NOA last read it" claims a reading and no comparison, which is exactly what NOA holds
    # here, and this is the branch that sends an operator to WHM to check by hand.
    assert "It was not suspended when NOA last read it." in str(payload["message"])
    assert "before this ran" not in str(payload["message"])
    assert len(api.requests_to(SUSPENDACCT_PATH)) == 1


async def test_a_whm_refusal_at_execute_time_keeps_its_own_code() -> None:
    """The mutation itself refused. `whm_api_error` and WHM's `reason` travel to the receipt,
    because "WHM said no" and "NOA broke" send an administrator to different systems.

    The sentence now carries the confirming read as well, and the code is unchanged: a refusal
    that also has a reading behind it is strictly more than the refusal alone, and the code is
    what an administrator branches on.
    """
    api = whm_endpoint(suspend_body=whm_api_failure_body("Account is locked"))
    fixture, _ = suspend_context(api)
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == "whm_api_error"
    assert "Account is locked." in str(payload["message"])
    # The reading disagrees with what was asked for, so the heading says so.
    assert payload["headline"] == f"Account not suspended — {ACCOUNT}"
    # The code stays on the envelope, where an administrator looks for it. It names a remedy to
    # an engineer and names nothing to the operator reading the sentence, whose useful half is
    # WHM's own words above.
    assert "whm_api_error" not in str(payload["message"])


async def test_a_server_that_vanished_after_approval_is_refused_before_the_mutation() -> None:
    """Fail closed on the far side of the boundary too: the row the operator approved against
    is gone, so the change does not run against whatever `server_ref` resolves to today."""
    fixture, api = suspend_context()
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=uuid4()))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert api.requests_to(SUSPENDACCT_PATH) == []


async def test_evidence_without_a_usable_server_id_is_refused() -> None:
    """The evidence round-tripped through JSONB. A value that no longer parses as a UUID is a
    request NOA refuses rather than guesses at."""
    fixture, api = suspend_context()
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id="not-a-uuid"))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert api.requests == []


# --------------------------------------------------------------------------------------
# The delta the runner publishes beside its envelope
# --------------------------------------------------------------------------------------


async def test_the_suspend_delta_names_the_one_field_it_moved() -> None:
    """One field, both sides measured somewhere real.

    The `old` side is the gate-time reading off the evidence — the state the operator authorised
    against — and the `new` side is the direction's own target, confirmed by the postflight
    before this branch is reached. Re-reading the `old` side in the runner would be a second
    reading, and a delta about a decision nobody made.

    The line an operator reads is asserted beside the facet, here and in the two tests below,
    because both are composed from the one tuple: the card's before-clause and the audit drawer's
    field change cannot state two different before-values, and this is the pair that says so.
    """
    fixture, _ = suspend_context(whm_endpoint(listings=[[suspended_account()]]))
    runner = build_whm_suspend_runner(context=fixture.context)

    outcome = await outcome_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    delta = outcome.delta
    assert delta is not None
    payload = delta.as_payload()
    assert payload["identity"] == {"server": SERVER_NAME, "username": ACCOUNT}
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == [{"field": "suspended", "old": False, "new": True}]
    # One row: the two sides were read and they differ, which is the ordinary confirmed change.
    assert "It was not suspended before this ran." in str(outcome.payload["message"])


async def test_evidence_that_never_recorded_the_field_states_no_field_change() -> None:
    """One side of the comparison is missing, so no comparison is stated.

    The account summary on the evidence carries no `suspended` key — a row opened before the key
    existed, or one whose summary did not survive its JSONB round trip as WHM wrote it. The change
    itself is unaffected: WHM accepted it and the postflight confirms the account is suspended. But
    NOA compared nothing, so the facet is **absent** rather than empty — an empty diff here would
    read as "NOA checked and the account did not move" about a change that verifiably moved it, and
    that is the benign value standing in for unknown.

    Paired with the control below, which is the same runner on the same endpoint with the evidence
    carrying a before-value that matches. Without the pair, a builder answering `()` for both would
    pass whichever of the two was written alone.
    """
    fixture, _ = suspend_context(whm_endpoint(listings=[[suspended_account()]]))
    runner = build_whm_suspend_runner(context=fixture.context)
    request = execution_request(server_id=fixture.servers.servers[0].id)
    # Replaced rather than merged: what is being arranged is the *absence* of the key.
    request.evidence[EVIDENCE_ACCOUNT] = {"user": ACCOUNT}

    outcome = await outcome_of(runner, request)

    delta = outcome.delta
    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert "changed_fields" not in payload
    # The absent facet in words. NOA states that it holds no reading rather than naming a value,
    # because an `old` side nobody recorded is not an `old` side of `false`.
    #
    # This is the sentence's one true meaning, and the confirming-read test above is what holds it
    # to that: there, the postflight could not confirm and the evidence *did* carry a reading, and
    # the card names it. Here nothing was ever recorded, so there is nothing to name.
    assert "NOA has no reading of what it was before." in str(outcome.payload["message"])
    assert "when NOA last read it" not in str(outcome.payload["message"])


async def test_a_before_value_that_already_matched_renders_a_measured_empty_diff() -> None:
    """Both sides present and equal: NOA compared, and the reading did not move.

    The control for the case above. The tool answers `no_op` instead of gating when the account is
    already suspended, so the way here is a row whose account moved and moved back while the
    request sat pending — the operator authorised against a suspended reading, and a suspended
    reading is what the postflight found. An empty diff is the truthful answer, and it is a
    different claim from the absent facet above.
    """
    fixture, _ = suspend_context(whm_endpoint(listings=[[suspended_account()]]))
    runner = build_whm_suspend_runner(context=fixture.context)
    request = execution_request(server_id=fixture.servers.servers[0].id)
    request.evidence[EVIDENCE_ACCOUNT] = {"user": ACCOUNT, "suspended": True}

    outcome = await outcome_of(runner, request)

    delta = outcome.delta
    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == []
    # The measured-empty spelling, and the third of the three that never fold: "it already read
    # suspended" is a comparison NOA made, which is a different claim from holding no reading.
    assert "It already read suspended before this ran." in str(outcome.payload["message"])


async def test_the_suspend_delta_never_carries_the_note_it_wrote() -> None:
    """The reason rule, on the delta: this runner is where the words genuinely leave NOA.

    WHM stores the operator's reason as the suspension note and echoes it back as
    `suspendreason` on every later `listaccts` row — including the postflight read this runner
    takes. So the words can arrive here from the *target system* as well as from the request, and
    the identity is built from two strings rather than from the summary that carries them.
    `ChangeDelta` refuses a `suspendreason` key outright; the point of building the identity by
    hand is that the refusal never has to fire.
    """
    fixture, _ = suspend_context(whm_endpoint(listings=[[suspended_account()]]))
    runner = build_whm_suspend_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    rendered = json.dumps(delta.as_payload())
    assert REASON not in rendered
    assert SUSPEND_NOTE_ECHO not in rendered


async def test_a_change_that_did_not_take_publishes_a_measured_empty_diff() -> None:
    """WHM accepted the call and the account is still live: measured, and disagreeing.

    An empty `changed_fields` rather than an absent one, because the account *was* re-read. That
    is the whole distinction the facet carries — this branch compared and found nothing moved,
    while the refusal below never compared at all.
    """
    fixture, _ = suspend_context(whm_endpoint(listings=[[live_account()]]))
    runner = build_whm_suspend_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_MISMATCH
    assert payload["changed_fields"] == []


async def test_a_mutation_whm_refused_now_carries_the_reading_that_agrees_with_it() -> None:
    """A refusal used to be reported with nothing behind it. It is read back now.

    WHM answering `result:0` is WHM saying what it did, which was nothing — so the account is
    re-read and, where the reading agrees, the two together are a measurement rather than an
    absence: `mismatch` with an empty diff, not `unavailable` with no diff at all. The empty
    diff is earned here, and that is the whole difference from a call that went unanswered,
    which cannot earn it because the change may still land.
    """
    fixture, _ = suspend_context(
        whm_endpoint(suspend_body=whm_api_failure_body("Account is locked"))
    )
    runner = build_whm_suspend_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_MISMATCH
    assert payload["changed_fields"] == []


async def test_a_server_that_vanished_after_approval_publishes_no_delta() -> None:
    """Nothing was asked of WHM, so nothing is stated.

    The refusal above the mutation is where a delta is absent rather than empty, and it is the
    same shape the executor's own three refusals take: no identity was resolved, no credential
    was proven, no command was sent.
    """
    fixture, _ = suspend_context()
    runner = build_whm_suspend_runner(context=fixture.context)

    assert await delta_of(runner, execution_request(server_id=uuid4())) is None


# --------------------------------------------------------------------------------------
# The reason boundary on the postflight branches that held no run with a note in front of them
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "mutation_body", "error_code", "verification", "verification_cause"),
    [
        pytest.param(
            live_account(suspendreason=SUSPEND_NOTE_ECHO),
            None,
            ERROR_POSTFLIGHT_FAILED,
            VERIFICATION_MISMATCH,
            None,
            id="the-change-did-not-take",
        ),
        pytest.param(
            live_account(suspendreason=SUSPEND_NOTE_ECHO),
            whm_api_failure_body("Account is locked"),
            "whm_api_error",
            VERIFICATION_MISMATCH,
            None,
            id="whm-refused-the-write",
        ),
        pytest.param(
            unreadable_state_account(),
            None,
            None,
            VERIFICATION_UNAVAILABLE,
            ERROR_SUSPENSION_STATE_UNREADABLE,
            id="whm-did-not-say-whether-it-is-suspended",
        ),
    ],
)
async def test_no_postflight_branch_carries_the_operator_words_back(
    row: dict[str, Any],
    mutation_body: dict[str, Any] | None,
    error_code: str | None,
    verification: str,
    verification_cause: str | None,
) -> None:
    """The reason rule on every postflight branch that holds a row, not only the confirmed one.

    `test_the_runner_payload_never_carries_the_reason_back` above plants the note and reads the
    payload back on the **verified** branch. The other three answers of `_verify_account_state`
    rested on a reading of the code — the matched row is reduced to one boolean the line after it
    arrives and never read again — and a reading is an argument, not a measurement. Each case here
    puts a note-bearing row in front of one of them and asserts the same property.

    **The note is planted, and on this direction that is more hazard than WHM sends.** WHM echoes
    the operator's typed NOA reason back as `suspendreason` on a *suspended* row; the two failure
    cases here read an account WHM says is live, which on a real host would likely carry no note at
    all. Planting it anyway is the point: the property is that nothing composed below is built from
    the row, so the fixture is deliberately more generous with the words than the target system is.
    The unsuspend direction is where the same row shape is WHM's own — every suspended row it reads
    back carries an earlier decision's words — and `test_whm_tools_unsuspend_runner.py` holds that
    mirror.

    **One shape of could-not-confirm is not staged here and cannot be.** Where WHM refuses the
    confirming read outright, no row arrives at all (`verified is None`), so there is no
    note-bearing row to put in front of the composer and nothing this test could add.
    `test_a_change_whm_accepted_but_could_not_confirm_says_unverified` drives that branch, and what
    it leaves open is a branch whose only material is the error code and the gate-time reading —
    stated rather than papered over, because a case pretending to stage it would read as coverage.

    Each case asserts the branch it means to drive before the property, since a fixture that
    stopped landing there would otherwise pass with the hazard nowhere near the composer.
    """
    fixture, _ = suspend_context(whm_endpoint(listings=[[row]], suspend_body=mutation_body))
    runner = build_whm_suspend_runner(context=fixture.context)

    outcome = await outcome_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    delta = outcome.delta
    assert delta is not None
    delta_payload = delta.as_payload()
    assert outcome.payload.get("error_code") == error_code
    assert delta_payload["verification"] == verification
    assert delta_payload.get("verification_cause") == verification_cause
    # The three surfaces one run opens towards a model, rendered together: the envelope,
    # the summary `result_summary` derives from it and `noa_get_action_result` hands back, and the
    # delta an administrator reads out of the audit drawer.
    readable = json.dumps([outcome.payload, result_summary(outcome.payload), delta_payload])
    assert REASON not in readable
    assert SUSPEND_NOTE_ECHO not in readable
