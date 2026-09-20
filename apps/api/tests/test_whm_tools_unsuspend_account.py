"""`whm_unsuspend_account` — the suspend tool's mirror, and its two extra states.

Two lanes here, and a third in a sibling, because the tool and the runner sit on opposite sides of
the cookie/CSRF boundary:

- **the tool** — the preflight, the refusals, the two answers that open no request, and the gate
  response. Driven through the real `open_change_request` inside a real request context, so
  `current_mcp_identity` and `read_conversation_ref` are production functions rather than patched
  names.
- **the mount** — `tools/call` over `create_app()`, with every middleware in the chain.

**The runner lane is `test_whm_tools_unsuspend_runner.py`** — what happens after an operator
approved, and the delta published beside it. It is a separate file for one reason and the reason
is a number: the repo caps a `.py` file at 900 lines, `apps/api/tests/test_config.py` enforces
that over `git ls-files`, and this file reached the cap. Nothing was dropped to fit; the half that
needs no request context moved out whole.

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
from noa_api.mcp_tools.change_runners import build_change_runners
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.results import ERROR_TIMEOUT, ERROR_TOOL_EXECUTION_FAILED
from noa_api.mcp_tools.whm_account_change import (
    ERROR_ACCOUNT_NOT_FOUND,
    ERROR_SUSPENSION_LOCKED,
    ERROR_USERNAME_REQUIRED,
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    STATUS_NO_OP,
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    whm_unsuspend_account,
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


def test_both_account_change_runners_are_registered() -> None:
    """Reusable functions over duplication, and DECISIONS section 9's split held at the same time:
    two names, one module, two builders reached from one map.

    The pair is deliberately *not* merged into a tool with an `action` enum — opposite risk
    directions — so what stops that from becoming two implementations is that both runners live
    in one module and are wired from one context.
    """
    context = build_tool_context().context

    runners = build_change_runners(context=context)

    assert {TOOL_WHM_SUSPEND_ACCOUNT, TOOL_WHM_UNSUSPEND_ACCOUNT} <= set(runners)


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
