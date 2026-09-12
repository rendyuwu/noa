"""`whm_suspend_account` — the first CHANGE tool, and the first end-to-end gate run.

Every other tool test in this suite asserts what a call *answers*. This one has to assert what a
call **does not do**: a CHANGE `tools/call` reads an account, writes a PENDING row and stops —
CHANGE goes through the gate, verdict read from `status` alone. So the load-bearing assertions here
are counted requests to `/json-api/suspendacct` — zero at gate time, exactly one after an approval —
rather than the shape of a payload.

Three lanes, because the tool and the runner sit on opposite sides of the cookie/CSRF boundary and
the mount is a third claim again:

- **the tool** — the preflight, the refusals, the no-op, and the gate response. Driven through
  the real `open_change_request` inside a real request context, so `current_mcp_identity` and
  `read_conversation_ref` are production functions rather than patched names.
- **the runner** — what happens after an operator approved. Driven with a `ChangeExecutionRequest`
  built the way `core.approvals.execution` builds one, because that is what the executor hands it.
- **the mount** — `tools/call` over `create_app()`. `test_mcp_change_gate.py` recorded that the
  gate had never run over the real mount and named this task as the fix; this is that lane, and
  it is also where "a CHANGE writes no `tool_runs` row" stops being asserted against a synthetic
  risk map.

Seams match the account-search tests, unchanged: the real `WHMClient` over a doubled socket
(`support.whm_api`), because WHM reports a refusal as **HTTP 200** with `metadata.result: 0` and a
doubled client would let this pass against error shapes WHM never sends; a real `SecretCipher`, so
the `Authorization` header proves a decrypt happened; the real resolver; the real
`sanitize_tool_errors`. Only the socket, the SQL and the directory are doubles.

**The reason is the thing to watch.** The reason rule: the LLM never authors, relays or sees one,
and this tool is the first place a reason leaves NOA at all: it becomes WHM's suspension note. Two
assertions bound that — the note WHM receives *is* what the operator typed, and nothing the model
can read carries it back (the no-op payload, the runner's payload, and — one file over —
`whm_search_accounts`' rows).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.approvals.delta import (
    VERIFICATION_MISMATCH,
    VERIFICATION_VERIFIED,
)
from core.approvals.execution import ChangeExecutionRequest
from core.audit.summaries import result_summary
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ActionRequestStatus, ToolRisk
from core.db.models import WHMServer
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.change_gate import (
    APPROVAL_CARD_PATH,
    FORBIDDEN_REASON_KEYS,
    UI_RESOURCE_MIME_TYPE,
    UI_RESOURCE_URI_PREFIX,
)
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.results import ERROR_TIMEOUT, ERROR_TOOL_EXECUTION_FAILED
from noa_api.mcp_tools.whm_account_change import (
    ERROR_ACCOUNT_NOT_FOUND,
    ERROR_POSTFLIGHT_FAILED,
    ERROR_SERVER_UNAVAILABLE,
    ERROR_USERNAME_REQUIRED,
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    STATUS_CHANGED,
    STATUS_NO_OP,
    TOOL_WHM_SUSPEND_ACCOUNT,
    VERIFICATION_UNAVAILABLE,
    whm_suspend_account,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    build_whm_suspend_runner,
)
from support.action_decisions import REASON
from support.change_delta import delta_of, outcome_of, payload_runner
from support.mcp_identity import (
    LIBRECHAT_USER,
    FakeMcpIdentityRepository,
    authenticated_caller,
    http_request_context,
)
from support.mcp_mount import mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.servers import (
    EMBED_BASE_URL,
    SECRETS,
    ToolFixture,
    build_tool_context,
    whm_server,
)
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


async def suspend(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    username: str = ACCOUNT,
    user_id: UUID | None = None,
) -> tuple[Any, UUID]:
    """Call the tool inside a real request context; return its answer and the caller's id.

    The context is not decoration: `open_change_request` reads the requester from the
    authenticated identity rather than from an argument, so a call outside it would
    be asserting against an identity the test planted.
    """
    user, resolved = authenticated_caller(user_id)
    with http_request_context({}, user=user):
        answer = await whm_suspend_account(
            server_ref=server_ref, username=username, context=fixture.context
        )
    return answer, resolved


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
# CHANGE through the gate: the call opens a question and changes nothing
# --------------------------------------------------------------------------------------


async def test_a_suspend_call_opens_a_pending_request_and_suspends_nothing() -> None:
    """The whole of "CHANGE through the gate" in one assertion pair: a row exists, and WHM was never
    asked to act.

    The second half is the one that matters and it is counted rather than inferred — the call
    *does* reach WHM, for its preflight, so "no HTTP happened" would be false and "the payload
    said pending" would pass against a tool that suspended the account and then said so.
    """
    fixture, api = suspend_context()

    answer, user_id = await suspend(fixture)

    request = fixture.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.tool_name == TOOL_WHM_SUSPEND_ACCOUNT
    assert request.requested_by_user_id == user_id
    assert fixture.action_requests.commits == [ActionRequestStatus.PENDING.value]
    assert api.requests_to(SUSPENDACCT_PATH) == []
    assert isinstance(answer, ToolResult)


async def test_the_preflight_runs_inside_the_call_and_lands_on_the_row() -> None:
    """One call, evidence born in it, persisted for the card.

    One `listaccts` request, not two: the preflight is `fetch_whm_accounts`, shared with the list
    and search tools rather than re-implemented, and a second read here would mean the card
    describes a state the tool did not gather.
    """
    fixture, api = suspend_context()

    await suspend(fixture)

    context = fixture.action_requests.only.approval_context
    evidence = context["evidence"]
    assert evidence[EVIDENCE_SERVER_NAME] == SERVER_NAME
    assert evidence[EVIDENCE_SERVER_ID] == str(fixture.servers.servers[0].id)
    assert evidence[EVIDENCE_ACCOUNT]["user"] == ACCOUNT
    assert evidence[EVIDENCE_ACCOUNT]["suspended"] is False
    assert len(api.requests_to(LISTACCTS_PATH)) == 1


async def test_the_recorded_arguments_are_the_two_the_schema_declares() -> None:
    """The row records what was asked for, and no reason is among it.

    Asserted on the keys rather than on the absence of one name, so a future argument cannot
    arrive here unnoticed — and cross-checked against `FORBIDDEN_REASON_KEYS` so the claim is
    about the whole family of spellings the gate refuses.
    """
    fixture, _ = suspend_context()

    await suspend(fixture)

    arguments = fixture.action_requests.only.approval_context["arguments"]
    assert set(arguments) == {"server_ref", "username"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(arguments)


# --------------------------------------------------------------------------------------
# link-out text beside the frame: what the model is handed
# --------------------------------------------------------------------------------------


async def test_the_result_carries_the_card_address_and_the_iframe() -> None:
    """Two blocks, text first, and the plain address inside the text.

    Asserted on the count and the order because that is what "both, never one" claims —
    and a test that only looked for a resource would pass on a result with no address in it,
    which is the case where the frame fails to load and the operator has no door.
    """
    fixture, _ = suspend_context()

    answer, _ = await suspend(fixture)

    assert isinstance(answer, ToolResult)
    text, resource = answer.content
    assert isinstance(text, TextContent)
    assert isinstance(resource, EmbeddedResource)

    action_request_id = fixture.action_requests.only.action_request_id
    url = f"{EMBED_BASE_URL}{APPROVAL_CARD_PATH}/{action_request_id}"
    assert url in text.text
    assert str(resource.resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{action_request_id}"
    assert resource.resource.mimeType == UI_RESOURCE_MIME_TYPE


async def test_the_tool_schema_carries_no_reason_parameter() -> None:
    """The boundary is on the schema, so the schema is where it is asserted.

    Read off the registered server rather than off the function signature: what the reason rule
    bounds is what the model is *told* it may send, and that is what `tools/list` publishes.
    """
    context = build_tool_context().context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
    properties = set(tools[TOOL_WHM_SUSPEND_ACCOUNT].parameters["properties"])

    assert properties == {"server_ref", "username"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(properties)


def test_the_tool_is_catalogued_and_classified_as_a_change() -> None:
    """A name outside the catalog is a capability no role can be granted, and a
    CHANGE that registered as a READ would have the audit middleware write a row for a change
    that has not happened."""
    context = build_tool_context().context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert TOOL_WHM_SUSPEND_ACCOUNT in TOOL_CATALOG
    assert registered[TOOL_WHM_SUSPEND_ACCOUNT] is ToolRisk.CHANGE


# --------------------------------------------------------------------------------------
# The no-op: nothing to approve, so nothing is asked
# --------------------------------------------------------------------------------------


async def test_an_already_suspended_account_opens_no_request() -> None:
    """The preflight is what discovers there is nothing to do, so it answers instead of gating.

    A card for a change that would do nothing costs an operator a decision and leaves a row
    somebody has to close.
    """
    fixture, api = suspend_context(whm_endpoint_listing_suspended())

    answer, _ = await suspend(fixture)

    assert answer["ok"] is True
    assert answer["status"] == STATUS_NO_OP
    assert answer["username"] == ACCOUNT
    assert fixture.action_requests.requests == []
    assert api.requests_to(SUSPENDACCT_PATH) == []


async def test_the_no_op_answer_does_not_carry_whms_suspension_note() -> None:
    """The note is the operator's reason, and this payload is transcript.

    The account summary the preflight built holds `suspendreason`; this answer is built from the
    username and the server instead of from that summary, which is the difference between an
    answer and a leak.
    """
    fixture, _ = suspend_context(whm_endpoint_listing_suspended())

    answer, _ = await suspend(fixture)

    assert SUSPEND_NOTE_ECHO not in json.dumps(answer)


# --------------------------------------------------------------------------------------
# Refusals, all of them before a row is written
# --------------------------------------------------------------------------------------


async def test_a_blank_username_is_refused_before_any_round_trip() -> None:
    """Whitespace-only input is refused, and the guard is placed where the schema cannot reach:
    `min_length` counts
    whitespace, so `"  "` would otherwise be fetched for and matched against nothing."""
    fixture, api = suspend_context()

    answer, _ = await suspend(fixture, username="   ")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_USERNAME_REQUIRED
    assert fixture.servers.reads == 0
    assert api.requests == []
    assert fixture.action_requests.requests == []


async def test_an_ambiguous_server_ref_returns_choices_and_opens_no_request() -> None:
    """Ambiguous identifier: candidates, never a guess.

    A CHANGE that guessed which machine an operator meant is the whole hazard.
    """
    shared = "https://shared.example.net:2087"
    fixture, api = suspend_context(
        servers=[whm_server("one", base_url=shared), whm_server("two", base_url=shared)],
    )

    answer, _ = await suspend(fixture, server_ref="shared.example.net")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["one", "two"]
    assert fixture.action_requests.requests == []
    assert api.requests == []


async def test_an_unknown_account_is_refused_and_opens_no_request() -> None:
    """The other half of never guessing: an operator's typo must not become an approval card for a
    username nobody can act on."""
    fixture, _ = suspend_context()

    answer, _ = await suspend(fixture, username="not-an-account")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_ACCOUNT_NOT_FOUND
    assert fixture.action_requests.requests == []


async def test_a_whm_refusal_keeps_the_code_that_names_the_remedy() -> None:
    """The passthrough: `whm_api_error` says WHM said no, which is a different fix from a
    NOA failure — and the message is WHM's own `reason`."""
    fixture, _ = suspend_context(
        whm_endpoint(listaccts_bodies=[whm_api_failure_body("Access denied")])
    )

    answer, _ = await suspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == "whm_api_error"
    assert answer["message"] == "Access denied"
    assert fixture.action_requests.requests == []


