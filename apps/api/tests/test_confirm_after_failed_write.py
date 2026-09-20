"""A write that failed is not an answer yet: consult the state once, then report.

The incident behind this file is a real suspension. WHM completed it, NOA stopped waiting, and
the operator was told `{"ok": false, "error_code": "timeout"}` about an account that was, at that
moment, suspended. The account was read back a line later — every CHANGE runner already re-reads
its target — and the reading was thrown away because the call had failed first.

**The asymmetry is the whole of the fix and it is not optional.** A positive reading after a call
that never answered is conclusive: NOA sent the write, the remote took the connection, and the
state is what was asked for. A *negative* reading after the same call is not, because a change
landing a second after NOA gave up reads exactly like one that never landed — so it is reported
as an unknown outcome and never as "this did not happen". Five rows come out of that, and this
file drives all five against the WHM account pair, which is the tool the incident happened on:

| the confirming read says | the write failed because | reported as |
|---|---|---|
| the state matches | nothing answered | `verified`, no cause, and no hedge about attribution |
| the state does not match | nothing answered | `unavailable`, cause the write's code |
| the state does not match | the remote refused | `mismatch` — a refusal with a reading behind it |
| the state matches | the remote refused | `unavailable`, cause the refusal, reading named |
| the confirming read itself failed | anything | `unavailable`, cause the **read's** code |

Row one carries no attribution qualifier, and that is a decision rather than an omission:
strictly, a state that matches after a timeout could have been reached by somebody else during
the hour a card lives. Attaching "NOA cannot prove it caused this" to every timeout buys nothing
and costs something real — an operator who reads the same qualifier on every timeout learns to
skip it, and a hedge nobody reads degrades the surface it sits on. Row four is the one place a
qualifier is factually required, and it is required *because* the remote spoke: it said it did
nothing, so something other than this change put the state there.

The seams are the account tests' own: the real `WHMClient` over a doubled socket, the real
cipher, the real resolver. Only the socket is replaced, because the classification under test
reads error codes the client mints and a doubled client would let a test pass against codes WHM
never produces.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from core.approvals.delta import (
    VERIFICATION_MISMATCH,
    VERIFICATION_UNAVAILABLE,
    VERIFICATION_VERIFIED,
    ChangeDelta,
)
from core.approvals.execution import ChangeExecutionRequest
from noa_api.mcp_tools.change_runners import build_change_runners
from noa_api.mcp_tools.change_target import (
    NON_ANSWER_ERROR_CODES,
    WriteFailure,
    confirmed_verification,
)
from noa_api.mcp_tools.proxmox_task import ERROR_TASK_TIMEOUT
from noa_api.mcp_tools.results import ERROR_TIMEOUT
from noa_api.mcp_tools.whm_account_change import (
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    TOOL_WHM_SUSPEND_ACCOUNT,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    WHM_CONFIRM_READ_TIMEOUT_SECONDS,
    build_whm_suspend_runner,
)
from support.action_decisions import REASON
from support.change_delta import outcome_of
from support.servers import ToolFixture, build_tool_context, whm_server
from support.whm_api import (
    LISTACCTS_PATH,
    SUSPENDACCT_PATH,
    listaccts_body,
    whm_account,
    whm_api_failure_body,
)

SERVER_NAME = "alpha"
ACCOUNT = "acmeco"
OWNER = "root"
WHM_API_TOKEN = "whm-api-token-plaintext"

# WHM's own refusal of the mutation: HTTP 200 with `metadata.result: 0`. The remote has told NOA
# what it did, which is nothing — the one shape that makes a disagreeing read conclusive.
LOCKED = "Account suspension is locked"


# --------------------------------------------------------------------------------------
# One WHM endpoint that can stage a call which never answers
# --------------------------------------------------------------------------------------


class _WHMEndpoint:
    """A `/json-api/` transport that can time out, refuse, or answer, per path.

    `FakeWHMApi` covers the answering cases and has no way to stage a call that never comes
    back, which is the whole subject here. Raising `httpx.TimeoutException` from the transport
    is what the real client classifies as `timeout`, so the code under test is minted by
    production rather than planted.
    """

    def __init__(
        self,
        *,
        suspended_after: bool,
        mutation: dict[str, Any] | None = None,
        mutation_error: type[httpx.HTTPError] = httpx.TimeoutException,
        confirm_status: int = 200,
    ) -> None:
        self._suspended_after = suspended_after
        self._mutation = mutation
        self._mutation_error = mutation_error
        self._confirm_status = confirm_status
        self.timeouts: dict[str, object] = {}

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.timeouts[path] = request.extensions.get("timeout")
        if path == SUSPENDACCT_PATH and self._mutation is None:
            raise self._mutation_error("suspendacct never answered")
        if path == SUSPENDACCT_PATH:
            return httpx.Response(200, json=self._mutation, request=request)
        return httpx.Response(
            self._confirm_status,
            json=listaccts_body(
                [whm_account(ACCOUNT, suspended=1 if self._suspended_after else 0, owner=OWNER)]
            ),
            request=request,
        )


def _context(endpoint: _WHMEndpoint) -> ToolFixture:
    cipher = build_tool_context().cipher
    row = whm_server(SERVER_NAME)
    row.api_token = cipher.encrypt_text(WHM_API_TOKEN)
    return build_tool_context(servers=[row], cipher=cipher, whm_transport=endpoint.transport)


def _request(server_id: UUID, *, account: dict[str, Any] | None = None) -> ChangeExecutionRequest:
    """What the executor hands the suspend runner for an approved, not-yet-suspended account.

    `account` is the gate-time summary, and every row above shares the default: the operator
    approved against an account WHM said was not suspended. Overridden in one place only, to
    arrange the *absence* of the `suspended` key — which is why it is replaced whole rather than
    merged into.
    """
    return ChangeExecutionRequest(
        action_request_id=uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
        arguments={"server_ref": SERVER_NAME, "username": ACCOUNT},
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            EVIDENCE_OWNER: OWNER,
            EVIDENCE_ACCOUNT: account or {"user": ACCOUNT, "suspended": False},
        },
        reason=REASON,
    )


async def _run(endpoint: _WHMEndpoint, *, account: dict[str, Any] | None = None) -> Any:
    fixture = _context(endpoint)
    return await outcome_of(
        build_whm_suspend_runner(context=fixture.context),
        _request(fixture.servers.servers[0].id, account=account),
    )


# --------------------------------------------------------------------------------------
# The five rows
# --------------------------------------------------------------------------------------


async def test_a_write_that_never_answered_over_a_change_that_landed_reports_it_as_landed() -> None:
    """The incident, answered.

    WHM did not reply and the account is suspended. The state is the better witness than a
    deadline NOA chose, so this is a plain confirmed suspension — no cause, no qualifier about
    whether NOA is the one that caused it.

    The sentence still names the call that did not answer, which is what keeps "confirmed" from
    reading as "answered": an operator who needs to know the call itself went badly can see it
    without being told the change did not happen.
    """
    outcome = await _run(_WHMEndpoint(suspended_after=True))

    assert outcome.payload["ok"] is True
    assert outcome.payload["verified"] is True
    assert outcome.payload["suspended"] is True
    assert outcome.payload["headline"] == f"Account suspended — {ACCOUNT}"
    assert "did not answer" in str(outcome.payload["message"])
    # The raw code does **not** ride in the sentence, and nothing distinguishable goes with it: a
    # reading that matches after a failed write is reported as verified only where the failure was
    # a non-answer (`confirmed_verification`), and every member of that closed set means the one
    # thing "did not answer" already says in words an operator reads.
    assert ERROR_TIMEOUT not in str(outcome.payload["message"])
    assert "It was not suspended before this ran." in str(outcome.payload["message"])
    assert outcome.delta is not None
    assert outcome.delta.verification == VERIFICATION_VERIFIED
    # No hedge has anywhere to ride, and that is the shape rather than a coincidence: a verified
    # delta carrying a cause is refused at construction, so the branch above cannot quietly grow
    # one without this run raising instead of answering.
    assert outcome.delta.verification_cause is None
    assert outcome.delta.changed_fields is not None
    assert [(row.field, row.old, row.new) for row in outcome.delta.changed_fields] == [
        ("suspended", False, True)
    ]


async def test_a_write_that_never_answered_over_a_change_that_did_not_land_is_unknown() -> None:
    """The same call, the other reading, and the answer is *not* the symmetric one.

    WHM did not reply and the account still reads live. That is not evidence the suspension
    failed — it may land a second from now — so the outcome is unknown and the write's own code
    says which kind of nothing NOA is holding. What must never appear is a claim that the change
    did not happen.
    """
    outcome = await _run(_WHMEndpoint(suspended_after=False))

    assert outcome.payload["ok"] is False
    assert outcome.payload["error_code"] == ERROR_TIMEOUT
    # The account was read and reads the other way, so the heading names that reading — while the
    # sentence keeps the change itself open, which is the asymmetry this row exists for.
    assert outcome.payload["headline"] == f"Account not suspended — {ACCOUNT}"
    assert "may still land" in str(outcome.payload["message"])
    # No *comparison* was made, and the clause says only that. It does not say NOA holds no
    # reading: the account read `not suspended` on the card this was approved from, that reading
    # is on the evidence, and this is the branch that sends an operator to WHM to look for
    # themselves — so it is the branch where they most need something to compare what they find
    # against. "when NOA last read it" claims a reading and no comparison; "before this ran" one
    # test up claims the comparison. The grammar is the whole distinction.
    assert "It was not suspended when NOA last read it." in str(outcome.payload["message"])
    assert outcome.delta is not None
    assert outcome.delta.verification == VERIFICATION_UNAVAILABLE
    assert outcome.delta.verification_cause == ERROR_TIMEOUT
    # Not an empty diff: an empty one says NOA compared and the account is where it left it,
    # which is a claim about the end state that this branch is exactly the absence of.
    assert outcome.delta.changed_fields is None


async def test_a_write_the_remote_refused_now_has_a_reading_behind_it() -> None:
    """The quiet win. A refusal used to be reported with no reading at all.

    WHM answered `result:0` — it has said what it did, which is nothing — and the account reads
    live, which agrees. Two sources agreeing is a measurement, so this is `mismatch` with an
    empty diff, where before it was `unavailable` with no diff and nothing behind the code.

    WHM's own sentence survives into the message, because it is the one that names what to
    unlock.
    """
    outcome = await _run(_WHMEndpoint(suspended_after=False, mutation=whm_api_failure_body(LOCKED)))

    assert outcome.payload["ok"] is False
    assert outcome.payload["error_code"] == "whm_api_error"
    assert LOCKED in str(outcome.payload["message"])
    assert "A fresh read agrees" in str(outcome.payload["message"])
    assert outcome.payload["headline"] == f"Account not suspended — {ACCOUNT}"
    # No before-clause line: the sentence above already states the reading it took, and a second
    # line restating it would read as a second reading.
    assert "before this ran" not in str(outcome.payload["message"])
    assert "NOA has no reading" not in str(outcome.payload["message"])
    assert outcome.delta is not None
    assert outcome.delta.verification == VERIFICATION_MISMATCH
    assert outcome.delta.changed_fields == ()


async def test_a_write_the_remote_refused_over_a_state_that_matches_is_not_a_change_noa_made() -> (
    None
):
    """The one row where a qualifier is factually required, and it is required because WHM spoke.

    WHM said it did not suspend the account, and the account is suspended. Something other than
    this change put it there — an out-of-band suspension between the card being opened and the
    change running is the concrete path — so reporting a confirmed suspension would be false,
    not merely over-confident.

    `unavailable` and not `mismatch`, because the postflight *agrees* with the target; and not
    `verified`, because that claims NOA caused it. What NOA honestly holds is no measurement
    that its own change took, with the reading named beside it.
    """
    outcome = await _run(_WHMEndpoint(suspended_after=True, mutation=whm_api_failure_body(LOCKED)))

    assert outcome.payload["ok"] is False
    assert outcome.payload["error_code"] == "whm_api_error"
    assert "something other than this change" in str(outcome.payload["message"])
    assert "is suspended" in str(outcome.payload["message"])
    # The reading agrees with what was asked for, so the heading states it. What the sentence
    # withholds is the attribution, not the state.
    assert outcome.payload["headline"] == f"Account suspended — {ACCOUNT}"
    # NOA withholds the attribution, not the reading. Something other than this change put the
    # account where it is, and the gate-time reading is what an operator needs in order to work
    # out what that something did.
    assert "It was not suspended when NOA last read it." in str(outcome.payload["message"])
    assert outcome.delta is not None
    assert outcome.delta.verification == VERIFICATION_UNAVAILABLE
    assert outcome.delta.verification_cause == "whm_api_error"
    assert outcome.delta.changed_fields is None


async def test_a_confirming_read_that_fails_names_its_own_cause_and_not_the_writes() -> None:
    """Two failures, two fields, and folding them into one would lose the question each answers.

    The write did not answer and neither did the read. `verification_cause` answers "why does NOA
    hold no measurement", which is the *read's* failure; the envelope answers "what went wrong
    with this change", which is the write's. The two codes are deliberately different here so an
    implementation reporting one in both places cannot pass.
    """
    outcome = await _run(_WHMEndpoint(suspended_after=True, confirm_status=500))

    assert outcome.payload["ok"] is False
    assert outcome.payload["error_code"] == ERROR_TIMEOUT
    # No reading came back at all, so the heading names what the card is about rather than a
    # measurement — the corner beside it is what says the change is unconfirmed.
    assert outcome.payload["headline"] == f"Account suspended — {ACCOUNT}"
    assert "NOA could not read the account back afterwards" in str(outcome.payload["message"])
    # Neither call answered, and the gate-time reading survives both: it was taken before either
    # of them, so nothing that went wrong afterwards can take it away.
    assert "It was not suspended when NOA last read it." in str(outcome.payload["message"])
    assert outcome.delta is not None
    assert outcome.delta.verification == VERIFICATION_UNAVAILABLE
    assert outcome.delta.verification_cause == "http_error"
    assert outcome.delta.changed_fields is None


# --------------------------------------------------------------------------------------
# What the card says it knew before — a comparison and a reading are two claims
# --------------------------------------------------------------------------------------

# The two grammars, and the whole distinction rides on them: the first claims NOA held both sides
# and is naming the one it started from, the second claims NOA held only the gate-time side. The
# third is the sentence for evidence that carried no side at all.
COMPARED = "It was not suspended before this ran."
READ_ONLY = "It was not suspended when NOA last read it."
NO_READING = "NOA has no reading of what it was before."


async def test_a_reading_noa_could_not_confirm_is_still_a_reading() -> None:
    """Three runs off one recorded `suspended: False`, and no two of them may say the same thing.

    The first two rows of this file's table differ in exactly one thing — what the postflight
    found — and that decides whether NOA *compared*, not whether NOA *has a reading*. Both were
    approved against the same card, displaying the same `not suspended`. A runner answering
    `NO_READING` on the second contradicts that card, on the one branch where an operator has to
    go to WHM and check by hand, which is precisely the branch where they need an anchor to
    compare what they find against.

    The third run is the separating case and the reason the other two cannot be trusted alone: the
    same unconfirmed branch with the `suspended` key taken off the evidence, where `NO_READING` is
    true and is the only honest thing to say. Each sentence asserted alone passes against a runner
    that prints that one sentence always; asserted as three mutually exclusive spellings off two
    endpoints, none of them can.

    **`False` is a reading.** The middle run is what proves it: a guard written on falsiness
    rather than on `is None` sends this account's perfectly good `not suspended` down the
    no-reading path, and the defect comes back wearing the new sentence's clothes.
    """
    compared = str((await _run(_WHMEndpoint(suspended_after=True))).payload["message"])
    unconfirmed = str((await _run(_WHMEndpoint(suspended_after=False))).payload["message"])
    unrecorded = str(
        (await _run(_WHMEndpoint(suspended_after=False), account={"user": ACCOUNT})).payload[
            "message"
        ]
    )

    assert COMPARED in compared
    assert READ_ONLY not in compared and NO_READING not in compared

    assert READ_ONLY in unconfirmed
    # "before this ran" rather than the whole of `COMPARED`: what must not appear is the claim
    # that a comparison happened, in any account-state wording it could be made in.
    assert "before this ran" not in unconfirmed and NO_READING not in unconfirmed

    assert NO_READING in unrecorded
    assert READ_ONLY not in unrecorded and "before this ran" not in unrecorded


# --------------------------------------------------------------------------------------
# The confirming read's deadline is its own
# --------------------------------------------------------------------------------------


async def test_the_confirming_read_does_not_inherit_the_mutations_deadline() -> None:
    """A large budget for the change and a small one for the question about it.

    The change's 120 s exists because `unsuspendacct` was measured at 52.91 s. A read taken
    afterwards inheriting it makes the worst case four minutes on one pooled connection, for a
    question the failed write has already answered badly. Asserted at the socket, on both calls
    of one run, so a runner that dropped the per-call override would show the same number twice.
    """
    endpoint = _WHMEndpoint(suspended_after=True)

    await _run(endpoint)

    mutation = endpoint.timeouts[SUSPENDACCT_PATH]
    confirm = endpoint.timeouts[LISTACCTS_PATH]
    assert isinstance(mutation, dict) and isinstance(confirm, dict)
    assert confirm["read"] == WHM_CONFIRM_READ_TIMEOUT_SECONDS
    assert mutation["read"] > confirm["read"]
    # Only the read moves. Reaching the host is a different question from WHM thinking, and a
    # confirming read has no reason to be given less time to open a socket.
    assert confirm["connect"] == mutation["connect"]


# --------------------------------------------------------------------------------------
# The shared predicate, and the sets it rests on bound to the code that mints them
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("matched", "code", "expected"),
    [
        (True, ERROR_TIMEOUT, (VERIFICATION_VERIFIED, None)),
        (True, "whm_api_error", (VERIFICATION_UNAVAILABLE, "whm_api_error")),
        (False, "whm_api_error", (VERIFICATION_MISMATCH, None)),
        (False, ERROR_TIMEOUT, (VERIFICATION_UNAVAILABLE, ERROR_TIMEOUT)),
    ],
)
def test_the_four_corners_of_the_asymmetry(
    matched: bool, code: str, expected: tuple[str, str | None]
) -> None:
    """The table at the top of this file, as the one function that decides it.

    Driven directly as well as through a runner because the runners share it: a change here
    moves every CHANGE tool at once, and the four corners are cheaper to read in one place than
    to reconstruct from four end-to-end runs.
    """
    assert confirmed_verification(matched=matched, failure=WriteFailure(code=code)) == expected


def test_a_verified_delta_can_never_be_built_with_a_cause() -> None:
    """The pairing is returned together so the two halves cannot be assembled apart.

    `ChangeDelta` refuses a `verified` delta carrying a cause, and the first row is the branch
    that would trip it: a positive reading after a call that failed is the one place where a
    failure code is in scope and the answer is still a plain confirmation. This states the guard
    is the reason the shared function hands back both values at once.
    """
    verification, cause = confirmed_verification(
        matched=True, failure=WriteFailure(code=ERROR_TIMEOUT)
    )

    assert (verification, cause) == (VERIFICATION_VERIFIED, None)
    ChangeDelta(
        identity={"server": SERVER_NAME}, verification=verification, verification_cause=cause
    )
    with pytest.raises(ValueError, match="verified delta carries no verification cause"):
        ChangeDelta(
            identity={"server": SERVER_NAME},
            verification=VERIFICATION_VERIFIED,
            verification_cause=ERROR_TIMEOUT,
        )


@pytest.mark.parametrize("message", [None, "", "   ", "\n\t "])
def test_a_remote_that_said_nothing_usable_gets_the_fallback_sentence(message: str | None) -> None:
    """Blank is blank, whitespace included — the guard sits where all three constructions pass.

    `"  "` is truthy, so choosing the fallback on truthiness and stripping afterwards leaves a
    bare `"."`: a sentence carrying no claim, spliced in front of a reading that carries one.
    `write_failure_or_none` blanks such a message on the way in, but the firewall pair's
    `backend_write_failure` and the password runner's construction build a `WriteFailure`
    directly, so a guard at that one reader would leave two callers holding the defect.
    """
    assert WriteFailure(code=ERROR_TIMEOUT, message=message).sentence("WHM said nothing.") == (
        "WHM said nothing."
    )


def test_the_fallback_never_displaces_words_the_remote_actually_said() -> None:
    """The separating case: a guard that always took the fallback would pass the test above.

    WHM's own refusal is the sentence an operator needs — it names the remedy — so the blank
    check has to distinguish "nothing was said" from "something short was said", and punctuation
    is still added where the remote's words end without any.
    """
    assert WriteFailure(code="whm_api_error", message=LOCKED).sentence("unused") == f"{LOCKED}."
    assert WriteFailure(code="whm_api_error", message=f"  {LOCKED}  ").sentence("unused") == (
        f"{LOCKED}."
    )


def test_every_task_deadline_the_runners_own_reads_as_a_non_answer() -> None:
    """The two literals in the shared set, bound to the constants that produce them.

    The set cannot import them — those modules import it — so the direction of the check is
    reversed instead of the claim being dropped. A runner renaming its code, or minting a second
    spelling, reddens here rather than silently reclassifying a write NOA stopped waiting on as
    one the remote refused.
    """
    assert ERROR_TASK_TIMEOUT in NON_ANSWER_ERROR_CODES


@pytest.mark.parametrize("failure", [httpx.TimeoutException, httpx.ConnectError])
async def test_the_codes_a_real_client_mints_for_a_call_that_never_answered_are_non_answers(
    failure: type[httpx.HTTPError],
) -> None:
    """The set is a claim about what the clients emit, so it is checked against what they emit.

    Both transport failures go through the production `WHMClient`, which is what decides the
    code — a deadline that passed, and a connection that never carried an answer. A client that
    renamed either, or a set that lost one, turns that write into a remote refusal, which makes
    a disagreeing read conclusive and puts the original incident back one direction over.
    """
    outcome = await _run(_WHMEndpoint(suspended_after=False, mutation_error=failure))

    assert outcome.payload["error_code"] in NON_ANSWER_ERROR_CODES
    assert not WriteFailure(code=str(outcome.payload["error_code"])).refused
    # The two are distinct codes, so a set holding only one of them cannot pass both cases.
    assert outcome.delta is not None
    assert outcome.delta.verification == VERIFICATION_UNAVAILABLE


# --------------------------------------------------------------------------------------
# No runner reports a failure without reading the state back — bound, not hand-kept
# --------------------------------------------------------------------------------------

# Where a runner is allowed to answer after its write step. Two cases, and each is a production
# name whose own docstring holds the reason, so renaming either reddens this rather than quietly
# widening the exemption:
#
# - `pmg_whitelist`'s write landed and `pmgconfig sync` did not. That is already a measurement
#   and a fresh read cannot add to it — `mynetworks` reads back exactly as written, and calling
#   that verified would report a change as in force when the step that puts it in force failed.
# - `proxmox_reset_vm_password`'s write was refused or its task came back rejected, so Proxmox
#   took nothing and the generated password is live nowhere. Its confirm is ten seconds of
#   polling for an answer already known, and the branch exists to ship the delivery link with
#   the refusal rather than to measure anything.
#
# Spelled as the identifiers rather than as their values, because what is read is the runner's
# own source: renaming either in production takes it out of that source and reddens this.
POST_WRITE_RETURN_ALLOWED = ("VERIFICATION_NOT_IN_FORCE", "_failure_payload")

# The keyword each runner's final call receives its write step's outcome under. Two spellings,
# because the firewall pair's write is a result per backend rather than one failure — and the
# plural is the whole reason those two tools cannot take the scalar path.
WRITE_RESULT_KEYWORDS = ("write_failure", "changes")


def test_no_runner_returns_between_its_write_and_its_confirming_read() -> None:
    """The rule this whole file exists for, asserted against the code rather than by hand.

    The runner list comes from the registry's own map, so a CHANGE tool added later is covered
    by having a runner at all — a list typed in here would be a claim about seven tools that
    stops being true on the eighth.

    The write is located rather than guessed: the runner's final call receives its write result,
    so the statement that produced whatever that argument is built from is the write. Every
    other `return` in the body must come *before* it, which is exactly "nothing is reported
    between the write and the read". The refusals that legitimately sit earlier — an unusable
    approval, a target that could not be read, a no-op — are all before it and need no
    exemption at all.

    **What this binds and what it does not.** The set of runners is bound: it is the registry's
    own map, so the eighth CHANGE tool is covered by having a runner. The *exemptions* are two
    names read out of production source, so renaming one reddens this — but nothing here can
    judge whether an exemption is deserved. What it catches is the regression it exists for: a
    new answer appearing between a write and the read that would have said what the write did.
    """
    offenders: dict[str, list[str]] = {}
    for tool_name, runner in build_change_runners(context=build_tool_context().context).items():
        source = textwrap.dedent(inspect.getsource(runner))
        body = ast.parse(source)
        write_line, confirm_line = _write_and_confirm_lines(tool_name, body)
        late = [
            ast.get_source_segment(source, node) or ast.unparse(node)
            for node in ast.walk(body)
            if isinstance(node, ast.Return) and write_line < node.lineno < confirm_line
        ]
        stray = [
            source
            for source in late
            if not any(allowed in source for allowed in POST_WRITE_RETURN_ALLOWED)
        ]
        if stray:
            offenders[tool_name] = stray

    assert offenders == {}, (
        "a runner answered between its write and its confirming read; consult the read and pass "
        "the failure into the postflight instead"
    )


def _write_and_confirm_lines(tool_name: str, body: ast.AST) -> tuple[int, int]:
    """Where the write happened, and where the runner hands its outcome to the confirming read.

    Followed by name rather than found by position. The final call is handed the write's
    outcome under one of two keywords; whatever names that argument is built from were bound by
    the statement that performed the write, so the last of their assignments is where the write
    happened. That holds through the conditional expressions two runners wrap theirs in, which a
    rule looking only for a bare name would have refused for no reason.
    """
    final = max(
        (node for node in ast.walk(body) if isinstance(node, ast.Return)),
        key=lambda node: node.lineno,
    )
    call = final.value.value if isinstance(final.value, ast.Await) else final.value
    assert isinstance(call, ast.Call), f"{tool_name}: the runner's last answer is not a call"

    carried = {
        node.id
        for keyword in call.keywords
        if keyword.arg in WRITE_RESULT_KEYWORDS
        for node in ast.walk(keyword.value)
        if isinstance(node, ast.Name)
    }
    assert carried, (
        f"{tool_name}: hand the write's outcome to the postflight as one of "
        f"{WRITE_RESULT_KEYWORDS}, so a failed write cannot be reported without the read"
    )

    lines = [
        node.lineno
        for node in ast.walk(body)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in carried
    ]
    assert lines, f"{tool_name}: nothing in the runner body assigns any of {sorted(carried)}"
    return max(lines), final.lineno
