"""The `request_id` on a tool's log lines names the tool call, not the MCP session.

A Streamable HTTP tool call does not run in the task that served it: the session manager runs the
session in a task created during `initialize`, and a task copies contextvars at creation. So the
id `RequestContextMiddleware` binds per HTTP request is, read from inside a tool, the id of the
request that *opened the session* — one value for every call in it, forever, and never the one the
call's own response returned in `x-request-id`. An id that reads like a correlation and resolves
to a different request is worse than no id, because it is the thing an operator greps with.

`ToolRunAuditMiddleware` rebinds it for the duration of every call, which is why these tests drive
the real mount rather than a tool function: the value under test is decided by middleware, and a
unit-level request context would be asserting against a number the test planted.

**Two claims, and the second is the one the first cannot make.** That a line carries the id of the
response it belongs to is checkable one call at a time — but so is a coincidence. That *two* calls
in one session carry two *different* ids is the shape of the defect stated directly: under it,
every call in a session shared one value. `test_whm_account_non_answers.py` holds the same claim
for a CHANGE tool's own event, which is the other half of "every tool", since a CHANGE tool's call
returns before any of the audit middleware's persistence runs.

The failure driven here is the sanitizer's (`noa_api.mcp_tools.results`), reached by making the
server inventory raise. It was among the events carrying the session's id and it is the one
furthest from the seam — inside the tool, inside its decorator — so a rebind that reaches it
reaches the shorter paths too.

`merge_contextvars` is passed to `capture_logs` explicitly because the capture disables the
configured processor chain, which is where production merges the binding in. Without it these
events would carry no `request_id` key at all and the assertions would be measuring nothing.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import structlog
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from noa_api.api.request_context import REQUEST_ID_HEADER
from noa_api.mcp_audit import rebind_request_id
from noa_api.mcp_tools.results import LOG_TOOL_FAILED
from noa_api.mcp_tools.whm_read import TOOL_WHM_LIST_SERVERS
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository
from support.mcp_mount import MCP_URL, McpSession, mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.servers import build_tool_context, whm_server

SERVER_NAME = "alpha"


@pytest.fixture
def failing_read(monkeypatch: pytest.MonkeyPatch):
    """An open session over a mounted app whose one granted READ tool raises.

    The raise is what reaches `sanitize_tool_errors`, which is the log site under test. Doubled at
    the inventory rather than at the socket because the cause does not matter here — only that the
    tool fails inside its own decorator, which is the deepest of the log sites the seam has to
    cover.
    """
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    tools = build_tool_context(servers=[whm_server(SERVER_NAME)], authorization=authorization)

    async def exploding_list_servers() -> Any:
        raise RuntimeError("WHM inventory unavailable")

    monkeypatch.setattr(tools.servers, "list_servers", exploding_list_servers)

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_LIST_SERVERS)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        yield open_session(fixture.client, plaintext)


def call_tool_response(session: McpSession, name: str, call_id: int) -> httpx.Response:
    """One `tools/call` POST, unparsed.

    Posted here rather than through `McpSession.call_tool`, which hands back the parsed reply:
    the response *headers* are half of every claim in this file.
    """
    return session.client.post(
        MCP_URL,
        content=json.dumps(
            {"jsonrpc": "2.0", "id": call_id, "method": "tools/call", "params": {"name": name}}
        ),
        headers=session.headers,
    )


def logged_request_ids(logs: list[dict[str, Any]]) -> list[str]:
    """The `request_id` of each sanitizer failure line, in the order they were emitted."""
    return [event["request_id"] for event in logs if event.get("event") == LOG_TOOL_FAILED]


def test_a_failed_read_logs_the_id_its_own_response_carried(failing_read: McpSession) -> None:
    """The sanitizer's line, over the real chain, against the header the caller was handed."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        response = call_tool_response(failing_read, TOOL_WHM_LIST_SERVERS, 99)

    assert response.status_code == 200
    assert logged_request_ids(logs) == [response.headers[REQUEST_ID_HEADER]]


def test_two_calls_in_one_session_log_two_different_ids(failing_read: McpSession) -> None:
    """The defect stated directly: it gave every call in a session one shared id.

    Both halves are asserted — that each id is the one its own response carried, and that the two
    differ. Difference alone would pass on any per-call value, including a minted one that
    correlates with nothing.
    """
    with capture_logs(processors=[merge_contextvars]) as logs:
        first = call_tool_response(failing_read, TOOL_WHM_LIST_SERVERS, 99)
        second = call_tool_response(failing_read, TOOL_WHM_LIST_SERVERS, 100)

    logged = logged_request_ids(logs)
    assert logged == [first.headers[REQUEST_ID_HEADER], second.headers[REQUEST_ID_HEADER]]
    assert logged[0] != logged[1]


def test_off_request_the_seam_binds_nothing_rather_than_minting_an_id() -> None:
    """No HTTP request in scope — a direct call, a non-HTTP transport, a test driving a tool alone.

    A fresh uuid here would be the silence the rebind exists to end, wearing a better costume: it
    reads as a correlation and joins to no request anywhere. Asserted through the behaviour — the
    capture sees no `request_id` — rather than against the returned object's type, so the claim
    survives swapping `nullcontext` for anything else that binds nothing.
    """
    with capture_logs(processors=[merge_contextvars]) as logs, rebind_request_id():
        structlog.get_logger(__name__).warning("off_request_probe")

    assert logs == [{"event": "off_request_probe", "log_level": "warning"}]
