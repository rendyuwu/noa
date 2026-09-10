"""`noa_get_action_result` — the read side of the approval loop.

The tool a model calls to find out what an operator did with a change it submitted. Real
`current_mcp_identity`, real `ActionResultService`, real `sanitize_tool_errors`, real registry
and — for the mount tests — the real verifier, the real RBAC middleware and the real audit
middleware. Only the SQL is doubled; `test_action_results_live.py` runs the statement itself,
because the states that matter most to a requester-match — a deleted operator's NULL column, a
row three different writers have touched — are the database's rather than a double's.

Four properties carry the weight.

**One refusal for the whole family** (§V.27, §V.76). Absent, foreign, requester-deleted and
malformed all answer the same `error_code`, the same `message` and nothing else. Asserted as
byte-equality between the answers rather than as "both were errors" — a differing code is a
403 spelled differently, and B1's shape is a test that only looked at the status.

**The requester comes from the access token** (§V.76). Never from an argument; the tool takes
one parameter and it is the request id.

**A request that is not the caller's is never written to** (§V.27, §V.32). The expiry
check-on-read takes an id and nothing else, so the read path runs it *after* the
requester-matched read. The journal is what pins the order: a foreign id produces `["read"]`
and stops there.

**The reason and the evidence never reach the model** (C8, §V.15, §V.43, §V.17). Not filtered
— `ActionResultView` has no field for either — and asserted against real values rather than
against nothing, because a payload that never had them cannot be shown to have dropped them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import ADMIN_ROLE_NAME
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.noa_read import (
    ERROR_ACTION_REQUEST_NOT_FOUND,
    MESSAGE_ACTION_REQUEST_NOT_FOUND,
    TOOL_NOA_GET_ACTION_RESULT,
    noa_get_action_result,
)
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.results import ERROR_TOOL_EXECUTION_FAILED, MESSAGE_TOOL_EXECUTION_FAILED
from support.action_expiry import FakeActionRequestRow
from support.action_results import (
    ARGUMENTS,
    CHANGE_TOOL,
    CREATED_AT,
    EVIDENCE,
    REASON,
    result_view,
    run_view,
)
from support.mcp_identity import (
    LIBRECHAT_USER,
    FakeMcpIdentityRepository,
    authenticated_caller,
    http_request_context,
)
from support.mcp_mount import McpSession, mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.servers import ToolFixture, build_tool_context

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


async def ask(
    tools: ToolFixture,
    action_request_id: Any,
    *,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    """Call the real tool inside a real request context, as the given caller."""
    user, _ = authenticated_caller(user_id)
    with http_request_context({}, user=user):
        return await noa_get_action_result(
            action_request_id=str(action_request_id),
            context=tools.context,
        )


def seed_pending(
    tools: ToolFixture,
    *,
    owner: UUID | None,
    expires_in_seconds: float = 3600,
) -> UUID:
    """One PENDING request in both doubles — the reader's and the expiry writer's.

    Two stores rather than one because production has two classes over one table, and a test
    that seeded only the reader could not tell a read path that skips the expiry from one
    whose expiry found nothing due.
    """
    view = result_view(status=ActionRequestStatus.PENDING, expires_in_seconds=expires_in_seconds)
    tools.action_results.add(view, requester_user_id=owner)
    tools.action_expiry.add(
        FakeActionRequestRow(
            action_request_id=view.action_request_id,
            expires_at=view.expires_at,
            status=ActionRequestStatus.PENDING,
        )
    )
    return view.action_request_id


# --------------------------------------------------------------------------------------
# What the tool answers
# --------------------------------------------------------------------------------------


async def test_it_answers_with_the_requests_status_and_its_run() -> None:
    """The shape a model reads: the decision, when it was made, and how far the run got."""
    tools = build_tool_context()
    owner = uuid4()
    run = run_view(
        status=ToolRunStatus.COMPLETED,
        result_summary="Account acmeco suspended.",
        completed_at=CREATED_AT + timedelta(seconds=42),
    )
    view = result_view(
        status=ActionRequestStatus.APPROVED,
        decided_at=CREATED_AT + timedelta(seconds=30),
        run=run,
    )
    tools.action_results.add(view, requester_user_id=owner)

    result = await ask(tools, view.action_request_id, user_id=owner)

    assert result == {
        "ok": True,
        "action_request_id": str(view.action_request_id),
        "tool_name": CHANGE_TOOL,
        "status": "APPROVED",
        "arguments": ARGUMENTS,
        "created_at": CREATED_AT.isoformat(),
        "expires_at": view.expires_at.isoformat(),
        "decided_at": (CREATED_AT + timedelta(seconds=30)).isoformat(),
        "run": {
            "tool_run_id": str(run.tool_run_id),
            "status": "COMPLETED",
            "result_summary": "Account acmeco suspended.",
            "created_at": CREATED_AT.isoformat(),
            "completed_at": (CREATED_AT + timedelta(seconds=42)).isoformat(),
        },
    }


async def test_a_request_that_never_ran_says_so_rather_than_omitting_the_field() -> None:
    """`run: null` is an answer. A missing key reads to a model as one it forgot to look at."""
    tools = build_tool_context()
    owner = uuid4()
    request_id = seed_pending(tools, owner=owner)

    result = await ask(tools, request_id, user_id=owner)

    assert result["status"] == "PENDING"
    assert "run" in result
    assert result["run"] is None


async def test_the_reason_and_the_evidence_never_reach_the_model() -> None:
    """C8/V15/V43 and V17, at the one surface that could break either.

    The operator's reason is on the row this tool reads and the preflight evidence is in the
    same JSONB payload the arguments come out of — so "the LLM never sees it" is a claim
    about *this path*. What is asserted here is the emitted shape, including that a nested
    copy did not travel either (V26 — this lands in LibreChat's MongoDB). The compare that
    *separates* — evidence in the row, absent from the payload, arguments still present —
    needs a real row and lives in `test_action_results_live.py`.
    """
    tools = build_tool_context()
    owner = uuid4()
    view = result_view(status=ActionRequestStatus.DENIED, decided_at=NOW)
    tools.action_results.add(view, requester_user_id=owner)

    result = await ask(tools, view.action_request_id, user_id=owner)

    assert "reason" not in result
    assert "evidence" not in result
    assert "requester" not in result
    serialized = json.dumps(result)
    assert REASON not in serialized
    assert EVIDENCE["domain"] not in serialized


# --------------------------------------------------------------------------------------
# V27 / V76: one refusal, and the caller is the token's
# --------------------------------------------------------------------------------------


async def test_a_foreign_id_an_unknown_id_and_a_malformed_id_answer_the_same_bytes() -> None:
    """V27/V76: the refusal is not an oracle for which requests exist.

    Byte-equality, not "all three were errors": a differing `error_code` is a 403 spelled
    differently, and a caller could walk it to learn that an id is real but someone else's.
    """
    tools = build_tool_context()
    caller_id = uuid4()
    someone_elses = seed_pending(tools, owner=uuid4())

    foreign = await ask(tools, someone_elses, user_id=caller_id)
    unknown = await ask(tools, uuid4(), user_id=caller_id)
    malformed = await ask(tools, "not-a-uuid", user_id=caller_id)

    assert foreign == unknown == malformed
    assert foreign == {
        "ok": False,
        "error_code": ERROR_ACTION_REQUEST_NOT_FOUND,
        "message": MESSAGE_ACTION_REQUEST_NOT_FOUND,
    }


async def test_a_request_whose_requester_was_deleted_is_not_readable() -> None:
    """V27 fails closed: the FK is `SET NULL`, so a NULL requester matches nobody.

    That the *statement* behaves this way is `test_action_results_live.py`'s claim; this is
    the tool answering the same refusal when the reader says there is no row for the caller.
    """
    tools = build_tool_context()
    orphaned = seed_pending(tools, owner=None)

    result = await ask(tools, orphaned, user_id=uuid4())

    assert result["ok"] is False
    assert result["error_code"] == ERROR_ACTION_REQUEST_NOT_FOUND


async def test_the_requester_asked_about_is_the_tokens_caller() -> None:
    """V76: the identity comes from the access token, never from an argument.

    The tool has one parameter and it is the request id — so the assertion is on what reached
    the repository: the caller `current_mcp_identity` resolved, and nothing else.
    """
    tools = build_tool_context()
    caller_id = uuid4()
    request_id = seed_pending(tools, owner=caller_id)

    await ask(tools, request_id, user_id=caller_id)

    assert tools.action_results.lookups == [(request_id, caller_id)]


async def test_a_malformed_id_is_refused_before_any_read() -> None:
    """A guard, not a lookup: a non-UUID cannot name a row, so nothing is asked."""
    tools = build_tool_context()

    result = await ask(tools, "   ", user_id=uuid4())

    assert result["error_code"] == ERROR_ACTION_REQUEST_NOT_FOUND
    assert tools.action_results.lookups == []
    assert tools.action_results.journal == []


# --------------------------------------------------------------------------------------
# V32: no stale PENDING, and no write to a row that is not the caller's
# --------------------------------------------------------------------------------------


async def test_a_request_past_its_deadline_reads_expired() -> None:
    """V32/V23: the check-on-read makes the row terminal and reports what it wrote.

    Not a PENDING the model would tell an operator they can still approve — nobody may act on
    this request any more, and the row now says so.
    """
    tools = build_tool_context()
    owner = uuid4()
    request_id = seed_pending(tools, owner=owner, expires_in_seconds=-1)

    result = await ask(tools, request_id, user_id=owner)

    assert result["status"] == "EXPIRED"
    assert result["decided_at"] is not None
    expired_row = tools.action_expiry.rows[request_id]
    assert expired_row.status is ActionRequestStatus.EXPIRED
    assert expired_row.reason is None
    assert tools.action_results.journal == ["read", "expire", "commit"]


async def test_a_live_request_is_left_pending() -> None:
    """The other side of the same call: a deadline that has not passed changes nothing."""
    tools = build_tool_context()
    owner = uuid4()
    request_id = seed_pending(tools, owner=owner, expires_in_seconds=3600)

    result = await ask(tools, request_id, user_id=owner)

    assert result["status"] == "PENDING"
    assert result["decided_at"] is None
    assert tools.action_expiry.rows[request_id].status is ActionRequestStatus.PENDING


async def test_a_request_that_is_not_the_callers_is_never_expired_by_this_read() -> None:
    """V27/V32: a prompt-injected id must not make NOA write to a stranger's row.

    `expire_if_due` takes an id and nothing else, so an implementation that ran it before the
    requester-matched read would expire another operator's request on demand — while still
    answering this caller not-found, which is why the assertion is on the journal rather than
    on the response.
    """
    tools = build_tool_context()
    someone_elses = seed_pending(tools, owner=uuid4(), expires_in_seconds=-1)

    result = await ask(tools, someone_elses, user_id=uuid4())

    assert result["error_code"] == ERROR_ACTION_REQUEST_NOT_FOUND
    assert tools.action_results.journal == ["read"]
    assert tools.action_expiry.rows[someone_elses].status is ActionRequestStatus.PENDING


# --------------------------------------------------------------------------------------
# V19: nothing raw reaches the model
# --------------------------------------------------------------------------------------


async def test_a_read_failure_reaches_the_model_as_a_named_failure() -> None:
    """V19/V8: the database's own words never cross the boundary."""
    tools = build_tool_context()
    tools.action_results.fail = RuntimeError("connection to server at 10.0.0.9 failed: no pg_hba")

    result = await ask(tools, uuid4(), user_id=uuid4())

    assert result == {
        "ok": False,
        "error_code": ERROR_TOOL_EXECUTION_FAILED,
        "message": MESSAGE_TOOL_EXECUTION_FAILED,
    }
    assert "pg_hba" not in json.dumps(result)


# --------------------------------------------------------------------------------------
# Registration: catalogued, classified, and reached through the one gate
# --------------------------------------------------------------------------------------


def test_it_is_registered_as_a_read_tool() -> None:
    """V10/V20/V83a: in the catalog, and classified where it is defined."""
    context = build_tool_context().context

    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert TOOL_NOA_GET_ACTION_RESULT in TOOL_CATALOG
    assert registered[TOOL_NOA_GET_ACTION_RESULT] is ToolRisk.READ


async def test_the_schema_takes_one_argument_and_it_is_not_a_reason() -> None:
    """C8's boundary read from the other end: this tool cannot carry an operator's words.

    Read off the registered tool rather than restated, so a signature that grew a parameter —
    a `reason` above all — fails here instead of quietly accepting one from a client.
    """
    server = build_mcp_server(tool_context=build_tool_context().context)
    listed = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    schema = listed[TOOL_NOA_GET_ACTION_RESULT].parameters

    assert set(schema["properties"]) == {"action_request_id"}
    assert schema["required"] == ["action_request_id"]


# --------------------------------------------------------------------------------------
# Over the real mount: RBAC, audit, and the caller the transport resolved
# --------------------------------------------------------------------------------------


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The mounted app plus a helper that signs an operator in and returns their id."""
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    tools = build_tool_context(authorization=authorization)

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:

        def sign_in(
            email: str,
            *,
            roles: tuple[str, ...] = (ROLE_SUPPORT,),
            grants: tuple[str, ...] = (TOOL_NOA_GET_ACTION_RESULT,),
            librechat_user: str = LIBRECHAT_USER,
        ) -> tuple[McpSession, UUID]:
            user = authorization.add_user(email, is_active=True, roles=roles)
            for role in roles:
                if grants:
                    authorization.grant(role, *grants)
            plaintext, _ = identities.add_token(
                user_id=user.id, librechat_user_id=librechat_user, is_active=True
            )
            return open_session(fixture.client, plaintext, librechat_user=librechat_user), user.id

        yield sign_in, tools


def test_a_permitted_call_answers_and_writes_one_read_row(scenario) -> None:  # type: ignore[no-untyped-def]
    """V45/V83b: reading an approval is itself an audited READ, recorded beside the gate."""
    sign_in, tools = scenario
    session, user_id = sign_in("operator@example.com")
    request_id = seed_pending(tools, owner=user_id)

    result = session.call_tool(TOOL_NOA_GET_ACTION_RESULT, {"action_request_id": str(request_id)})

    assert result.get("isError") is not True
    assert result["structuredContent"]["status"] == "PENDING"

    run = tools.tool_runs.only
    assert run.tool_name == TOOL_NOA_GET_ACTION_RESULT
    assert run.risk is ToolRisk.READ
    assert run.requested_by_user_id == user_id
    assert run.status is ToolRunStatus.COMPLETED


def test_the_tool_is_refused_without_a_grant(scenario) -> None:  # type: ignore[no-untyped-def]
    """V1: the one RBAC gate covers this tool like every other, and it never runs."""
    sign_in, tools = scenario
    session, user_id = sign_in("operator@example.com", grants=())
    request_id = seed_pending(tools, owner=user_id)

    assert session.tool_names() == []
    assert (
        session.call_tool(TOOL_NOA_GET_ACTION_RESULT, {"action_request_id": str(request_id)})[
            "isError"
        ]
        is True
    )
    assert tools.action_results.lookups == []
    assert tools.tool_runs.runs == []


def test_another_operators_request_is_not_readable_over_the_mount(scenario) -> None:  # type: ignore[no-untyped-def]
    """V27/V76 end to end: two real tokens, two real identities, one refusal.

    The identity here is the one the verifier and the auth middleware resolved from a bearer
    token, not one the test planted — which is the difference between asserting the tool's
    access control and asserting its argument handling.
    """
    sign_in, tools = scenario
    _, owner_id = sign_in("owner@example.com")
    request_id = seed_pending(tools, owner=owner_id)

    intruder, _ = sign_in(
        "intruder@example.com",
        roles=(ADMIN_ROLE_NAME,),
        grants=(),
        librechat_user="librechat-user-2",
    )

    result = intruder.call_tool(TOOL_NOA_GET_ACTION_RESULT, {"action_request_id": str(request_id)})

    payload = result["structuredContent"]
    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_ACTION_REQUEST_NOT_FOUND
    assert payload["message"] == MESSAGE_ACTION_REQUEST_NOT_FOUND
    # An `admin` bypasses per-tool permission checks, so the refusal above is the
    # requester-match and nothing else — the strongest caller NOA has still cannot read
    # another operator's action.
    assert tools.action_expiry.rows[request_id].status is ActionRequestStatus.PENDING