async def test_a_raising_preflight_reaches_the_model_as_a_named_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw exception never reaches the LLM, and a timeout says so by name."""
    from noa_api.mcp_tools import whm_account_change

    async def raises(**_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("WHM did not answer")

    monkeypatch.setattr(whm_account_change, "fetch_whm_accounts", raises)
    fixture, _ = suspend_context()

    answer, _ = await suspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TIMEOUT
    assert "WHM did not answer" not in answer["message"]
    assert fixture.action_requests.requests == []


async def test_a_gate_write_failure_refuses_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """No authorization row means no authorization, so the call fails rather than
    answering with a card address that leads nowhere."""
    fixture, _ = suspend_context()
    fixture.action_requests.fail_create = RuntimeError("connection reset")

    answer, _ = await suspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] != ERROR_TOOL_EXECUTION_FAILED
    assert "connection reset" not in answer["message"]


async def test_no_credential_reaches_the_result() -> None:
    """The result persists in LibreChat's MongoDB, so it carries no credential
    material — and the `Authorization` header proves the real decrypt ran."""
    fixture, api = suspend_context()

    answer, _ = await suspend(fixture)

    assert isinstance(answer, ToolResult)
    serialized = answer.model_dump_json()
    assert all(secret not in serialized for secret in SECRETS)
    assert api.authorization_headers == [f"whm root:{WHM_API_TOKEN}"]


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
    # Nothing was compared on this branch by construction — there is no reading to compare
    # against — so the before-clause is the no-reading spelling and never the measured-empty one,
    # which would claim a comparison that could not have happened.
    assert "NOA has no reading of what it was before." in str(payload["message"])
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
# The mount: the gate's first run over `tools/call` (the request-opening gate's owed lane)
# --------------------------------------------------------------------------------------


async def test_the_mounted_call_opens_a_request_and_writes_no_tool_runs_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over `create_app()`, with every middleware in the chain.

    Until this tool existed there was no CHANGE tool to drive this with, so `test_mcp_tool_audit.py`
    asserted the middleware's CHANGE branch against a synthetic risk map: a mapping the test
    supplied, around a tool that did not exist. This is the same claim made by a real registration —
    the risk comes from `register_whm_account_change_tools`, and the row that is *not* written is
    not written by the production middleware.
    """
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    api = whm_endpoint()
    cipher = build_tool_context().cipher
    row = whm_server(SERVER_NAME)
    row.api_token = cipher.encrypt_text(WHM_API_TOKEN)
    tools = build_tool_context(
        servers=[row],
        authorization=authorization,
        cipher=cipher,
        whm_transport=api.transport,
    )

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_SUSPEND_ACCOUNT)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        session = open_session(fixture.client, plaintext)

        result = session.call_tool(
            TOOL_WHM_SUSPEND_ACCOUNT, {"server_ref": SERVER_NAME, "username": ACCOUNT}
        )

    assert result.get("isError") is not True
    # Both blocks survive the transport, in order.
    assert [block["type"] for block in result["content"]] == ["text", "resource"]

    request = tools.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.requested_by_user_id == user.id
    # The run-plus-receipt write belongs to the executor that runs after a decision, not to this
    # call.
    assert tools.tool_runs.runs == []
    assert api.requests_to(SUSPENDACCT_PATH) == []


def whm_endpoint_listing_suspended() -> FakeWHMApi:
    """An endpoint whose account is already suspended, note included."""
    return whm_endpoint(listings=[[suspended_account()]])


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
    assert "NOA has no reading of what it was before." in str(outcome.payload["message"])


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
