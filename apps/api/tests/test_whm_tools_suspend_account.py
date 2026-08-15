"""`whm_suspend_account` — the first CHANGE tool, and the first end-to-end gate run (T22).

Every other tool test in this suite asserts what a call *answers*. This one has to assert what a
call **does not do**: a CHANGE `tools/call` reads an account, writes a PENDING row and stops
(V16, V23). So the load-bearing assertions here are counted requests to `/json-api/suspendacct`
— zero at gate time, exactly one after an approval — rather than the shape of a payload.

Three lanes, because the tool and the runner sit on opposite sides of V22's boundary and the
mount is a third claim again:

- **the tool** — the preflight, the refusals, the no-op, and the gate response. Driven through
  the real `open_change_request` inside a real request context, so `current_mcp_identity` and
  `read_conversation_ref` are production functions rather than patched names.
- **the runner** — what happens after an operator approved. Driven with a `ChangeExecutionRequest`
  built the way `core.approvals.execution` builds one, because that is what the executor hands it.
- **the mount** — `tools/call` over `create_app()`. `test_mcp_change_gate.py` recorded that the
  gate had never run over the real mount and named this task as the fix; this is that lane, and
  it is also where "a CHANGE writes no `tool_runs` row" stops being asserted against a synthetic
  risk map (T73).

Seams are T21's, unchanged: the real `WHMClient` over a doubled socket (`support.whm_api`),
because WHM reports a refusal as **HTTP 200** with `metadata.result: 0` and a doubled client
would let this pass against error shapes WHM never sends; a real `SecretCipher`, so the
`Authorization` header proves a decrypt happened; the real resolver; the real
`sanitize_tool_errors`. Only the socket, the SQL and the directory are doubles.

**The reason is the thing to watch.** C8 says the LLM never authors, relays or sees one, and T22
is the first task where a reason leaves NOA at all: it becomes WHM's suspension note. Two
assertions bound that — the note WHM receives *is* what the operator typed, and nothing the
model can read carries it back (the no-op payload, the runner's payload, and — one file over —
`whm_search_accounts`' rows).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.approvals.execution import ChangeExecutionRequest
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
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    STATUS_CHANGED,
    STATUS_NO_OP,
    TOOL_WHM_SUSPEND_ACCOUNT,
    VERIFICATION_UNAVAILABLE,
    build_whm_suspend_runner,
    whm_suspend_account,
)
from support.action_decisions import REASON
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

# The plaintext behind the row's `api_token`, encrypted into the column so a header assertion
# proves a decrypt rather than a passthrough.
WHM_API_TOKEN = "whm-api-token-plaintext"

# What WHM echoes back in `suspendreason` once NOA has written the operator's reason there.
# Planted on the *already suspended* row, so a payload that carried the field would be carrying
# an operator's words back to the model (C8).
SUSPEND_NOTE_ECHO = "operator words WHM would echo back"


def live_account(**extra: Any) -> dict[str, Any]:
    """One `listaccts` row for a running account, as WHM sends it (`suspended` as `0`)."""
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=0, **extra)


def suspended_account(**extra: Any) -> dict[str, Any]:
    """The same account after a suspension, note included."""
    return whm_account(
        ACCOUNT,
        domain="acme.example.com",
        suspended=1,
        suspendreason=SUSPEND_NOTE_ECHO,
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
    authenticated identity rather than from an argument (V23, V27), so a call outside it would
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
            EVIDENCE_ACCOUNT: {"user": username, "suspended": False},
        },
        reason=reason,
    )


def query_of(request: Any) -> dict[str, str]:
    """One captured request's query parameters."""
    return dict(request.url.params)


# --------------------------------------------------------------------------------------
# V16, V23: the call opens a question and changes nothing
# --------------------------------------------------------------------------------------


