"""What the account CHANGE pair does when WHM does not answer the question it was asked.

Two shapes of non-answer, and until this file neither left the system honest.

**A field WHM spelled unreadably.** `normalize_whm_account_summary` writes `suspended` only when
`_optional_bool` can read WHM's value, so a row whose value is absent, JSON `null`, or a spelling
from an unseen cPanel version arrives with no `suspended` key at all. Three sites read that field
to decide something an operator acts on, and `dict.get` answers "absent" with the same `None` it
answers nothing else with — so every one of them folded an unread field into `false`. The two
directions fail *oppositely*, which is why they are asserted a few lines apart here rather than a
file apart: on a suspension the fold reports a change that landed as `postflight_failed`, and on
an unsuspension it reports a confirmed success with no reading behind it. The second is the worse
one and it is the one that reads as fine.

**A call WHM never answered.** `WHMClient` reports a timeout as a *payload*, not an exception, so
nothing on the CHANGE path logged it: the error sanitiser logs only what raised, a CHANGE tool
writes no `tool_runs` row by design, and a refusal opens no `action_requests` row either. An
operator who read "Request timed out" in chat had nothing to grep. The trace is one structured
line, and the id on it is asserted to be the *same* id the response carried — a line correlating
with nothing would be the same silence under a longer name.

Seams are the account tests' own, unchanged: the real `WHMClient` over a doubled socket, the real
cipher, the real resolver, and for the trace claim the real mounted app with every middleware in
the chain — because the id under test is bound by middleware, and a unit-level request context
would be asserting against a value the test planted.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from core.approvals.delta import VERIFICATION_VERIFIED
from core.approvals.execution import ChangeExecutionRequest
from core.integrations.whm.accounts import (
    account_suspension_state,
    normalize_whm_account_summary,
)
from noa_api.api.request_context import REQUEST_ID_HEADER
from noa_api.mcp_tools.results import ERROR_TIMEOUT
from noa_api.mcp_tools.whm_account_change import (
    ERROR_SUSPENSION_STATE_UNREADABLE,
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    LOG_PREFLIGHT_FAILED,
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    VERIFICATION_UNAVAILABLE,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    build_whm_suspend_runner,
    build_whm_unsuspend_runner,
)
from support.action_decisions import REASON
from support.change_delta import outcome_of
from support.mcp_identity import (
    LIBRECHAT_USER,
    FakeMcpIdentityRepository,
    authenticated_caller,
    http_request_context,
)
from support.mcp_mount import MCP_URL, json_rpc_payload, mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.servers import ToolFixture, build_tool_context, whm_server
from support.whm_api import (
    LISTACCTS_PATH,
    SUSPENDACCT_PATH,
    UNSUSPENDACCT_PATH,
    FakeWHMApi,
    listaccts_body,
    whm_account,
    whm_api_success_body,
)

SERVER_NAME = "alpha"
ACCOUNT = "acmeco"
OWNER = "root"
WHM_API_TOKEN = "whm-api-token-plaintext"

# A spelling `_optional_bool` does not know, so the normaliser drops the key. WHM's measured
# answers are `0`, `1` and a real `false` — all readable — which is why this is a guard against an
# unseen version rather than a reproduction of a live fault.
UNREADABLE = "maybe"


def unreadable_account() -> dict[str, Any]:
    """One `listaccts` row whose suspension state NOA cannot read."""
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=UNREADABLE, owner=OWNER)


def whm_endpoint(rows: list[dict[str, Any]]) -> FakeWHMApi:
    """A WHM endpoint answering `listaccts` with `rows`, and accepting either mutation."""
    return FakeWHMApi(
        body=listaccts_body([]),
        scripted={
            LISTACCTS_PATH: [listaccts_body(rows)],
            SUSPENDACCT_PATH: [whm_api_success_body()],
            UNSUSPENDACCT_PATH: [whm_api_success_body()],
        },
    )


def account_context(api: FakeWHMApi) -> ToolFixture:
    """A tool context whose WHM endpoint is a `MockTransport` over the production client."""
    cipher = build_tool_context().cipher
    row = whm_server(SERVER_NAME)
    row.api_token = cipher.encrypt_text(WHM_API_TOKEN)
    return build_tool_context(servers=[row], cipher=cipher, whm_transport=api.transport)


def execution_request(
    *, tool_name: str, server_id: UUID, suspended_before: bool
) -> ChangeExecutionRequest:
    """What `core.approvals.execution` hands a runner for an approved account change."""
    return ChangeExecutionRequest(
        action_request_id=uuid4(),
        tool_run_id=uuid4(),
        tool_name=tool_name,
        arguments={"server_ref": SERVER_NAME, "username": ACCOUNT},
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            EVIDENCE_OWNER: OWNER,
            EVIDENCE_ACCOUNT: {"user": ACCOUNT, "suspended": suspended_before},
        },
        reason=REASON,
    )


# --------------------------------------------------------------------------------------
# The shared reading: absence stays distinguishable from a read `false`
# --------------------------------------------------------------------------------------


def test_a_suspension_state_whm_spelled_unreadably_is_not_a_false() -> None:
    """The whole fix in one pair, at the function all three sites route through.

    `false` and "no reading at all" are two different facts and only one of them is a
    measurement. A reader that answered `False` to both would make every caller below correct in
    its own terms and wrong about the account.
    """
    live = normalize_whm_account_summary(whm_account(ACCOUNT, suspended=0))
    suspended = normalize_whm_account_summary(whm_account(ACCOUNT, suspended=1))
    unreadable = normalize_whm_account_summary(whm_account(ACCOUNT, suspended=UNREADABLE))
    assert live is not None and suspended is not None and unreadable is not None

    assert account_suspension_state(live) is False
    assert account_suspension_state(suspended) is True
    # The normaliser drops what it cannot read, so there is no key here to misread later.
    assert "suspended" not in unreadable
    assert account_suspension_state(unreadable) is None


# --------------------------------------------------------------------------------------
# The postflight: an unread field is unverifiable, in both directions
# --------------------------------------------------------------------------------------


async def test_a_suspension_whose_confirming_read_is_unreadable_is_unverified_not_failed() -> None:
    """WHM accepted the suspension and answered the confirming read with a state NOA cannot read.

    Reported as a change that happened and was not verified — the third answer. Reporting it as
    `postflight_failed` would send an operator to re-suspend an account that is already suspended,
    on the strength of a field nobody read.
    """
    api = whm_endpoint([unreadable_account()])
    fixture = account_context(api)
    request = execution_request(
        tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
        server_id=fixture.servers.servers[0].id,
        suspended_before=False,
    )

    outcome = await outcome_of(build_whm_suspend_runner(context=fixture.context), request)

    assert outcome.payload["ok"] is True
    assert outcome.payload["verified"] is False
    assert outcome.payload["verification"] == VERIFICATION_UNAVAILABLE
    assert outcome.payload["headline"] == f"Account suspended — {ACCOUNT}"
    # The two shapes of non-answer stay apart in the sentence as well as in the cause: a read that
    # answered without a state NOA can spell is a different thing to go and look at from a read
    # that never answered, and the negative half is what keeps the two from folding.
    assert "WHM did not say whether it is suspended" in str(outcome.payload["message"])
    assert "could not read the account back" not in str(outcome.payload["message"])
    assert outcome.delta is not None
    assert outcome.delta.verification == VERIFICATION_UNAVAILABLE
    assert outcome.delta.verification_cause == ERROR_SUSPENSION_STATE_UNREADABLE
    # Nothing was compared, so no diff is stated — not an empty one, which would claim a re-read
    # that found the account where the change left it.
    assert outcome.delta.changed_fields is None
    assert len(api.requests_to(SUSPENDACCT_PATH)) == 1


async def test_an_unsuspension_that_could_not_be_read_is_never_a_verified_success() -> None:
    """The direction that failed silently, and the reason this file holds one test per direction.

    `target_suspended` is `False` here, so folding the unread field into `False` made the two
    sides agree: the runner fell through to the confirmed branch and published `verified: True`
    with `STATUS_CHANGED` — a success claim built on a reading nobody has. A failure would at
    least have been visible; this was not.
    """
    api = whm_endpoint([unreadable_account()])
    fixture = account_context(api)
    request = execution_request(
        tool_name=TOOL_WHM_UNSUSPEND_ACCOUNT,
        server_id=fixture.servers.servers[0].id,
        suspended_before=True,
    )

    outcome = await outcome_of(build_whm_unsuspend_runner(context=fixture.context), request)

    assert outcome.payload["verified"] is False
    assert outcome.payload["verification"] == VERIFICATION_UNAVAILABLE
    assert outcome.payload["headline"] == f"Account unsuspended — {ACCOUNT}"
    # The sentence refuses the claim as well: it says what NOA cannot say, and the state word it
    # names is the one the change asked for rather than a reading nobody took.
    assert "so it cannot say the account is no longer suspended" in str(outcome.payload["message"])
    # The confirmed branch is the one that states the field it moved. Neither half may say so.
    assert "suspended" not in outcome.payload
    assert outcome.delta is not None
    assert outcome.delta.verification != VERIFICATION_VERIFIED
    assert outcome.delta.verification_cause == ERROR_SUSPENSION_STATE_UNREADABLE
    assert outcome.delta.changed_fields is None
    assert len(api.requests_to(UNSUSPENDACCT_PATH)) == 1


# --------------------------------------------------------------------------------------
# The preflight: an unread field opens no card and answers no benign value
# --------------------------------------------------------------------------------------


async def test_an_unreadable_state_opens_no_card_for_an_account_that_may_be_suspended() -> None:
    """The no-op guard's other edge: a retry must not buy a second card.

    The suspend tool decides "is there anything to approve" from this one field, so an unread
    value would open an approval request for an account that may already be suspended — and the
    operator would have no way to tell from the card.
    """
    api = whm_endpoint([unreadable_account()])
    fixture = account_context(api)

    user, _ = authenticated_caller()
    with http_request_context({}, user=user):
        answer = await whm_suspend_account(
            server_ref=SERVER_NAME, username=ACCOUNT, context=fixture.context
        )

    # Asserted before the envelope: the defect this guards is a card that exists, and a call that
    # opened one answers content blocks rather than a payload — so an envelope assertion alone
    # reddens with a `TypeError` that names a type instead of the request nobody should have made.
    assert fixture.action_requests.requests == []
    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_SUSPENSION_STATE_UNREADABLE
    assert api.requests_to(SUSPENDACCT_PATH) == []


async def test_an_unreadable_state_is_never_answered_as_not_suspended() -> None:
    """The same guard from the unsuspend side, where the fold produced a *statement* rather than a
    card: "`x` is not suspended; nothing to approve" about an account nobody read.

    Zero answers is `unknown`, never the benign value.
    """
    api = whm_endpoint([unreadable_account()])
    fixture = account_context(api)

    user, _ = authenticated_caller()
    with http_request_context({}, user=user):
        answer = await whm_unsuspend_account(
            server_ref=SERVER_NAME, username=ACCOUNT, context=fixture.context
        )

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_SUSPENSION_STATE_UNREADABLE
    assert "status" not in answer
    assert "not suspended" not in json.dumps(answer)
    assert fixture.action_requests.requests == []
    assert api.requests_to(UNSUSPENDACCT_PATH) == []


# --------------------------------------------------------------------------------------
# The trace: a preflight that timed out is greppable by the id the caller was given
# --------------------------------------------------------------------------------------


async def test_a_preflight_timeout_leaves_one_line_carrying_the_requests_own_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over `create_app()`, because the id under test is decided by middleware.

    Driven at the socket: the transport raises `httpx.ReadTimeout`, which the production client
    turns into the `timeout` *payload* that leaves no trace of its own. Two halves are asserted
    together on purpose — that a line exists, and that its `request_id` is byte-identical to the
    `x-request-id` **this** response carried. The second half is the whole test. A line carrying
    some other request's id looks exactly like a trace and correlates with nothing, and it is
    reachable by accident rather than by neglect: the ambient binding alone yields it.

    **Measured, and the reason the tool reads the id off the live request instead.** A Streamable
    HTTP tool call does not run in the task that served it — the session manager runs the session
    in a task created during `initialize`, and a task copies contextvars at creation — so the
    `request_id` structlog merges in here is the id of the request that opened the MCP session,
    one value for every call in it. With the explicit read removed, this assertion fails with the
    `initialize` response's id on the left.

    `merge_contextvars` is passed to the capture explicitly because `capture_logs` disables the
    configured processor chain, which is where production merges it — so the inherited value is
    present in this capture exactly as it is in production, and the explicit one has to beat it.
    """

    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("WHM did not answer", request=request)

    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    cipher = build_tool_context().cipher
    row = whm_server(SERVER_NAME)
    row.api_token = cipher.encrypt_text(WHM_API_TOKEN)
    tools = build_tool_context(
        servers=[row],
        authorization=authorization,
        cipher=cipher,
        whm_transport=httpx.MockTransport(times_out),
    )

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_SUSPEND_ACCOUNT)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        session = open_session(fixture.client, plaintext)

        # Posted here rather than through `McpSession.call_tool`, which hands back the parsed
        # reply: the response *headers* are half the claim.
        with capture_logs(processors=[merge_contextvars]) as logs:
            response = fixture.client.post(
                MCP_URL,
                content=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 99,
                        "method": "tools/call",
                        "params": {
                            "name": TOOL_WHM_SUSPEND_ACCOUNT,
                            "arguments": {"server_ref": SERVER_NAME, "username": ACCOUNT},
                        },
                    }
                ),
                headers=session.headers,
            )

    assert response.status_code == 200
    assert ERROR_TIMEOUT in json.dumps(json_rpc_payload(response))

    refusals = [event for event in logs if event.get("event") == LOG_PREFLIGHT_FAILED]
    assert len(refusals) == 1
    assert refusals[0]["tool"] == TOOL_WHM_SUSPEND_ACCOUNT
    assert refusals[0]["error_code"] == ERROR_TIMEOUT
    assert refusals[0]["server_ref"] == SERVER_NAME
    assert refusals[0]["request_id"] == response.headers[REQUEST_ID_HEADER]
    # The refusal is the whole event: no card, and nothing asked of WHM beyond the read.
    assert tools.action_requests.requests == []
