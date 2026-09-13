"""`whm_unsuspend_account` after an operator approved — the runner and the delta it publishes.

Split out of `test_whm_tools_unsuspend_account.py`, which holds the other half: the preflight,
the refusals, the two answers that open no request, and the mounted `tools/call`. The two files
are one suite in two parts, and the seam between them is the cookie/CSRF boundary the tool and
the runner sit on opposite sides of — everything here runs only because a decision was already
made, and is driven with a `ChangeExecutionRequest` built the way `core.approvals.execution`
builds one, because that is what the executor hands it.

**Why two files and not one.** The repo caps a `.py` file at 900 lines, and
`apps/api/tests/test_config.py` enforces that over `git ls-files` rather than leaving it to a
reviewer's eye. The partner file reached the cap, so the next assertion either lands here or does
not land at all — and trimming an assertion to hold a file under a number is how a check leaves
without anyone deciding it should.

The fixtures below are this file's own copies rather than an import from the partner: a test
module that imports another test module's helpers makes the two collectible only together, and
what each half needs has already diverged — nothing here builds a request context, opens a
session or reads a tool schema.

Seams are the account suite's own, unchanged: the real `WHMClient` over a doubled socket
(`support.whm_api`), because WHM reports a refusal as **HTTP 200** with `metadata.result: 0` and a
doubled client would let this pass against error shapes WHM never sends; a real `SecretCipher`, so
the `Authorization` header proves a decrypt happened; the real resolver. Only the socket, the SQL
and the directory are doubles.

And one absence this half is where to see: `unsuspendacct` takes no note, so the runner writes
nothing out and the `unsuspendacct` query is asserted **exactly**, not by the absence of one key.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from core.approvals.delta import VERIFICATION_MISMATCH, VERIFICATION_VERIFIED
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
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    VERIFICATION_UNAVAILABLE,
)
from noa_api.mcp_tools.whm_account_change_runner import build_whm_unsuspend_runner
from support.action_decisions import REASON
from support.change_delta import delta_of, outcome_of, payload_runner
from support.servers import ToolFixture, build_tool_context, whm_server
from support.whm_api import (
    LISTACCTS_PATH,
    UNSUSPENDACCT_PATH,
    FakeWHMApi,
    listaccts_body,
    whm_account,
    whm_api_failure_body,
    whm_api_success_body,
)

SERVER_NAME = "alpha"
ACCOUNT = "acmeco"

# Who WHM says owns the account, and it has to equal the row's `api_username` or the preflight
# refuses before a card exists — owner, not machine: cPanel gates an account write on ownership,
# so an account with no owner is one NOA cannot prove this credential may change. `whm_server`'s
# credential is `root`, and root owning accounts directly is the measured case — 56 of the 451
# rows on the host this was measured live on. `test_whm_account_owner_gate.py` is where the mismatch
# and the unreported-owner refusals are asserted; here the owner is fixture, not subject.
OWNER = "root"

# The plaintext behind the row's `api_token`, encrypted into the column so a header assertion
# proves a decrypt rather than a passthrough.
WHM_API_TOKEN = "whm-api-token-plaintext"

# WHM's `suspendreason` — the operator's own words, echoed back on every `listaccts`, unreadable
# on every path back to a model. It sits on the row this tool's preflight reads, the case the
# suspend tool could not have: an unsuspend target is suspended right now.
SUSPEND_NOTE_ECHO = "operator words WHM would echo back"


def suspended_account(**extra: Any) -> dict[str, Any]:
    """One `listaccts` row for a suspended account, note included, as WHM sends it."""
    return whm_account(
        ACCOUNT,
        domain="acme.example.com",
        suspended=1,
        suspendreason=SUSPEND_NOTE_ECHO,
        owner=OWNER,
        **extra,
    )


def live_account(**extra: Any) -> dict[str, Any]:
    """The same account once the suspension is lifted (`suspended` as `0`, WHM's spelling)."""
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=0, owner=OWNER, **extra)