async def test_a_suspend_call_opens_a_pending_request_and_suspends_nothing() -> None:
    """The whole of V16 in one assertion pair: a row exists, and WHM was never asked to act.

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
    """C9, V17, V33, V35: one call, evidence born in it, persisted for the card.

    One `listaccts` request, not two: the preflight is `fetch_whm_accounts`, shared with T20/T21
    rather than re-implemented, and a second read here would mean the card describes a state the
    tool did not gather.
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
    """C8, V15, V43: the row records what was asked for, and no reason is among it.

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
# V24, V25: what the model is handed
# --------------------------------------------------------------------------------------


async def test_the_result_carries_the_card_address_and_the_iframe() -> None:
    """V24, V25: two blocks, text first, and the plain address inside the text.

    Asserted on the count and the order because that is what V24 claims — "both, never one" —
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
    """C8, V15: the boundary is on the schema, so the schema is where it is asserted.

    Read off the registered server rather than off the function signature: what C8 bounds is
    what the model is *told* it may send, and that is what `tools/list` publishes.
    """
    context = build_tool_context().context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
    properties = set(tools[TOOL_WHM_SUSPEND_ACCOUNT].parameters["properties"])

    assert properties == {"server_ref", "username"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(properties)


def test_the_tool_is_catalogued_and_classified_as_a_change() -> None:
    """V10, V20: a name outside the catalog is a capability no role can be granted, and a
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
    """C8: the note is the operator's reason, and this payload is transcript (V26).

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
    """V21, and the guard is placed where the schema cannot reach: `min_length` counts
    whitespace, so `"  "` would otherwise be fetched for and matched against nothing."""
    fixture, api = suspend_context()

    answer, _ = await suspend(fixture, username="   ")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_USERNAME_REQUIRED
    assert fixture.servers.reads == 0
    assert api.requests == []
    assert fixture.action_requests.requests == []


async def test_an_ambiguous_server_ref_returns_choices_and_opens_no_request() -> None:
    """V18, C10: a CHANGE that guessed which machine an operator meant is the whole hazard."""
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
    """The other half of C10: an operator's typo must not become an approval card for a
    username nobody can act on."""
    fixture, _ = suspend_context()

    answer, _ = await suspend(fixture, username="not-an-account")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_ACCOUNT_NOT_FOUND
    assert fixture.action_requests.requests == []


async def test_a_whm_refusal_keeps_the_code_that_names_the_remedy() -> None:
    """V19's passthrough: `whm_api_error` says WHM said no, which is a different fix from a
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
    """V19: a raw exception never reaches the LLM, and a timeout says so by name."""
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
    """V23: no authorization row means no authorization, so the call fails rather than
    answering with a card address that leads nowhere."""
    fixture, _ = suspend_context()
    fixture.action_requests.fail_create = RuntimeError("connection reset")

    answer, _ = await suspend(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] != ERROR_TOOL_EXECUTION_FAILED
    assert "connection reset" not in answer["message"]


async def test_no_credential_reaches_the_result() -> None:
    """V8, V26: the result persists in LibreChat's MongoDB, so it carries no credential
    material — and the `Authorization` header proves the real decrypt ran."""
    fixture, api = suspend_context()

    answer, _ = await suspend(fixture)

    assert isinstance(answer, ToolResult)
    serialized = answer.model_dump_json()
    assert all(secret not in serialized for secret in SECRETS)
    assert api.authorization_headers == [f"whm root:{WHM_API_TOKEN}"]


# --------------------------------------------------------------------------------------
# The runner: what happens after an operator approved (V22's far side)
# --------------------------------------------------------------------------------------


async def test_the_runner_sends_the_operator_reason_as_whms_suspension_note() -> None:
    """C8's single field, written where WHM keeps a suspension note (T22).

    The reason is the operator's own words, typed on the card after the model was done. This is
    the one place it leaves NOA, and it leaves as `suspendacct`'s `reason` parameter — asserted
    on the wire, because "the runner passed it along" is a claim about the request WHM receives.
    """
    api = whm_endpoint(listings=[[suspended_account()]])
    fixture, api = suspend_context(api)
    runner = build_whm_suspend_runner(context=fixture.context)

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    mutation = api.requests_to(SUSPENDACCT_PATH)
    assert len(mutation) == 1
    assert query_of(mutation[0]) == {"api.version": "1", "user": ACCOUNT, "reason": REASON}
    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is True


async def test_the_runner_payload_never_carries_the_reason_back() -> None:
    """V76: this payload becomes `action_receipts.receipt_data`, which `noa_get_action_result`
    reads out to a model. A runner that echoed the note it just wrote would hand the LLM the one
    field C8 keeps from it — by way of the receipt rather than by way of the tool schema."""
    api = whm_endpoint(listings=[[suspended_account()]])
    fixture, _ = suspend_context(api)
    runner = build_whm_suspend_runner(context=fixture.context)

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert REASON not in json.dumps(payload)
    assert SUSPEND_NOTE_ECHO not in json.dumps(payload)


async def test_the_runner_acts_on_the_server_the_card_named() -> None:
    """V33: inventory can change between a request and its approval, and `server_ref` is a
    string the model supplied. The evidence carries the id of the machine the preflight read and
    the operator saw, so that is what the change reaches — asserted on the host WHM was called
    at, which is the only way "it ran somewhere else" would show."""
    alpha = whm_server(SERVER_NAME)
    beta = whm_server("beta")
    api = whm_endpoint(listings=[[suspended_account()]])
    fixture, api = suspend_context(api, servers=[alpha, beta])
    runner = build_whm_suspend_runner(context=fixture.context)

    # The arguments name the other server; only the evidence names alpha.
    await runner(execution_request(server_id=alpha.id, server_ref="beta"))

    hosts = {request.url.host for request in api.requests_to(SUSPENDACCT_PATH)}
    assert hosts == {f"{SERVER_NAME}.example.net"}


async def test_a_change_that_did_not_take_is_a_failure() -> None:
    """WHM accepted the call and the account is still live. Reporting that as done is the
    fabrication the postflight exists to stop."""
    api = whm_endpoint(listings=[[live_account()]])
    fixture, _ = suspend_context(api)
    runner = build_whm_suspend_runner(context=fixture.context)

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_POSTFLIGHT_FAILED


async def test_a_change_whm_accepted_but_could_not_confirm_says_unverified() -> None:
    """The third answer, and the reason `_verify_suspended` is a function rather than a boolean
    (V62's rule, one system over). A failure here would send an operator to re-suspend an account
    that may already be suspended; a plain success would claim a confirmation nobody has."""
    api = whm_endpoint(listaccts_bodies=[whm_api_failure_body("Access denied")])
    fixture, api = suspend_context(api)
    runner = build_whm_suspend_runner(context=fixture.context)

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert len(api.requests_to(SUSPENDACCT_PATH)) == 1


async def test_a_whm_refusal_at_execute_time_keeps_its_own_code() -> None:
    """The mutation itself refused. `whm_api_error` and WHM's `reason` travel to the receipt,
    because "WHM said no" and "NOA broke" send an administrator to different systems."""
    api = whm_endpoint(suspend_body=whm_api_failure_body("Account is locked"))
    fixture, _ = suspend_context(api)
    runner = build_whm_suspend_runner(context=fixture.context)

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == "whm_api_error"
    assert payload["message"] == "Account is locked"


async def test_a_server_that_vanished_after_approval_is_refused_before_the_mutation() -> None:
    """Fail closed on the far side of the boundary too: the row the operator approved against
    is gone, so the change does not run against whatever `server_ref` resolves to today."""
    fixture, api = suspend_context()
    runner = build_whm_suspend_runner(context=fixture.context)

    payload = await runner(execution_request(server_id=uuid4()))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert api.requests_to(SUSPENDACCT_PATH) == []


async def test_evidence_without_a_usable_server_id_is_refused() -> None:
    """The evidence round-tripped through JSONB. A value that no longer parses as a UUID is a
    request NOA refuses rather than guesses at."""
    fixture, api = suspend_context()
    runner = build_whm_suspend_runner(context=fixture.context)

    payload = await runner(execution_request(server_id="not-a-uuid"))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert api.requests == []


# --------------------------------------------------------------------------------------
# The mount: the gate's first run over `tools/call` (T33's owed lane)
# --------------------------------------------------------------------------------------


async def test_the_mounted_call_opens_a_request_and_writes_no_tool_runs_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V16, V20, V45, T73 — over `create_app()`, with every middleware in the chain.

    Until T22 there was no CHANGE tool to drive this with, so `test_mcp_tool_audit.py` asserted
    the middleware's CHANGE branch against a synthetic risk map: a mapping the test supplied,
    around a tool that did not exist. This is the same claim made by a real registration — the
    risk comes from `register_whm_account_change_tools`, and the row that is *not* written is not
    written by the production middleware.
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
    # Both blocks survive the transport, in order (V24, V25).
    assert [block["type"] for block in result["content"]] == ["text", "resource"]

    request = tools.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.requested_by_user_id == user.id
    # V46's row belongs to the executor that runs after a decision, not to this call.
    assert tools.tool_runs.runs == []
    assert api.requests_to(SUSPENDACCT_PATH) == []


def whm_endpoint_listing_suspended() -> FakeWHMApi:
    """An endpoint whose account is already suspended, note included."""
    return whm_endpoint(listings=[[suspended_account()]])
