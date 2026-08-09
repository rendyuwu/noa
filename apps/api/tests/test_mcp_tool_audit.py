"""`tool_runs` written for every READ, from the gate (T73 — V20, V45, V47, V83b).

T19(g) recorded the hole this closes: every tool call was unaudited because the table did
not exist yet. T35 built the table and wrote nothing. This is the test that the write
happens, that it happens in one place rather than in each tool, and that a failed READ is
recorded as one.

Three levels, for three different claims:

- **Over the real mount** — the middleware chain, the RBAC gate, the verifier, the real
  tool. This is where V45 and V83b are provable, because the thing being asserted is that
  a row appears without the tool knowing anything about it.
- **Against the middleware's helpers** — summarisation, status mapping, header handling.
  Cheap, and they say which piece broke.
- **Live** — `SQLToolRunRepository` against a scratch Postgres. "The middleware called a
  repository" and "a row exists in `tool_runs`" are different claims, and only the second
  one is V45.

The identity doubles are joined by `user_id` exactly as in `test_mcp_tool_rbac.py`:
`FakeMcpIdentityRepository.add_token` mints the credential and
`FakeAuthorizationRepository.add_user` holds the roles, both under the same id, which is the
join production makes through `users.id`. The audit row's `requested_by_user_id` is asserted
against that id, so a middleware that recorded the wrong caller fails here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import types as mt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.audit.tool_runs import SQLToolRunRepository
from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.db.models import ToolRun, User
from core.secrets.redaction import REDACTED
from noa_api.mcp_audit import (
    CONVERSATION_REF_HEADER,
    ERROR_AUDIT_UNAVAILABLE,
    MAX_CONVERSATION_REF_LENGTH,
    MAX_RESULT_SUMMARY_LENGTH,
    ToolRunAuditMiddleware,
    redacted_args,
    result_summary,
    status_for_payload,
)
from noa_api.mcp_tools.whm_read import (
    TOOL_WHM_LIST_SERVERS,
    TOOL_WHM_SEARCH_ACCOUNTS,
    whm_list_servers,
)
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository
from support.mcp_mount import McpSession, mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.servers import SECRETS, build_tool_context, whm_server
from support.tool_runs import FakeToolRunRepository
from support.whm_api import whm_account, whm_api_listing

SCRATCH_DB = "noa_tool_run_writer_test"

# The header T57 will fill from `{{LIBRECHAT_BODY_CONVERSATIONID}}`. A UUID, because that is
# what LibreChat's `conversationId` is.
CONVERSATION_ID = "1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12"

# Catalogued, unbuilt, and a CHANGE when it lands (T22). Used to drive the middleware's
# CHANGE branch, which has no registered tool behind it yet.
CHANGE_TOOL = "whm_suspend_account"

# Catalogued but not registered (T20) — what a stale client catalog or a prompt-injected
# call names. Refused by the RBAC gate outside this middleware.
UNREGISTERED_TOOL = "whm_list_accounts"


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch):
    """A mounted app plus a sign-in helper, sharing one `tool_runs` double."""
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    runs = FakeToolRunRepository()
    tools = build_tool_context(
        servers=[whm_server("alpha")],
        authorization=authorization,
        tool_runs=runs,
    )

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:

        def sign_in(*, conversation_ref: str | None = None) -> tuple[McpSession, UUID]:
            user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
            authorization.grant(ROLE_SUPPORT, TOOL_WHM_LIST_SERVERS)
            plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
            session = open_session(fixture.client, plaintext)
            if conversation_ref is not None:
                session.headers[CONVERSATION_REF_HEADER] = conversation_ref
            return session, user.id

        yield sign_in, runs, tools


# --------------------------------------------------------------------------------------
# V45: every READ writes a row
# --------------------------------------------------------------------------------------


def test_a_successful_read_writes_one_completed_row(scenario) -> None:
    """V45, the baseline. One call, one row, terminal and attributed.

    Everything below is a variation on this, so it is asserted first: a middleware that
    wrote nothing would pass most of the negative tests here.
    """
    sign_in, runs, _ = scenario
    session, user_id = sign_in()

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["structuredContent"]["ok"] is True
    run = runs.only
    assert run.tool_name == TOOL_WHM_LIST_SERVERS
    assert run.requested_by_user_id == user_id
    assert run.status is ToolRunStatus.COMPLETED


def test_each_call_writes_its_own_row(scenario) -> None:
    """Nothing is memoized or coalesced: the audit trail counts calls, not distinct tools."""
    sign_in, runs, _ = scenario
    session, _ = sign_in()

    session.call_tool(TOOL_WHM_LIST_SERVERS)
    session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert len(runs.runs) == 2


def test_the_written_row_carries_every_v47_field(scenario) -> None:
    """V47's field list, one assertion per field rather than a shape comparison.

    A shape comparison would go green on a row where `conversation_ref` and
    `result_summary` were both silently `None`.
    """
    sign_in, runs, _ = scenario
    session, user_id = sign_in(conversation_ref=CONVERSATION_ID)

    session.call_tool(TOOL_WHM_LIST_SERVERS)
    run = runs.only

    assert run.requested_by_user_id == user_id
    assert run.tool_name == TOOL_WHM_LIST_SERVERS
    assert run.status is ToolRunStatus.COMPLETED
    assert run.conversation_ref == CONVERSATION_ID
    assert run.result_summary is not None
    assert run.args == {}


def test_timing_is_a_started_row_committed_before_the_tool_runs(scenario) -> None:
    """V47 timing, and the reason `STARTED` exists at all (T35, T38).

    Two commits in order — `STARTED`, then `COMPLETED`. One commit would mean the row only
    appears once the call ends, so a process that died mid-call would leave no evidence,
    and T38's reaper would have nothing to sweep.
    """
    sign_in, runs, _ = scenario
    session, _ = sign_in()

    session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert runs.commits == [ToolRunStatus.STARTED.value, ToolRunStatus.COMPLETED.value]
    assert runs.only.committed == 2


# --------------------------------------------------------------------------------------
# V45 fail-closed: no row, no run
# --------------------------------------------------------------------------------------


def test_a_read_is_refused_when_its_audit_row_cannot_be_written(scenario) -> None:
    """V45 held by mechanism rather than by prose.

    Running anyway would leave "every READ writes a row" asserted in the spec and enforced
    by nothing — the shape B2 shipped (V69). The refusal reuses the tool envelope so a model
    sees one shape whichever gate closed.
    """
    sign_in, runs, tools = scenario
    session, _ = sign_in()
    runs.fail_start = RuntimeError("tool_runs INSERT rejected")

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == ERROR_AUDIT_UNAVAILABLE
    # The refusal is a refusal to run, not a discarded result: the tool never read inventory.
    assert tools.servers.reads == 0


def test_a_terminal_write_failure_leaves_the_row_started_and_the_call_intact(scenario) -> None:
    """The other direction, and it is deliberately not symmetric.

    The tool has already run by the time the closing write happens, so refusing the caller
    would misreport a call that took place. The row stays `STARTED`, which is a state the
    schema defines and T38's reaper resolves.
    """
    sign_in, runs, _ = scenario
    session, _ = sign_in()
    runs.fail_finish = RuntimeError("tool_runs UPDATE rejected")

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["structuredContent"]["ok"] is True
    assert runs.only.status is ToolRunStatus.STARTED


# --------------------------------------------------------------------------------------
# V20: risk and status are separate, and a failed READ is representable
# --------------------------------------------------------------------------------------


def test_risk_is_read_while_status_carries_the_lifecycle(scenario) -> None:
    """V20: two columns, two enums. `risk` never advances."""
    sign_in, runs, _ = scenario
    session, _ = sign_in()

    session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert runs.only.risk is ToolRisk.READ
    assert runs.only.status is ToolRunStatus.COMPLETED


def test_an_ok_false_result_is_recorded_as_a_failed_read(scenario, monkeypatch) -> None:
    """V20's whole point, over the real mount.

    `sanitize_tool_errors` (V19) turns an exception into a *returned* `{"ok": False}`
    payload, so a failure arrives as an ordinary result. Without the `ok` branch every
    failed READ would be filed as a success — the audit trail would say NOA did something
    it did not do.
    """
    sign_in, runs, tools = scenario
    session, _ = sign_in()

    async def exploding_list_servers() -> Any:
        raise RuntimeError("WHM inventory unavailable")

    monkeypatch.setattr(tools.servers, "list_servers", exploding_list_servers)
    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["structuredContent"]["ok"] is False
    assert runs.only.risk is ToolRisk.READ
    assert runs.only.status is ToolRunStatus.FAILED


def test_the_failure_summary_names_the_sanitized_code_not_the_cause(scenario, monkeypatch) -> None:
    """T35 left out an `error` column because a sanitized code fits `result_summary`.

    The raw exception text must not land there: the audit row is read by the admin surface
    and may name hosts and paths (V8). What is stored is what the model was told.
    """
    sign_in, runs, tools = scenario
    session, _ = sign_in()

    async def exploding_list_servers() -> Any:
        raise RuntimeError("connection to whm-alpha.internal:2087 refused")

    monkeypatch.setattr(tools.servers, "list_servers", exploding_list_servers)
    session.call_tool(TOOL_WHM_LIST_SERVERS)

    summary = runs.only.result_summary or ""
    assert "tool_execution_failed" in summary
    assert "whm-alpha.internal" not in summary


# --------------------------------------------------------------------------------------
# V83b: the write is the gate's, not the tool's
# --------------------------------------------------------------------------------------


async def test_calling_the_tool_function_directly_writes_nothing() -> None:
    """V83b, first half: no tool contains audit code.

    Paired with `test_a_successful_read_writes_one_completed_row`, which drives the same
    tool over the mount and gets a row. Together they locate the write in the middleware —
    which is what makes T20-T31 and T63 audited by existing, rather than by each remembering
    to call something.
    """
    tools = build_tool_context(servers=[whm_server("alpha")])

    payload = await whm_list_servers(context=tools.context)

    assert payload["ok"] is True
    assert tools.tool_runs.runs == []


def test_a_call_refused_by_rbac_writes_no_row(scenario) -> None:
    """The audit middleware sits *inside* the RBAC gate, and the order is load-bearing.

    A registered tool with the grant revoked is the case that pins it: swap the two
    `add_middleware` calls and the audit middleware sees a tool it *does* classify as READ,
    writes a `STARTED` row, and the denial lands in the audit trail as an execution — a
    `tool_runs` entry for a call NOA refused. An unregistered name (below) cannot catch that,
    because it is absent from the risk map either way.
    """
    sign_in, runs, tools = scenario
    session, _ = sign_in()
    tools.authorization.role_tools[ROLE_SUPPORT] = set()

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["isError"] is True
    assert runs.runs == []


def test_an_unregistered_name_writes_no_row(scenario) -> None:
    """V10's refusal is not an execution either, and the risk map is the second guard.

    `whm_list_accounts` is catalogued but unbuilt (T20), so an `admin` bypass reaches the
    gate with it. Neither the gate nor the risk map knows it, and it must not become an
    audit row for a tool that does not exist yet.
    """
    sign_in, runs, _ = scenario
    session, _ = sign_in()

    session.call_tool(UNREGISTERED_TOOL)

    assert runs.runs == []


def test_the_second_read_tool_is_audited_with_its_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V45, V83b: the row comes from the gate, so a *new* tool inherits it (T21).

    Every other test here drives `whm_list_servers`, the tool that shipped beside the
    middleware — which cannot distinguish "audits every READ" from "audits that one". This
    calls `whm_search_accounts` instead, over the same mount, and asserts the row and the
    recorded arguments. Its own fixture because it needs a decryptable API token and a WHM
    endpoint, neither of which the shared `scenario` has.
    """
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    runs = FakeToolRunRepository()
    cipher = build_tool_context().cipher
    endpoint = whm_api_listing([whm_account("acme")])
    tools = build_tool_context(
        servers=[whm_server("alpha", api_token=cipher.encrypt_text("whm-token"))],
        authorization=authorization,
        tool_runs=runs,
        cipher=cipher,
        whm_transport=endpoint.transport,
    )

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_SEARCH_ACCOUNTS)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        session = open_session(fixture.client, plaintext)

        result = session.call_tool(
            TOOL_WHM_SEARCH_ACCOUNTS, {"server_ref": "alpha", "query": "acme", "limit": 5}
        )

    assert result["structuredContent"]["ok"] is True
    run = runs.only
    assert run.tool_name == TOOL_WHM_SEARCH_ACCOUNTS
    assert run.risk is ToolRisk.READ
    assert run.status is ToolRunStatus.COMPLETED
    assert run.args == {"server_ref": "alpha", "query": "acme", "limit": 5}


