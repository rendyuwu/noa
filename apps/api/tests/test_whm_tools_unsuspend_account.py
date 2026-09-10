"""`whm_unsuspend_account` — the suspend tool's mirror, and its two extra states.

Same three lanes as `test_whm_tools_suspend_account.py`, because the tool and the runner sit on
opposite sides of the cookie/CSRF boundary and the mount is a third claim again:

- **the tool** — the preflight, the refusals, the two answers that open no request, and the gate
  response. Driven through the real `open_change_request` inside a real request context, so
  `current_mcp_identity` and `read_conversation_ref` are production functions rather than patched
  names.
- **the runner** — what happens after an operator approved. Driven with a `ChangeExecutionRequest`
  built the way `core.approvals.execution` builds one, because that is what the executor hands it.
- **the mount** — `tools/call` over `create_app()`, with every middleware in the chain.

Seams are the account search's and the suspend tool's, unchanged: the real `WHMClient` over a
doubled socket (`support.whm_api`), because WHM reports a refusal as **HTTP 200** with
`metadata.result: 0` and a doubled client would let this pass against error shapes WHM never sends;
a real `SecretCipher`, so the `Authorization` header proves a decrypt happened; the real resolver;
the real `sanitize_tool_errors`. Only the socket, the SQL and the directory are doubles.

**What is genuinely new here, and therefore what this file is for.** The rest mirrors the suspend
tool and is asserted because a mirror can be built crooked, but three claims exist only here:

1. **The no-op runs the other way.** An account that is *not* suspended has nothing to lift, so
   no request is opened. Flipping that predicate is a mutation, and the assertion is a request
   count rather than a payload shape.
2. **A locked suspension is refused before a card exists.** `unsuspendacct` will not lift one, so
   the card would buy an operator's decision and then a failed run. Asserted with its negative
   control: a suspended, *unlocked* account still opens a request, and an account whose WHM
   version never sent the field is not refused either — without those two the refusal would pass
   against a tool that refuses everything.
3. **The preflight summary carries `suspendreason` here.** An account being unsuspended is
   suspended right now, so WHM's suspension note — which as of the suspend tool is the operator's
   own reason — is on the row this tool reads. The suspend tool never met that: it reads *live*
   accounts. Both answers this tool puts in a transcript are checked: no path back to a model.

And one absence: `unsuspendacct` takes no note, so the runner writes nothing out and the
`unsuspendacct` query is asserted **exactly**, not by the absence of one key.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.approvals.delta import VERIFICATION_VERIFIED
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
    ERROR_SUSPENSION_LOCKED,
    ERROR_USERNAME_REQUIRED,
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    STATUS_CHANGED,
    STATUS_NO_OP,
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    VERIFICATION_UNAVAILABLE,
    whm_unsuspend_account,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    build_whm_account_change_runners,
    build_whm_unsuspend_runner,
)
from support.action_decisions import REASON
from support.change_delta import delta_of, payload_runner
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


async def unsuspend(
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
        answer = await whm_unsuspend_account(
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
# READ now, CHANGE through the gate: the call opens a question and changes nothing
# --------------------------------------------------------------------------------------


async def test_an_unsuspend_call_opens_a_pending_request_and_unsuspends_nothing() -> None:
    """One assertion pair: a row exists, and WHM was never asked to act.

    The second half is the one that matters and it is counted rather than inferred — the call
    *does* reach WHM, for its preflight, so "no HTTP happened" would be false and "the payload
    said pending" would pass against a tool that lifted the suspension and then said so.
    """
    fixture, api = unsuspend_context()

    answer, user_id = await unsuspend(fixture)

    request = fixture.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.tool_name == TOOL_WHM_UNSUSPEND_ACCOUNT
    assert request.requested_by_user_id == user_id
    assert fixture.action_requests.commits == [ActionRequestStatus.PENDING.value]
    assert api.requests_to(UNSUSPENDACCT_PATH) == []
    assert isinstance(answer, ToolResult)


async def test_the_preflight_runs_inside_the_call_and_lands_on_the_row() -> None:
    """One workflow, one tool — evidence stays in-process, persisted at gate time for the card.

    One `listaccts` request, not two: the preflight is `fetch_whm_accounts` through
    `collect_account_state`, shared with the list, search and suspend tools rather than
    re-implemented; a second read would mean the card describes a state the tool did not gather.
    """
    fixture, api = unsuspend_context()

    await unsuspend(fixture)

    context = fixture.action_requests.only.approval_context
    evidence = context["evidence"]
    assert evidence[EVIDENCE_SERVER_NAME] == SERVER_NAME
    assert evidence[EVIDENCE_SERVER_ID] == str(fixture.servers.servers[0].id)
    assert evidence[EVIDENCE_ACCOUNT]["user"] == ACCOUNT
    assert evidence[EVIDENCE_ACCOUNT]["suspended"] is True
    assert len(api.requests_to(LISTACCTS_PATH)) == 1


async def test_the_recorded_arguments_are_the_two_the_schema_declares() -> None:
    """One operator-typed reason field: the row records what was asked for, no reason among it.

    Asserted on the keys rather than on the absence of one name, so a future argument cannot
    arrive here unnoticed — and cross-checked against `FORBIDDEN_REASON_KEYS` so the claim is
    about the whole family of spellings the gate refuses.
    """
    fixture, _ = unsuspend_context()

    await unsuspend(fixture)

    arguments = fixture.action_requests.only.approval_context["arguments"]
    assert set(arguments) == {"server_ref", "username"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(arguments)


# --------------------------------------------------------------------------------------
# What the model is handed: one result shape, link-out text beside the frame
# --------------------------------------------------------------------------------------


async def test_the_result_carries_the_card_address_and_the_iframe() -> None:
    """Two blocks, text first, and the plain address inside the text.

    Asserted on the count and the order because the result shape claims "both, never one" — and a
    test that only looked for a resource would pass on a result with no address in it, the case
    where the frame fails to load and the operator has no door.
    """
    fixture, _ = unsuspend_context()

    answer, _ = await unsuspend(fixture)

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

    Read off the registered server rather than off the function signature: the reason field
    bounds what the model is *told* it may send, and that is what `tools/list` publishes.
    """
    context = build_tool_context().context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
    properties = set(tools[TOOL_WHM_UNSUSPEND_ACCOUNT].parameters["properties"])

    assert properties == {"server_ref", "username"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(properties)


def test_the_tool_is_catalogued_and_classified_as_a_change() -> None:
    """Admin bypass over known tools only, and risk and status kept as separate columns: a name
    outside the catalog is a capability no role can be granted, and a CHANGE that registered as a
    READ would have the audit middleware write a row for a change
    that has not happened."""
    context = build_tool_context().context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert TOOL_WHM_UNSUSPEND_ACCOUNT in TOOL_CATALOG
    assert registered[TOOL_WHM_UNSUSPEND_ACCOUNT] is ToolRisk.CHANGE


def test_both_account_change_runners_come_from_one_builder() -> None:
    """Reusable functions over duplication, and DECISIONS section 9's split held at the same time:
    two names, one module, one builder.

    The pair is deliberately *not* merged into a tool with an `action` enum — opposite risk
    directions — so what stops that from becoming two implementations is that both runners are
    built here, from one context, by one function.
    """
    context = build_tool_context().context

    runners = build_whm_account_change_runners(context=context)

    assert set(runners) == {TOOL_WHM_SUSPEND_ACCOUNT, TOOL_WHM_UNSUSPEND_ACCOUNT}


# --------------------------------------------------------------------------------------
# The two states that open no request: nothing to lift, and a lift WHM would refuse
# --------------------------------------------------------------------------------------


async def test_an_account_that_is_not_suspended_opens_no_request() -> None:
    """The suspend tool's no-op, running the other way.

    The preflight is what discovers there is nothing to do, so it answers instead of gating: a
    card for a change that would do nothing costs an operator a decision and leaves a row
    somebody has to close.
    """
    fixture, api = unsuspend_context(whm_endpoint(listings=[[live_account()]]))

    answer, _ = await unsuspend(fixture)

    assert answer["ok"] is True
    assert answer["status"] == STATUS_NO_OP
    assert answer["username"] == ACCOUNT
    assert answer["suspended"] is False
    assert fixture.action_requests.requests == []
    assert api.requests_to(UNSUSPENDACCT_PATH) == []


async def test_a_locked_suspension_is_refused_before_a_card_is_opened() -> None:
    """`unsuspendacct` will not lift a locked suspension, so NOA does not ask.

    The same argument as the no-op one state over: an approval request here buys an operator's
    decision and then a run that fails. The lock is on the summary the preflight already read,
    so discovering it costs nothing.
    """
    fixture, api = unsuspend_context(
        whm_endpoint(listings=[[suspended_account(is_locked=1)]]),
    )

    answer, _ = await unsuspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_SUSPENSION_LOCKED
    assert fixture.action_requests.requests == []
    assert api.requests_to(UNSUSPENDACCT_PATH) == []


async def test_the_older_suspendlock_spelling_is_refused_too() -> None:
    """The field is `is_locked` on current cPanel and `suspendlock` on older ones.

    Normalised in one place (`core.integrations.whm.accounts`), so this asserts that the guard
    reads the normalised summary rather than one raw key — which is what would leave a whole
    cPanel generation ungated.
    """
    fixture, _ = unsuspend_context(
        whm_endpoint(listings=[[suspended_account(suspendlock="1")]]),
    )

    answer, _ = await unsuspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_SUSPENSION_LOCKED


async def test_a_suspended_but_unlocked_account_still_opens_a_request() -> None:
    """The lock guard's negative control.

    Without it, "a locked account is refused" passes just as well against a tool that refuses
    every unsuspension — the refusal has to be about the lock, and that is only visible next to
    a case that is not refused.
    """
    fixture, _ = unsuspend_context(
        whm_endpoint(listings=[[suspended_account(is_locked=0)]]),
    )

    answer, _ = await unsuspend(fixture)

    assert isinstance(answer, ToolResult)
    assert fixture.action_requests.only.status is ActionRequestStatus.PENDING


async def test_an_account_whose_whm_never_reported_a_lock_is_not_refused() -> None:
    """The bound on the guard, asserted rather than left in a docstring.

    `listaccts` omits the field entirely on cPanel versions that do not have it. Refusing on an
    absent field would take the tool away from every one of those servers, and WHM's own refusal
    at execute time — `whm_api_error`, carrying the sentence that names the remedy — is the
    authoritative answer there.
    """
    fixture, _ = unsuspend_context(whm_endpoint(listings=[[suspended_account()]]))

    answer, _ = await unsuspend(fixture)

    assert "is_locked" not in fixture.action_requests.only.approval_context["evidence"]["account"]
    assert isinstance(answer, ToolResult)


async def test_neither_answer_that_opens_no_request_carries_whms_suspension_note() -> None:
    """No path back for a value the operator typed and the LLM never sees — and the case the suspend
    tool's file could not write.

    An account being unsuspended is suspended right now, so the summary the preflight built holds
    `suspendreason`, which since the suspend tool is the operator's own approval reason. Both
    answers below are built from the username and the server name instead of from that summary,
    which is the difference between an answer and a leak.

    The no-op path is included even though its account is *live*: the field survives an
    unsuspension in some cPanel versions, and a payload assembled from the summary would carry it
    either way. Asserting both is what makes the claim about how the answers are built.
    """
    locked, _ = unsuspend_context(whm_endpoint(listings=[[suspended_account(is_locked=1)]]))
    lifted, _ = unsuspend_context(
        whm_endpoint(listings=[[live_account(suspendreason=SUSPEND_NOTE_ECHO)]]),
    )

    locked_answer, _ = await unsuspend(locked)
    no_op_answer, _ = await unsuspend(lifted)

    assert SUSPEND_NOTE_ECHO not in json.dumps(locked_answer)
    assert SUSPEND_NOTE_ECHO not in json.dumps(no_op_answer)


# --------------------------------------------------------------------------------------
# Refusals, all of them before a row is written
# --------------------------------------------------------------------------------------


async def test_a_blank_username_is_refused_before_any_round_trip() -> None:
    """Whitespace-only strings are rejected, and the guard is placed where the schema cannot reach:
    `min_length` counts
    whitespace, so `"  "` would otherwise be fetched for and matched against nothing."""
    fixture, api = unsuspend_context()

    answer, _ = await unsuspend(fixture, username="   ")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_USERNAME_REQUIRED
    assert fixture.servers.reads == 0
    assert api.requests == []
    assert fixture.action_requests.requests == []


async def test_an_ambiguous_server_ref_returns_choices_and_opens_no_request() -> None:
    """What refusing to guess requires: a CHANGE that guessed which machine an operator meant
    is the whole hazard."""
    shared = "https://shared.example.net:2087"
    fixture, api = unsuspend_context(
        servers=[whm_server("one", base_url=shared), whm_server("two", base_url=shared)],
    )

    answer, _ = await unsuspend(fixture, server_ref="shared.example.net")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["one", "two"]
    assert fixture.action_requests.requests == []
    assert api.requests == []


async def test_an_unknown_account_is_refused_and_opens_no_request() -> None:
    """The other half of ambiguous identifier -> candidates, never guess: an operator's typo must
    not become an approval card for a
    username nobody can act on."""
    fixture, _ = unsuspend_context()

    answer, _ = await unsuspend(fixture, username="not-an-account")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_ACCOUNT_NOT_FOUND
    assert fixture.action_requests.requests == []


async def test_a_whm_refusal_keeps_the_code_that_names_the_remedy() -> None:
    """The raw-exceptions-sanitised rule's passthrough: `whm_api_error` says WHM said no, which is a
    different fix from a
    NOA failure — and the message is WHM's own `reason`."""
    fixture, _ = unsuspend_context(
        whm_endpoint(listaccts_bodies=[whm_api_failure_body("Access denied")])
    )

    answer, _ = await unsuspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == "whm_api_error"
    assert answer["message"] == "Access denied"
    assert fixture.action_requests.requests == []


async def test_a_raising_preflight_reaches_the_model_as_a_named_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw exceptions never reach the LLM — sanitised to a code: a timeout says so by name."""
    from noa_api.mcp_tools import whm_account_change

    async def raises(**_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("WHM did not answer")

    monkeypatch.setattr(whm_account_change, "fetch_whm_accounts", raises)
    fixture, _ = unsuspend_context()

    answer, _ = await unsuspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TIMEOUT
    assert "WHM did not answer" not in answer["message"]
    assert fixture.action_requests.requests == []


async def test_a_gate_write_failure_refuses_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verdict is read from `status` every time: no authorization row means no authorization, so
    the call fails rather than
    answering with a card address that leads nowhere."""
    fixture, _ = unsuspend_context()
    fixture.action_requests.fail_create = RuntimeError("connection reset")

    answer, _ = await unsuspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] != ERROR_TOOL_EXECUTION_FAILED
    assert "connection reset" not in answer["message"]


