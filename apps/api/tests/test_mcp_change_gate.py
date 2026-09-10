"""A CHANGE `tools/call` opens a request; it never runs a change.

The schema landed and wrote nothing to it. This is the test that the gate writes
the row, that the row is the authorization rather than a note about one, and that neither an
argument nor a claim can move it past PENDING.

Three levels, for three different claims:

- **Against `open_change_request`** — the whole gate, driven through the real identity and
  header contextvars (`http_request_context` with an `AuthenticatedUser`), so
  `current_mcp_identity` and `read_conversation_ref` are the production functions rather than
  patched names. This is where the cookie/CSRF decision boundary, the status-read verdict and
  the single reason field are provable.
- **Against the guards** — the reason check and the context builder on their own. Cheap, and
  they say which piece broke.
- **Live** — `SQLActionRequestRepository` against a scratch Postgres. "The gate called a
  repository" and "a PENDING row exists that the status verdict can be answered from" are
  different claims, and only the second one is the invariant.

**There is no mount-level test here, and that gap is closed elsewhere.** When this file was
written no CHANGE tool was registered, so nothing reached the gate through `tools/call` — the
same shape as the tool-run writer's CHANGE branch, driven at middleware level for the same reason
(`test_mcp_tool_audit.py::test_a_change_tool_writes_no_row_here`). The suspend tool put the gate
on the real mount and the unsuspend tool added the second lane; those mount tests live beside
their tools
(`test_whm_tools_{suspend,unsuspend}_account.py`), because a mount lane asserts a tool's
registration as much as this function.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from core.approvals.errors import (
    ChangeEvidenceRequiredError,
    ChangeGateError,
    ChangeGateUnavailableError,
    ChangeReasonForbiddenError,
)
from core.approvals.repository import ActionRequestRepository, SQLActionRequestRepository
from core.db.lifecycle import ActionRequestStatus
from core.db.models import ActionRequest, User
from core.secrets.redaction import REDACTED
from noa_api.api.errors import FALLBACK_STATUS, status_for
from noa_api.mcp_audit import CONVERSATION_REF_HEADER
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.change_gate import (
    FORBIDDEN_REASON_KEYS,
    LOG_CHANGE_GATE_WRITE_FAILED,
    LOG_CHANGE_REQUEST_OPENED,
    PendingChangeRequest,
    assert_no_reason_argument,
    build_approval_context,
    open_change_request,
)
from support.action_requests import FakeActionRequestRepository
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.mcp_identity import (
    DISPLAY_NAME,
    EMAIL,
    LIBRECHAT_USER,
    authenticated_caller,
    http_request_context,
)
from support.servers import PENDING_TTL_SECONDS, ToolFixture, build_tool_context

SCRATCH_DB = "noa_change_gate_test"

# The first CHANGE tool. Named here rather than built: this file tests the gate, and a
# tool would only be a second thing that could be wrong.
CHANGE_TOOL = "whm_suspend_account"

# What LibreChat fills from `{{LIBRECHAT_BODY_CONVERSATIONID}}`.
CONVERSATION_ID = "1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12"

# A CHANGE call's arguments and the preflight those arguments produced. The evidence is what
# the card shows as before-state — born in-process, never crossing a tool boundary.
ARGUMENTS: dict[str, Any] = {"server_ref": "alpha", "account": "acmeco"}
EVIDENCE: dict[str, Any] = {"account": "acmeco", "suspended": False, "domain": "acme.example"}


async def open_gate(
    tools: ToolFixture,
    *,
    arguments: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    user_id: UUID | None = None,
    conversation_ref: str | None = CONVERSATION_ID,
    tool_name: str = CHANGE_TOOL,
) -> tuple[PendingChangeRequest, UUID]:
    """Call the real gate inside a real request context; return what it wrote and for whom."""
    user, resolved = authenticated_caller(user_id)
    headers = {CONVERSATION_REF_HEADER: conversation_ref} if conversation_ref is not None else {}

    with http_request_context(headers, user=user):
        opened = await open_change_request(
            tool_name=tool_name,
            arguments=ARGUMENTS if arguments is None else arguments,
            evidence=EVIDENCE if evidence is None else evidence,
            context=tools.context,
        )
    return opened, resolved


# --------------------------------------------------------------------------------------
# The row is the authorization, and it starts PENDING
# --------------------------------------------------------------------------------------


async def test_the_gate_opens_a_pending_request() -> None:
    """CHANGE goes through the approval gate: a call produces a question in the database,
    not an execution."""
    tools = build_tool_context()

    opened, user_id = await open_gate(tools)

    request = tools.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.tool_name == CHANGE_TOOL
    assert request.requested_by_user_id == user_id
    assert request.conversation_ref == CONVERSATION_ID
    assert opened.action_request_id == request.action_request_id
    assert opened.status is ActionRequestStatus.PENDING


async def test_the_requester_comes_from_the_token_not_the_arguments() -> None:
    """The requester match: an argument-supplied requester would be an argument-supplied
    authorization.

    `requested_by_user_id` is what the approval endpoint compares the deciding operator
    against, so a caller who could name someone else in the arguments could hand their
    own pending change to another operator's approval card.
    """
    tools = build_tool_context()
    someone_else = uuid4()

    _, user_id = await open_gate(
        tools,
        arguments={**ARGUMENTS, "requested_by_user_id": str(someone_else), "user_id": "root"},
    )

    assert tools.action_requests.only.requested_by_user_id == user_id
    assert user_id != someone_else


async def test_an_approved_request_named_in_the_arguments_still_only_opens_a_new_pending_one() -> (
    None
):
    """ "May this run?" is read from the row's own `status`, never from a tool argument.

    The shape of the attack this closes: a model that has seen one approval — or been talked
    into claiming one — calls the CHANGE tool again carrying the approved id. The gate has no
    parameter for a decision and no branch that reads one, so the call produces a second
    PENDING request and the operator is asked again.
    """
    tools = build_tool_context()
    already_approved = uuid4()

    opened, _ = await open_gate(
        tools,
        arguments={
            **ARGUMENTS,
            "action_request_id": str(already_approved),
            "approved": True,
            "status": "APPROVED",
        },
    )

    assert opened.status is ActionRequestStatus.PENDING
    assert opened.action_request_id != already_approved
    assert tools.action_requests.only.status is ActionRequestStatus.PENDING


async def test_the_gate_writes_no_decision() -> None:
    """Deciding is a cookie POST from a NOA-origin document, and this is not one.

    Asserted against the writer's surface rather than against the recorded row's NULLs: a
    row is only evidence that this particular call decided nothing, while the absence of any
    way to write `reason`, `decided_at`, `tool_run_id` or a non-PENDING `status` is evidence
    that no call from the MCP path can. `status` is on that list too — a repository that
    took it as a parameter would put a second door on the authorization beside the cookie-POST
    boundary, on the side an LLM can reach.
    """
    tools = build_tool_context()
    await open_gate(tools)

    for writer in (ActionRequestRepository, SQLActionRequestRepository):
        exposed = {name for name in vars(writer) if not name.startswith("_")}
        assert exposed == {"create_pending", "commit"}, writer.__name__

    accepted = set(inspect.signature(SQLActionRequestRepository.create_pending).parameters)
    assert not accepted & {"reason", "decided_at", "tool_run_id", "status"}


async def test_the_gate_commits_its_own_transaction() -> None:
    """The MCP tool path has no request transaction to join (`core.approvals.repository`).

    A pending request that rolls back with the call that opened it is a request the operator
    never sees, and a tool result that points at an approval card for a row that is not
    there.
    """
    tools = build_tool_context()

    await open_gate(tools)

    assert tools.action_requests.commits == [ActionRequestStatus.PENDING.value]
    assert tools.action_requests.only.committed == 1


async def test_the_gate_writes_no_tool_run_row() -> None:
    """The gate call is not an execution, so it is not a run.

    The run-plus-receipt row belongs to the executor that runs after approval. Recording one here
    would put a change that has not happened — and may be denied — into the audit trail.
    """
    tools = build_tool_context()

    await open_gate(tools)

    assert tools.tool_runs.runs == []


# --------------------------------------------------------------------------------------
# No reason exists at call time, under any spelling
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(FORBIDDEN_REASON_KEYS))
async def test_an_argument_named_like_a_reason_refuses_the_gate(key: str) -> None:
    """The reason is typed by an operator at approve time and nowhere else.

    Refused rather than silently dropped: dropping the key would leave the offending tool's
    schema wrong, the LLM still being asked to author a reason, and nothing red.
    """
    tools = build_tool_context()

    with pytest.raises(ChangeReasonForbiddenError):
        await open_gate(tools, arguments={**ARGUMENTS, key: "customer asked"})

    assert tools.action_requests.requests == []


def test_the_reason_guard_reads_keys_the_way_redaction_does() -> None:
    """Case and surrounding whitespace must not be a way past the no-reason rule."""
    with pytest.raises(ChangeReasonForbiddenError):
        assert_no_reason_argument({"  Reason  ": "x"})
    with pytest.raises(ChangeReasonForbiddenError):
        assert_no_reason_argument({"PROPOSED_REASON": "x"})


def test_the_reason_guard_admits_an_argument_that_merely_contains_the_word() -> None:
    """An explicit set, not a substring rule (see `FORBIDDEN_REASON_KEYS`).

    Without this case the guard could be tightened into a substring match and nothing would
    go red — until some future integration's `reason_code` field made a legitimate CHANGE
    unsubmittable, which is a refusal for the wrong cause.
    """
    assert_no_reason_argument({"reason_code": "SUSPENDED_BY_POLICY", "unreasonable": True})


async def test_a_nested_payload_key_is_not_a_reason() -> None:
    """Top level only: a reason is a first-class parameter of a call or it is not one."""
    tools = build_tool_context()

    await open_gate(tools, arguments={"server_ref": "alpha", "payload": {"reason": "upstream"}})

    assert tools.action_requests.only.approval_context["arguments"]["payload"] == {
        "reason": "upstream"
    }


async def test_every_registered_change_tool_declares_no_reason_parameter() -> None:
    """The no-reason rule on the schema, swept over what the server actually exposes.

    Written while every registered tool was still a READ — same reason as the row-cap rule: it
    has to exist before the second instance, because the second is where nobody re-reads it. It
    stopped being vacuous at the suspend tool and covers two CHANGE tools since the unsuspend
    tool — `build_mcp_server` registers them, so the sweep is over the real surface. The case
    below is what keeps it from passing as a tautology.
    """
    tools = build_tool_context()
    server = build_mcp_server(tool_context=tools.context)

    for tool in await server.list_tools():
        properties = (tool.parameters or {}).get("properties", {})
        assert_no_reason_argument(dict.fromkeys(properties, None))


async def test_the_schema_sweep_separates_a_tool_that_carries_a_reason() -> None:
    """The sweep above must be able to fail.

    A probe server, because `register_mcp_tools` refuses an uncatalogued name (a tool ships
    with its gate, never per-tool code) and there is no CHANGE tool to break. What is asserted
    is the predicate the sweep applies, against a schema that genuinely carries the parameter
    the no-reason rule forbids.
    """
    probe = FastMCP("probe")

    @probe.tool(name="probe_change", description="A CHANGE tool that should not exist.")
    async def probe_change(account: str, reason: str) -> dict[str, Any]:  # pragma: no cover
        return {"ok": True, "account": account, "reason": reason}

    exposed = await probe.list_tools()
    properties = (exposed[0].parameters or {}).get("properties", {})
    assert "reason" in properties

    with pytest.raises(ChangeReasonForbiddenError):
        assert_no_reason_argument(dict.fromkeys(properties, None))


# --------------------------------------------------------------------------------------
# The context is built at gate time and persisted
# --------------------------------------------------------------------------------------


async def test_the_context_holds_arguments_provenance_and_preflight_evidence() -> None:
    """What the approval card renders is captured once, at the moment of the call."""
    tools = build_tool_context()

    await open_gate(tools)

    assert tools.action_requests.only.approval_context == {
        "arguments": ARGUMENTS,
        "requester": {"email": EMAIL, "librechat_user_id": LIBRECHAT_USER},
        "evidence": EVIDENCE,
    }


async def test_the_context_holds_only_what_no_column_holds() -> None:
    """The schema's rule, one layer up: two records of one moment can disagree.

    `tool_name`, `conversation_ref`, the requester's id and the created-at stamp are all
    columns on `action_requests`. Copying any of them into the JSONB would be the mistake
    the table's design avoided by dropping `args`, `risk`, `decided_by_user_id` and `updated_at`.
    """
    tools = build_tool_context()

    _, user_id = await open_gate(tools)

    context = tools.action_requests.only.approval_context
    assert set(context) == {"arguments", "requester", "evidence"}
    assert str(user_id) not in repr(context)
    for duplicated in ("tool_name", "conversation_ref", "requested_at", "created_at", "status"):
        assert duplicated not in context


async def test_a_gate_call_without_preflight_evidence_is_refused() -> None:
    """A card that asks for authorisation and describes nothing.

    The table's model docstring already says the gate always holds provenance, arguments and
    the in-process preflight. This is that sentence as a mechanism, so every CHANGE tool
    inherits it by construction rather than by each remembering.
    """
    tools = build_tool_context()

    with pytest.raises(ChangeEvidenceRequiredError):
        await open_gate(tools, evidence={})

    assert tools.action_requests.requests == []


async def test_a_credential_shaped_argument_is_redacted_in_the_stored_context() -> None:
    """The same redactor the audit path uses, not a second copy of the rule.

    CHANGE arguments should carry no credential by construction — server-side password
    generation keeps secrets on the yopass path — so this is the net rather than the fix, and
    the net is where a future tool's argument name lands.
    """
    tools = build_tool_context()

    await open_gate(
        tools,
        arguments={**ARGUMENTS, "password": "hunter2", "nested": {"api_token": "whm-secret"}},
    )

    stored = tools.action_requests.only.approval_context["arguments"]
    assert stored["password"] == REDACTED
    assert stored["nested"]["api_token"] == REDACTED
    assert "hunter2" not in repr(stored)
    assert "whm-secret" not in repr(stored)


def test_the_context_builder_is_json_native() -> None:
    """The payload lands in JSONB and comes back as plain Python.

    A `Mapping` subclass or a `datetime` handed in here would round-trip into something an
    equality against the original no longer matches, which is how a persisted-context assertion
    becomes "the shapes are close enough".
    """
    context = build_approval_context(
        arguments={"a": 1},
        evidence={"b": [1, 2]},
        requester_email=EMAIL,
        librechat_user_id=LIBRECHAT_USER,
    )

    assert type(context) is dict
    assert type(context["arguments"]) is dict
    assert type(context["evidence"]) is dict


# --------------------------------------------------------------------------------------
# Every pending request carries a deadline
# --------------------------------------------------------------------------------------


async def test_the_deadline_comes_from_the_configured_ttl() -> None:
    """A request without one cannot expire, and "pending forever" is what the TTL removes.

    Asserted as a window rather than an equality: `expires_at` is computed from the clock at
    call time, so a bare `==` would be a coin flip across a second boundary. The
    window is far narrower than any other TTL in the tree, so a gate that read the wrong
    setting still fails.
    """
    tools = build_tool_context()

    before = datetime.now(UTC)
    opened, _ = await open_gate(tools)
    after = datetime.now(UTC)

    expected = timedelta(seconds=PENDING_TTL_SECONDS)
    assert before + expected <= opened.expires_at <= after + expected
    assert tools.action_requests.only.expires_at == opened.expires_at


async def test_a_different_ttl_moves_the_deadline() -> None:
    """The value is read, not hardcoded — the compare above still separates."""
    tools = build_tool_context(pending_ttl_seconds=60)

    opened, _ = await open_gate(tools)

    assert opened.expires_at < datetime.now(UTC) + timedelta(seconds=PENDING_TTL_SECONDS)


def test_the_mount_hands_the_tool_path_the_configured_pending_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production wiring, not a fixture's: `create_app` reads `Settings` and passes it.

    The configured value is deliberately **not** the `Settings` default. `mounted_app` is
    not used here for exactly that reason: it patches `get_settings` with the harness's own
    builder, whose `approval_pending_ttl_seconds` is 3600 — the same number a hardcoded
    literal in `create_app` would produce, so the assertion could not separate the two — the
    compare has to keep separating the cases, not just pass. Patching `get_settings` here
    instead makes the number one nothing else holds.
    """
    from noa_api import main
    from noa_api.mcp_tools.context import build_mcp_tool_context
    from support.auth import build_settings

    configured = 1234
    captured: dict[str, Any] = {}

    def capturing_builder(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return build_mcp_tool_context(**kwargs)

    monkeypatch.setattr(
        main, "get_settings", lambda: build_settings(approval_pending_ttl_seconds=configured)
    )
    monkeypatch.setattr(main, "build_mcp_tool_context", capturing_builder)
    main.create_app()

    assert captured["pending_ttl_seconds"] == configured


# --------------------------------------------------------------------------------------
# Fail closed: no row, no change
# --------------------------------------------------------------------------------------


async def test_a_write_failure_refuses_the_change() -> None:
    """The status verdict has no truthful answer without a row, so the change does not proceed.

    The same argument the tool-run writer made for the opening `tool_runs` write, one table
    over: running anyway would leave the invariant asserted by prose and held by nothing.
    """
    requests = FakeActionRequestRepository()
    requests.fail_create = RuntimeError("connection refused")
    tools = build_tool_context(action_requests=requests)

    with capture_logs() as logs, pytest.raises(ChangeGateUnavailableError):
        await open_gate(tools)

    assert requests.requests == []
    assert any(entry["event"] == LOG_CHANGE_GATE_WRITE_FAILED for entry in logs)


async def test_the_refusal_names_no_internal_detail_to_the_model() -> None:
    """`message` is what a model and an operator read; the cause stays in the logs — raw
    exceptions never reach the model."""
    requests = FakeActionRequestRepository()
    requests.fail_create = RuntimeError("password=hunter2 host=db.internal")
    tools = build_tool_context(action_requests=requests)

    with pytest.raises(ChangeGateUnavailableError) as raised:
        await open_gate(tools)

    assert "hunter2" not in raised.value.message
    assert "db.internal" not in raised.value.message
    assert raised.value.error_code == "change_gate_unavailable"


async def test_the_opened_request_is_logged_without_its_payload() -> None:
    """Identifiers only — never the arguments, never the evidence."""
    tools = build_tool_context()

    with capture_logs() as logs:
        opened, user_id = await open_gate(tools)

    entry = next(item for item in logs if item["event"] == LOG_CHANGE_REQUEST_OPENED)
    assert entry["action_request_id"] == str(opened.action_request_id)
    assert entry["requested_by_user_id"] == str(user_id)
    assert "acmeco" not in repr(entry)


def test_every_change_gate_error_is_mapped_explicitly() -> None:
    """No gate refusal may reach the 503 *fallback* — that means "unclassified"."""

    def subclasses(klass: type[ChangeGateError]) -> set[type[ChangeGateError]]:
        found = {klass}
        for child in klass.__subclasses__():
            found |= subclasses(child)
        return found

    for klass in subclasses(ChangeGateError):
        assert klass in _mapped_error_classes(), f"{klass.__name__} has no explicit status"
        assert status_for(klass.__new__(klass)) in {500, FALLBACK_STATUS}


def _mapped_error_classes() -> set[type[Exception]]:
    """The classes `noa_api.api.errors` maps by name, read rather than retyped."""
    from noa_api.api.errors import STATUS_BY_ERROR

    return set(STATUS_BY_ERROR)


# --------------------------------------------------------------------------------------
# Live: a row the status verdict can be answered from
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as opened:
            yield opened
    finally:
        await engine.dispose()


async def insert_user(session: AsyncSession) -> UUID:
    user = User(email=EMAIL, display_name=DISPLAY_NAME, is_active=True)
    session.add(user)
    await session.flush()
    await session.commit()
    return user.id


async def test_the_repository_writes_a_row_the_status_verdict_can_be_answered_from(
    session: AsyncSession,
) -> None:
    """The row exists, it says PENDING, and the context survives the round trip.

    The JSONB is compared byte-equal to what the gate built, because the persisted-context rule
    is that the payload is *persisted* rather than rebuilt at render time — a comparison on a
    subset would pass against a row that dropped half of it.
    """
    user_id = await insert_user(session)
    context = build_approval_context(
        arguments=ARGUMENTS,
        evidence=EVIDENCE,
        requester_email=EMAIL,
        librechat_user_id=LIBRECHAT_USER,
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=PENDING_TTL_SECONDS)

    repository = SQLActionRequestRepository(session)
    request_id = await repository.create_pending(
        tool_name=CHANGE_TOOL,
        requested_by_user_id=user_id,
        conversation_ref=CONVERSATION_ID,
        approval_context=context,
        expires_at=expires_at,
    )
    await repository.commit()

    stored = await session.get(ActionRequest, request_id)
    assert stored is not None
    assert stored.status is ActionRequestStatus.PENDING
    assert stored.tool_name == CHANGE_TOOL
    assert stored.requested_by_user_id == user_id
    assert stored.conversation_ref == CONVERSATION_ID
    assert stored.approval_context == context
    assert stored.expires_at == expires_at
    # The three columns a decision writes are untouched.
    assert (stored.reason, stored.decided_at, stored.tool_run_id) == (None, None, None)


async def test_the_status_a_verdict_read_sees_is_pending(session: AsyncSession) -> None:
    """Read back through SQL rather than through the ORM identity map.

    `session.get` above can answer from the object it just flushed; this asks Postgres.
    """
    user_id = await insert_user(session)
    repository = SQLActionRequestRepository(session)
    request_id = await repository.create_pending(
        tool_name=CHANGE_TOOL,
        requested_by_user_id=user_id,
        conversation_ref=None,
        approval_context={"arguments": {}, "requester": {}, "evidence": {"probe": True}},
        expires_at=datetime.now(UTC) + timedelta(seconds=PENDING_TTL_SECONDS),
    )
    await repository.commit()

    status_value = await session.scalar(
        sa.text("SELECT status FROM action_requests WHERE id = :id"), {"id": request_id}
    )
    assert status_value == ActionRequestStatus.PENDING.value