async def test_a_change_tool_writes_no_row_here() -> None:
    """V46 belongs to the post-approval executor (T38), not to the gate call.

    A CHANGE tool's `tools/call` opens the approval gate and executes nothing (T33), so a
    row written here would record a change that has not happened and may be denied. Driven
    through the middleware with a CHANGE risk map, because no CHANGE tool exists yet.
    """
    tools = build_tool_context()
    middleware = ToolRunAuditMiddleware(
        context=tools.context, tool_risks={CHANGE_TOOL: ToolRisk.CHANGE}
    )
    passed_through = ToolResult(structured_content={"ok": True})

    async def call_next(_context: MiddlewareContext[Any]) -> ToolResult:
        return passed_through

    context: MiddlewareContext[Any] = MiddlewareContext(
        message=mt.CallToolRequestParams(name=CHANGE_TOOL, arguments={"account": "acme"}),
        method="tools/call",
    )

    assert await middleware.on_call_tool(context, call_next) is passed_through
    assert tools.tool_runs.runs == []


# --------------------------------------------------------------------------------------
# V8: arguments and summaries are redacted before they land
# --------------------------------------------------------------------------------------


def test_arguments_are_redacted_before_they_land() -> None:
    """V8, V45: the audit row says what was asked for, never with the credential in it."""
    stored = redacted_args({"server_ref": "alpha", "ssh_password": "hunter2"})

    assert stored == {"server_ref": "alpha", "ssh_password": REDACTED}