async def test_no_credential_reaches_the_result() -> None:
    """The envelope shape and an id-only URL: the result persists in LibreChat's MongoDB, so it
    carries no credential
    material — and the `Authorization` header proves the real decrypt ran."""
    fixture, api = unsuspend_context()

    answer, _ = await unsuspend(fixture)

    assert isinstance(answer, ToolResult)
    serialized = answer.model_dump_json()
    assert all(secret not in serialized for secret in SECRETS)
    assert api.authorization_headers == [f"whm root:{WHM_API_TOKEN}"]


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


async def test_the_runner_payload_never_carries_the_reason_back() -> None:
    """V96b: `result_summary` is derived from this payload, and `noa_get_action_result` returns
    the summary to a model.

    The reason is on the `ChangeExecutionRequest` — the executor reads it off the row for every
    approved change — and the evidence carries WHM's older note, so both strings are in
    front of this runner even though it writes neither. Asserted on the derived summary as well
    as on the payload, because the summary is the thing a model actually reads.
    """
    fixture, _ = unsuspend_context(whm_endpoint(listings=[[live_account()]]))
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
    assert len(api.requests_to(UNSUSPENDACCT_PATH)) == 1


async def test_a_whm_refusal_at_execute_time_keeps_its_own_code() -> None:
    """The mutation itself refused — the case a lock set after the request was opened lands in.

    `whm_api_error` and WHM's `reason` travel to the receipt, because "WHM said no" and "NOA
    broke" send an administrator to different systems, and WHM's sentence is the one that names
    what to unlock.
    """
    fixture, _ = unsuspend_context(
        whm_endpoint(unsuspend_body=whm_api_failure_body("Account suspension is locked"))
    )
    runner = payload_runner(build_whm_unsuspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == "whm_api_error"
    assert payload["message"] == "Account suspension is locked"


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
# The mount: `tools/call` over the real app
# --------------------------------------------------------------------------------------


async def test_the_mounted_call_opens_a_request_and_writes_no_tool_runs_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """READ now CHANGE through the gate, risk and status as separate columns, the tool-run trail's
    job, and the tool-run writer — over `create_app()`, with every middleware in the chain.

    The second CHANGE tool to make this claim, and it is re-made rather than inherited: the risk
    comes from `register_whm_account_change_tools`, and a tool registered CHANGE but classified
    READ would have the production middleware write a `tool_runs` row for a change that has not
    happened.
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
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_UNSUSPEND_ACCOUNT)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        session = open_session(fixture.client, plaintext)

        result = session.call_tool(
            TOOL_WHM_UNSUSPEND_ACCOUNT, {"server_ref": SERVER_NAME, "username": ACCOUNT}
        )

    assert result.get("isError") is not True
    # Both blocks survive the transport, in order.
    assert [block["type"] for block in result["content"]] == ["text", "resource"]

    request = tools.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.requested_by_user_id == user.id
    # The run-plus-receipt row belongs to the executor that runs after a decision, not to this call.
    assert tools.tool_runs.runs == []
    assert api.requests_to(UNSUSPENDACCT_PATH) == []


async def test_a_grant_for_one_direction_does_not_reach_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DECISIONS section 9's whole reason for two names rather than an `action` enum, asserted.

    Suspend and unsuspend carry opposite risk, so a role granted one must not receive the other.
    The merged pairs (`proxmox_vm_nic`, `pmg_whitelist`) accepted exactly that coarsening and
    recorded it as a cost; this pair did not, and that only stays true while the two names are
    separately grantable — which is a property of `tools/list`, so it is read off `tools/list`.
    """
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    tools = build_tool_context(authorization=authorization)

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_UNSUSPEND_ACCOUNT)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        session = open_session(fixture.client, plaintext)

        visible = set(session.tool_names())

    assert TOOL_WHM_UNSUSPEND_ACCOUNT in visible
    assert TOOL_WHM_SUSPEND_ACCOUNT not in visible


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