def unreadable_state_account() -> dict[str, Any]:
    """The account with a `suspended` the normaliser does not read, WHM's note still on it.

    `account_suspension_state` answers `None` for `maybe`, so the postflight holds a matched row
    and no state — the one could-not-confirm shape that *has* a row, and therefore the only one a
    note can ride in on. The spelling is the unseen-cPanel-version guard
    `test_whm_account_non_answers.py` names; here it is staging, not subject.
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
    unsuspend_body: dict[str, Any] | None = None,
) -> FakeWHMApi:
    """A WHM endpoint that answers `listaccts` from a queue and `unsuspendacct` once.

    A queue rather than one body because a CHANGE workflow reads the same endpoint twice and
    needs two answers: the account was suspended when the operator was asked, and live when the
    runner checked. One body cannot express that, and a test that reused it would pass against a
    postflight that never ran.
    """
    bodies = listaccts_bodies or [
        listaccts_body(rows) for rows in (listings or [[suspended_account()]])
    ]
    return FakeWHMApi(
        body=listaccts_body([]),
        scripted={
            LISTACCTS_PATH: bodies,
            UNSUSPENDACCT_PATH: [unsuspend_body or whm_api_success_body()],
        },
    )


def unsuspend_context(
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
    """What `core.approvals.execution` hands a runner for an approved unsuspension.

    `reason` is carried because the executor carries it for *every* approved change — the
    point of the runner tests below is that this one never sends it anywhere.
    """
    return ChangeExecutionRequest(
        action_request_id=uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_WHM_UNSUSPEND_ACCOUNT,
        arguments={"server_ref": server_ref, "username": username},
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            # The runner re-compares this against the row's live `api_username` — owner as
            # `server_ref` — so evidence without it is an approved change NOA refuses to run.
            EVIDENCE_OWNER: OWNER,
            EVIDENCE_ACCOUNT: {
                "user": username,
                "suspended": True,
                "suspendreason": SUSPEND_NOTE_ECHO,
            },
        },
        reason=reason,
    )


def query_of(request: Any) -> dict[str, str]:
    """One captured request's query parameters."""
    return dict(request.url.params)


# --------------------------------------------------------------------------------------
# The runner: what happens after an operator approved (the far side of the cookie/CSRF boundary)
# --------------------------------------------------------------------------------------


async def test_the_runner_asks_whm_for_the_account_and_nothing_else() -> None:
    """A value the operator types and the LLM never sees, and no path back for it once written:
    `unsuspendacct` has no note field, so nothing leaves NOA on this path.

    Asserted as an **exact** query rather than as the absence of one key name: what the suspend tool
    had to guard is a note field that exists, and what this guards is a runner that grows one later
    under whatever name WHM would call it. An equality goes red for any of them.
    """
    fixture, api = unsuspend_context(whm_endpoint(listings=[[live_account()]]))
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    mutation = api.requests_to(UNSUSPENDACCT_PATH)
    assert len(mutation) == 1
    assert query_of(mutation[0]) == {"api.version": "1", "user": ACCOUNT}
    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is True
    assert payload["suspended"] is False
    assert payload["headline"] == f"Account unsuspended — {ACCOUNT}"
    # **The owner's whole-account sentence is absent here and nothing takes its place.** It states
    # the consequence of a suspension; lifting one produces no new consequence to state, so a
    # mirrored sentence would be a clause nobody measured and nobody supplied. Asserted whole, so
    # a mirror added later reddens here rather than shipping on a card.
    assert payload["message"] == (
        f"The {ACCOUNT} account is no longer suspended on {SERVER_NAME}.\n"
        "It was suspended before this ran."
    )


async def test_the_runner_payload_never_carries_the_reason_back() -> None:
    """V96b: `result_summary` is derived from this payload, and `noa_get_action_result` returns
    the summary to a model.

    The reason is on the `ChangeExecutionRequest` — the executor reads it off the row for every
    approved change — and the evidence carries WHM's older note, so both strings are in
    front of this runner even though it writes neither. Asserted on the derived summary as well
    as on the payload, because the summary is the thing a model actually reads.

    **The note is on the POSTFLIGHT row too, and that is the half this was missing.** The
    confirming read is the second place WHM hands the operator's own words back, and until the
    row carried one this test drove a composer with nothing to leak: splicing `suspendreason`
    into the confirmed branch's sentence yielded the literal `None` and sat green, so the one
    branch-direction this test exists to hold was measured by nothing. A suspension that was
    lifted keeps its `suspendreason` on `listaccts`, so the row below is the ordinary shape
    rather than a contrived one — and the suspend direction already staged it this way, which is
    why only this mirror was open.
    """
    fixture, _ = unsuspend_context(
        whm_endpoint(listings=[[live_account(suspendreason=SUSPEND_NOTE_ECHO)]])
    )
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert REASON not in json.dumps(payload)
    assert SUSPEND_NOTE_ECHO not in json.dumps(payload)
    assert REASON not in (result_summary(payload) or "")