def test_no_arguments_is_recorded_as_an_empty_object_not_a_null() -> None:
    """Matches the column's server default: "none" and "not recorded" must not read alike."""
    assert redacted_args(None) == {}


def test_the_result_summary_carries_no_credential_material(scenario) -> None:
    """The summary is a rendering of the tool result, which is transcript-adjacent (V26).

    `whm_list_servers` already renders through `to_safe_dict()` and carries nothing secret,
    so this is the assertion that the *summary path* does not reintroduce one — it runs the
    same redaction, so a later tool cannot be the first to notice.
    """
    sign_in, runs, _ = scenario
    session, _ = sign_in()

    session.call_tool(TOOL_WHM_LIST_SERVERS)
    summary = runs.only.result_summary or ""

    assert "alpha" in summary
    for secret in SECRETS:
        assert secret not in summary


def test_a_long_result_is_truncated_to_the_column_bound() -> None:
    """V45 "truncated". The column is `String(2000)`; an over-long value would fail the
    INSERT and, being fail-closed, take the whole call down with it."""
    summary = result_summary({"ok": True, "rows": ["x" * 100 for _ in range(200)]})

    assert summary is not None
    assert len(summary) == MAX_RESULT_SUMMARY_LENGTH
    assert summary.endswith("...")


def test_a_summary_is_compact_json_of_the_redacted_payload() -> None:
    """Compact so the bound holds as much of the result as possible."""
    summary = result_summary({"ok": True, "api_token": "secret"})

    assert summary is not None
    assert json.loads(summary) == {"ok": True, "api_token": REDACTED}
    assert " " not in summary


# --------------------------------------------------------------------------------------
# Status mapping, in isolation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ok": True, "servers": []}, ToolRunStatus.COMPLETED),
        ({"ok": False, "error_code": "timeout"}, ToolRunStatus.FAILED),
        # V18: an ambiguity is not a success. The call did not do what was asked.
        ({"ok": False, "error_code": "ambiguous_server_ref", "choices": []}, ToolRunStatus.FAILED),
        # A result that cannot be read as a success is not evidence of one.
        ({}, ToolRunStatus.FAILED),
        ({"ok": "yes"}, ToolRunStatus.FAILED),
        (None, ToolRunStatus.FAILED),
    ],
)
def test_status_is_read_off_the_result_envelope(payload, expected: ToolRunStatus) -> None:
    """V20: `ok` is the one field every tool result carries."""
    assert status_for_payload(payload) is expected


# --------------------------------------------------------------------------------------
# conversation_ref: a label, never a scope
# --------------------------------------------------------------------------------------


def test_the_conversation_header_becomes_the_grouping_label(scenario) -> None:
    """V47, DECISIONS §10.4. LibreChat sends no conversation id in the call itself — at pin
    `45cc53c4` `MCPManager.callTool` sends `params: {name, arguments}` with no `_meta` — so
    it arrives as a header T57 fills from `{{LIBRECHAT_BODY_CONVERSATIONID}}`."""
    sign_in, runs, _ = scenario
    session, _ = sign_in(conversation_ref=CONVERSATION_ID)

    session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert runs.only.conversation_ref == CONVERSATION_ID