async def test_the_runner_acts_on_the_server_the_card_named() -> None:
    """Context persisted at gate time: inventory can change between a request and its approval, and
    `server_ref` is a string the model supplied. The evidence carries the id of the machine the
    preflight read and the operator saw, so that is what the change reaches — asserted on the
    host WHM was called
    at, which is the only way "it ran somewhere else" would show."""
    alpha = whm_server(SERVER_NAME)
    beta = whm_server("beta")
    fixture, api = unsuspend_context(
        whm_endpoint(listings=[[live_account()]]), servers=[alpha, beta]
    )
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    # The arguments name the other server; only the evidence names alpha.
    await runner(execution_request(server_id=alpha.id, server_ref="beta"))

    hosts = {request.url.host for request in api.requests_to(UNSUSPENDACCT_PATH)}
    assert hosts == {f"{SERVER_NAME}.example.net"}


async def test_a_change_that_did_not_take_is_a_failure() -> None:
    """WHM accepted the call and the account is still suspended. Reporting that as done is the
    fabrication the postflight exists to stop — and the direction of the check is the mutation
    that separates this file from the suspend tool's."""
    fixture, _ = unsuspend_context(whm_endpoint(listings=[[suspended_account()]]))
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_POSTFLIGHT_FAILED
    # Not the negation of the confirmed heading: each direction names its own failure the way an
    # operator would say it, and "still suspended" is the one that says what is true of the
    # account right now.
    assert payload["headline"] == f"Account still suspended — {ACCOUNT}"
    assert payload["message"] == (
        f"NOA read the account back on {SERVER_NAME}: {ACCOUNT} is suspended."
    )


async def test_a_change_whm_accepted_but_could_not_confirm_says_unverified() -> None:
    """The password-reset verdict-on-verify rule one system over, and the third answer
    `_verify_account_state` exists for.

    A failure here would send an operator to re-run a lift that may already have taken; a plain
    success would claim a confirmation nobody has.
    """
    fixture, api = unsuspend_context(
        whm_endpoint(listaccts_bodies=[whm_api_failure_body("Access denied")])
    )
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    # The heading is the change's own, because WHM accepted the call; the corner beside it says
    # the change is unconfirmed, and the sentence says why.
    assert payload["headline"] == f"Account unsuspended — {ACCOUNT}"
    assert "NOA could not read the account back afterwards" in str(payload["message"])
    # No comparison was made and the clause says only that. The reading the operator approved
    # against is still on the evidence and is named in this direction's own vocabulary —
    # `suspended`, which is what an unsuspension starts from — because this is the branch that
    # sends them to WHM to check by hand, and a card that claimed NOA held nothing would
    # contradict the one they approved from.
    assert "It was suspended when NOA last read it." in str(payload["message"])
    assert "before this ran" not in str(payload["message"])
    assert len(api.requests_to(UNSUSPENDACCT_PATH)) == 1


async def test_a_whm_refusal_at_execute_time_keeps_its_own_code() -> None:
    """The mutation itself refused — the case a lock set after the request was opened lands in.

    `whm_api_error` and WHM's `reason` travel to the receipt, because "WHM said no" and "NOA
    broke" send an administrator to different systems, and WHM's sentence is the one that names
    what to unlock — spliced into a longer sentence now, since the account is re-read first.
    """
    fixture, _ = unsuspend_context(
        whm_endpoint(unsuspend_body=whm_api_failure_body("Account suspension is locked"))
    )
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == "whm_api_error"
    assert "Account suspension is locked." in str(payload["message"])
    assert payload["headline"] == f"Account still suspended — {ACCOUNT}"
    # The code stays on the envelope. WHM's own sentence is the half of this failure an operator
    # can act on, and the raw token beside it names nothing to them.
    assert "whm_api_error" not in str(payload["message"])


async def test_a_server_that_vanished_after_approval_is_refused_before_the_mutation() -> None:
    """Fail closed on the far side of the boundary too: the row the operator approved against
    is gone, so the change does not run against whatever `server_ref` resolves to today."""
    fixture, api = unsuspend_context()
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=uuid4()))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert api.requests_to(UNSUSPENDACCT_PATH) == []


async def test_evidence_without_a_usable_server_id_is_refused() -> None:
    """The evidence round-tripped through JSONB. A value that no longer parses as a UUID is a
    request NOA refuses rather than guesses at."""
    fixture, api = unsuspend_context()
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id="not-a-uuid"))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert api.requests == []