def test_a_missing_conversation_header_is_null_and_the_call_proceeds(scenario) -> None:
    """It is a label, not a security scope (old V165 dissolved).

    Fail-closing on its absence is what the old design did, and §3.2 removed the reason:
    evidence never crosses calls, so nothing is being scoped.
    """
    sign_in, runs, _ = scenario
    session, _ = sign_in()

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["structuredContent"]["ok"] is True
    assert runs.only.conversation_ref is None


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("over the column bound", "c" * (MAX_CONVERSATION_REF_LENGTH + 1)),
        ("carries a space", "conv 1"),
        ("blank", "   "),
    ],
)
def test_an_unusable_conversation_header_is_dropped_rather_than_stored(
    scenario, label: str, value: str
) -> None:
    """The value lands in Postgres *and* in structlog, the surface V73 closed for
    `x-request-id` — so it gets the same bounded allowlist check.

    Dropped to NULL rather than refused: a malformed header from a client NOA does not
    control would otherwise turn every tool off. An over-long one would also fail the
    INSERT, and the write is fail-closed, so the call would die on a label.
    """
    sign_in, runs, _ = scenario
    session, _ = sign_in(conversation_ref=value)

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["structuredContent"]["ok"] is True, label
    assert runs.only.conversation_ref is None, label