# --------------------------------------------------------------------------------------
# The delta the runner publishes beside its envelope
# --------------------------------------------------------------------------------------


async def test_the_unsuspend_delta_moves_the_same_field_the_other_way() -> None:
    """The mirror of the suspend tool's delta, and the only thing that differs is the direction's
    value.

    One postflight serves both tools and `target_suspended` is the whole difference, so
    this is the assertion that a mutation flipping it turns the change's meaning over — in the
    delta as well as in the payload, since the `new` side is read from the same field.
    """
    fixture, _ = unsuspend_context(whm_endpoint(listings=[[live_account()]]))
    runner = build_whm_unsuspend_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["identity"] == {"server": SERVER_NAME, "username": ACCOUNT}
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == [{"field": "suspended", "old": True, "new": False}]


async def test_the_unsuspend_delta_never_carries_the_earlier_note() -> None:
    """A value the operator types and the LLM never sees, and no path back for it once written: this
    runner writes nothing out, and it still has a return path to close.

    An account being unsuspended *is* suspended when the preflight reads it, so its summary
    carries `suspendreason` — an operator's words from the earlier suspension — and that summary
    is on the evidence this runner resolves its target from. The delta's identity is two strings
    rather than that summary, so the words have nowhere to ride.
    """
    fixture, _ = unsuspend_context(whm_endpoint(listings=[[live_account()]]))
    runner = build_whm_unsuspend_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    rendered = json.dumps(delta.as_payload())
    assert REASON not in rendered
    assert SUSPEND_NOTE_ECHO not in rendered


async def test_a_confirming_read_that_did_not_answer_claims_no_diff() -> None:
    """WHM accepted the call and could not be re-read: the change happened, unconfirmed.

    Absent rather than empty. Reporting an empty diff would say the account was looked at and
    had not moved, which is the opposite of what a read that did not answer establishes — and
    reporting the change as failed would send an operator to lift a suspension that is already
    lifted (the password-reset verdict-on-verify rule, one system over).
    """
    fixture, _ = unsuspend_context(
        whm_endpoint(listaccts_bodies=[whm_api_failure_body("Access denied")])
    )
    runner = build_whm_unsuspend_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["verification_cause"] == "whm_api_error"
    assert "changed_fields" not in payload


# --------------------------------------------------------------------------------------
# The reason boundary on the postflight branches that held no run with a note in front of them
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "mutation_body", "error_code", "verification", "verification_cause"),
    [
        pytest.param(
            suspended_account(),
            None,
            ERROR_POSTFLIGHT_FAILED,
            VERIFICATION_MISMATCH,
            None,
            id="the-change-did-not-take",
        ),
        pytest.param(
            suspended_account(),
            whm_api_failure_body("Account suspension is locked"),
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

    `test_the_runner_payload_never_carries_the_reason_back` above reads the payload back on the
    **verified** branch. The other three answers of `_verify_account_state` rested on a reading of
    the code — the matched row is reduced to one boolean the line after it arrives and never read
    again — and a reading is an argument, not a measurement. Each case here puts a note-bearing row
    in front of one of them and asserts the same property.

    **This direction is where the hazard is WHM's own rather than planted.** The two failure cases
    read an account that is *still suspended*, which is exactly the row WHM echoes a previous
    decision's `suspendreason` on — the words an operator typed into a NOA card, coming back off
    the target system on the branch that reports the lift did not take. The suspend direction
    plants the same field on a live row, and `test_whm_tools_suspend_runner.py` holds that mirror.

    **One shape of could-not-confirm is not staged here and cannot be.** Where WHM refuses the
    confirming read outright, no row arrives at all (`verified is None`), so there is no
    note-bearing row to put in front of the composer and nothing this test could add.
    `test_a_change_whm_accepted_but_could_not_confirm_says_unverified` drives that branch, and what
    it leaves open is a branch whose only material is the error code and the gate-time reading —
    stated rather than papered over, because a case pretending to stage it would read as coverage.

    Each case asserts the branch it means to drive before the property, since a fixture that
    stopped landing there would otherwise pass with the hazard nowhere near the composer.
    """
    fixture, _ = unsuspend_context(whm_endpoint(listings=[[row]], unsuspend_body=mutation_body))
    runner = build_whm_unsuspend_runner(context=fixture.context)

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