# --------------------------------------------------------------------------------------
# Live: a row actually exists in `tool_runs`
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test engine and session, mutated tables emptied first."""
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
            yield opened
    finally:
        await engine.dispose()


async def test_the_sql_writer_round_trips_a_run_through_postgres(session: AsyncSession) -> None:
    """V45 against the database, not against a double.

    "The middleware called a repository" and "a row exists in `tool_runs`" are different
    claims. This one covers the second: the INSERT satisfies the CHECK constraints and the
    NOT NULLs the migration built, and the UPDATE reaches the same row.
    """
    # Held as a plain id, not as the ORM instance: `expire_all()` below would send a read of
    # `user.id` back to the database outside the greenlet the async session needs.
    user_id = await _insert_user(session, "operator@example.com")

    repository = SQLToolRunRepository(session)
    run_id = await repository.start_run(
        tool_name=TOOL_WHM_LIST_SERVERS,
        requested_by_user_id=user_id,
        risk=ToolRisk.READ,
        conversation_ref=CONVERSATION_ID,
        args={"server_ref": "alpha", "ssh_password": REDACTED},
    )
    await repository.commit()

    mid_flight = (await session.execute(sa.select(ToolRun))).scalar_one()
    assert mid_flight.status is ToolRunStatus.STARTED
    assert mid_flight.completed_at is None

    await repository.finish_run(
        tool_run_id=run_id, status=ToolRunStatus.COMPLETED, result_summary='{"ok":true}'
    )
    await repository.commit()
    session.expire_all()

    stored = (await session.execute(sa.select(ToolRun))).scalar_one()

    assert stored.id == run_id
    assert stored.requested_by_user_id == user_id
    assert stored.risk is ToolRisk.READ
    assert stored.status is ToolRunStatus.COMPLETED
    assert stored.conversation_ref == CONVERSATION_ID
    assert stored.args == {"server_ref": "alpha", "ssh_password": REDACTED}
    assert stored.result_summary == '{"ok":true}'
    assert stored.completed_at is not None
    assert stored.completed_at >= stored.created_at


async def test_a_failed_read_round_trips_through_the_writer(session: AsyncSession) -> None:
    """V20 through the production writer, not through a hand-built `ToolRun`.

    `test_tool_runs_schema.py` proves the *column pair* can hold this. This proves the code
    that writes it does.
    """
    repository = SQLToolRunRepository(session)
    run_id = await repository.start_run(
        tool_name=TOOL_WHM_LIST_SERVERS,
        requested_by_user_id=await _insert_user(session, "failer@example.com"),
        risk=ToolRisk.READ,
        conversation_ref=None,
        args={},
    )
    await repository.commit()
    await repository.finish_run(
        tool_run_id=run_id,
        status=ToolRunStatus.FAILED,
        result_summary='{"ok":false,"error_code":"tool_execution_failed"}',
    )
    await repository.commit()
    session.expire_all()

    stored = (await session.execute(sa.select(ToolRun))).scalar_one()

    assert (stored.risk, stored.status) == (ToolRisk.READ, ToolRunStatus.FAILED)


async def test_finishing_an_unknown_run_touches_nothing(session: AsyncSession) -> None:
    """The terminal write is a bare `UPDATE`; a stale id must not raise or invent a row.

    Reachable only if the two writes disagree about the id, which would be a bug — but the
    audit path swallows its own failure by design, so a raise here would be logged and
    forgotten rather than surfaced.
    """
    repository = SQLToolRunRepository(session)

    await repository.finish_run(
        tool_run_id=uuid4(), status=ToolRunStatus.COMPLETED, result_summary=None
    )
    await repository.commit()

    assert (await session.execute(sa.select(sa.func.count()).select_from(ToolRun))).scalar() == 0


async def test_a_terminal_run_is_never_re_finished(session: AsyncSession) -> None:
    """The first terminal write wins; the second is a no-op (T38).

    Two writers reach one run: the executor, finishing a change that took longer than the
    reaper's deadline, and the reaper, calling that same run abandoned. Without
    `status = STARTED` in the predicate the later `UPDATE` overwrites the earlier answer —
    a completed change re-written as "outcome never observed", or a reaped run flipped to
    `COMPLETED` beside the abandonment receipt `create_if_missing` already made permanent.
    """
    repository = SQLToolRunRepository(session)
    run_id = await repository.start_run(
        tool_name=TOOL_WHM_LIST_SERVERS,
        requested_by_user_id=await _insert_user(session, "twice@example.com"),
        risk=ToolRisk.READ,
        conversation_ref=None,
        args={},
    )
    await repository.finish_run(
        tool_run_id=run_id, status=ToolRunStatus.COMPLETED, result_summary='{"ok":true}'
    )
    await repository.commit()

    await repository.finish_run(
        tool_run_id=run_id,
        status=ToolRunStatus.FAILED,
        result_summary='{"ok":false,"error_code":"tool_run_abandoned"}',
    )
    await repository.commit()
    session.expire_all()

    stored = (await session.execute(sa.select(ToolRun))).scalar_one()

    assert stored.status is ToolRunStatus.COMPLETED
    assert stored.result_summary == '{"ok":true}'


async def _insert_user(session: AsyncSession, email: str) -> UUID:
    user = User(email=email, is_active=True)
    session.add(user)
    await session.commit()
    return user.id
