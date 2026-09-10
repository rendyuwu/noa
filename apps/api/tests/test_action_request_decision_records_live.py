"""What a decision records, against a real Postgres.

`test_action_request_decision_routes.py` drives the same service over an in-memory
repository, which proves the ordering but cannot prove the claim that is *about the database*:

- **One transaction covers the decision and the run.** "Both commit together" is a claim
  about a transaction boundary, and an in-memory repository has none. The CHECK that refuses
  a decided row without a reason is the same kind of claim one layer down — it is the
  constraint itself, not a service that happens to agree with it.

So this file is about the rows an approval and a denial leave behind: the `action_requests`
row that becomes the authorization, the `tool_runs` row that describes the change
(V46, V47), and the four identity fields that row carries beside the arguments (V108).

The refusals and V28's row lock are `test_action_request_decisions_live.py`; V31's per-user
cap is `test_action_request_change_cap_live.py`. Split out of the first when it passed the
900-line limit; all three share the row helpers in `support.action_decisions` and the
scratch-database fixtures in `support.database`.

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.

`SQLActionDecisionRepository` runs for real throughout; only the executor is a recorder,
because T38 is what makes it do anything.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.approvals.decisions import SQLActionDecisionRepository
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import ActionRequest
from support.action_decisions import (
    APPROVAL_CONTEXT,
    CHANGE_TOOL,
    CONVERSATION_ID,
    REASON,
    build_live_decision_service,
    insert_user,
    open_request,
    read_request,
    read_runs,
)
from support.database import migrated_database, session_factory

SCRATCH_DB = "noa_action_decision_records_test"

OPERATOR_EMAIL = "operator@example.com"

# One reseller credential, standing in for §V108's four identity fields (the row's name, its
# `api_username`, its host and the account's owner). Named after the credential because that is
# what a reseller row is named after (V109(b)).
RESELLER = "web08cpnpool01"

# Planted in the evidence under a key `SENSITIVE_KEYS` names, to prove the audit row's reader is
# a whitelist rather than a copy. Nothing in production puts a token there — the point is that
# the reader would not carry one if something did.
PLANTED_TOKEN = "planted-api-token-value"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory over a freshly emptied database (`support.database`).

    Function-scoped because an asyncpg connection belongs to the loop that opened it.
    """
    async with session_factory(database_url) as sessions:
        yield sessions


# --------------------------------------------------------------------------------------
# The approval, end to end through real SQL
# --------------------------------------------------------------------------------------


async def test_approval_writes_the_change_tool_run(factory) -> None:
    """V46, V47: an approved CHANGE produces a `tool_runs` row that describes it.

    `risk=CHANGE` is fixed by the repository rather than passed in — every row in
    `action_requests` is a change by construction, and a parameter would be somewhere
    for `READ` to be written into an approved change's audit row.

    The run is `STARTED`, not `COMPLETED`: nothing has executed. T38's executor moves it, and
    its reaper is what covers a run that never gets there.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        service, executor = build_live_decision_service(session)
        outcome = await service.approve(
            action_request_id=action_request_id,
            caller_user_id=user_id,
            reason=REASON,
        )

    runs = await read_runs(factory)
    assert len(runs) == 1
    run = runs[0]

    assert run.id == outcome.tool_run_id
    assert run.tool_name == CHANGE_TOOL
    assert run.risk is ToolRisk.CHANGE
    assert run.status is ToolRunStatus.STARTED
    assert run.requested_by_user_id == user_id
    assert run.conversation_ref == CONVERSATION_ID
    assert run.args == APPROVAL_CONTEXT["arguments"]
    assert run.completed_at is None
    assert executor.only.tool_run_id == run.id


async def test_an_approved_change_records_the_credential_it_acted_as_beside_its_arguments(
    factory,
) -> None:
    """§V108: the audit row names the identity, not only the machine.

    A privileged write whose credential is not recorded is not auditable, and `tool_runs` is
    the table the audit surface reads (`admin_audit.py`) — `action_receipts` carries the same
    four fields for a different reader, and a fact reachable only through a join nobody
    performs is recorded rather than reported.

    The four come out of the gate's stored evidence by **whitelist**
    (`core.approvals.context.AUDIT_IDENTITY_KEYS`), which is the half worth testing: `evidence`
    is a free-form preflight payload whose shape each CHANGE tool decides, so a passthrough
    would copy whatever a future tool records into a second table. The evidence below carries
    two things that must not travel — an account summary that belongs on the card, and a
    token-shaped value under the very key `SENSITIVE_KEYS` names — and neither may appear in
    the row.

    The additive half is asserted one test up: `test_approval_writes_the_change_tool_run` uses
    an `APPROVAL_CONTEXT` whose evidence names none of the four and still expects `args` to
    equal the arguments exactly.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(
        factory,
        requested_by_user_id=user_id,
        approval_context={
            "arguments": {"server_ref": RESELLER, "username": "acmeco"},
            "requester": {"email": OPERATOR_EMAIL, "librechat_user_id": "librechat-user-1"},
            "evidence": {
                "server": RESELLER,
                "api_username": RESELLER,
                "host": "web08.example.net",
                "owner": RESELLER,
                "account": {"user": "acmeco", "suspended": False},
                "api_token": PLANTED_TOKEN,
            },
        },
    )

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        await service.approve(
            action_request_id=action_request_id, caller_user_id=user_id, reason=REASON
        )

    runs = await read_runs(factory)
    assert len(runs) == 1
    args = runs[0].args

    assert args["credential"] == {
        "server": RESELLER,
        "api_username": RESELLER,
        "host": "web08.example.net",
        "owner": RESELLER,
    }
    # What was asked for is still exactly what was asked for: the identity is beside the
    # arguments, never merged into them (`ActionResultView` reads that half, V76).
    assert args["server_ref"] == RESELLER
    assert args["username"] == "acmeco"
    assert set(args) == {"server_ref", "username", "credential"}
    assert PLANTED_TOKEN not in json.dumps(args)
    assert "account" not in json.dumps(args)


async def test_a_change_that_names_a_machine_but_no_credential_records_only_its_arguments(
    factory,
) -> None:
    """The additive half of §V108, against evidence a real tool actually writes.

    This is the test the review found wanting, and the reason it was wanting is worth keeping:
    `evidence["server"]` is not the WHM account pair's alone. `proxmox_nic`, `proxmox_password`,
    `pmg_whitelist` and both WHM firewall tools all write it for the machine they act on, so a
    whitelist keyed on "any of the four" gave every one of those approvals an
    `args["credential"]` holding a machine name — a block labelled credential with no credential
    in it, which is the precise misreading the nested key exists to prevent. The old fixture
    could not see it, because its evidence shape (`{account, suspended, domain}`) is one no tool
    produces: a control that cannot separate because its fixture does not represent the subject.

    So the evidence below is `proxmox_nic`'s, key for key (`mcp_tools/proxmox_nic.py`), and the
    claim is that such an approval's audit row is byte-identical to what it was before §V108:
    the arguments, and no `credential` key at all. `api_username` is what gates the block, and
    nothing but the account pair records one.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    arguments = {"server_ref": "pve-01", "node": "pve", "vmid": 120, "action": "disconnect"}
    action_request_id = await open_request(
        factory,
        requested_by_user_id=user_id,
        approval_context={
            "arguments": arguments,
            "requester": {"email": OPERATOR_EMAIL, "librechat_user_id": "librechat-user-1"},
            "evidence": {
                "server_id": str(uuid4()),
                "server": "pve-01",
                "node": "pve",
                "vmid": 120,
                "net": "net0",
                "action": "disconnect",
                "nic": {"key": "net0", "auto_selected": True},
                "vm": {"name": "web-01", "status": "running"},
            },
        },
    )

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        await service.approve(
            action_request_id=action_request_id, caller_user_id=user_id, reason=REASON
        )

    runs = await read_runs(factory)
    assert len(runs) == 1
    assert runs[0].args == arguments
    assert "credential" not in runs[0].args


async def test_the_decision_row_is_the_authorization_after_approval(factory) -> None:
    """V23: what the row says is the answer, so this is what the row says."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)
    before = datetime.now(UTC)

    async with factory() as session:
        service, _ = build_live_decision_service(session)
        outcome = await service.approve(
            action_request_id=action_request_id,
            caller_user_id=user_id,
            reason=REASON,
        )

    stored = await read_request(factory, action_request_id)

    assert stored.status is ActionRequestStatus.APPROVED
    assert stored.reason == REASON
    assert stored.tool_run_id == outcome.tool_run_id
    # Asserted as a bound, not as an equality: `decided_at` is clock-stamped, and an equality
    # compare against a second `now()` is the flake V87 is about.
    assert stored.decided_at is not None
    assert before <= stored.decided_at <= datetime.now(UTC)


async def test_decision_and_run_commit_together(factory) -> None:
    """One transaction, so `APPROVED` with no run cannot exist.

    Driven by making the *commit* impossible rather than by patching the service: the
    `tool_runs` FK is deferred to nothing, so instead the reason is emptied behind the
    endpoint's guard, which trips the database CHECK at flush time. Whatever the cause, the
    property is the same — if the decision does not land, neither does the run.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        repository = SQLActionDecisionRepository(session)
        locked = await repository.lock_for_decision(action_request_id=action_request_id)
        assert locked is not None

        tool_run_id = await repository.start_change_run(
            tool_name=locked.tool_name,
            requested_by_user_id=user_id,
            conversation_ref=locked.conversation_ref,
            args=locked.redacted_arguments,
        )
        # The CHECK fires on the UPDATE itself here rather than at COMMIT (it is not
        # deferrable), so both statements sit inside the expectation — which point it
        # surfaces at is Postgres's business, not this test's.
        with pytest.raises(IntegrityError):
            await repository.write_decision(
                action_request_id=action_request_id,
                status=ActionRequestStatus.APPROVED,
                reason="   ",  # what the CHECK refuses (T34(f))
                decided_at=datetime.now(UTC),
                tool_run_id=tool_run_id,
            )
            await repository.commit()
        await session.rollback()

    assert (await read_request(factory, action_request_id)).status is ActionRequestStatus.PENDING
    assert await read_runs(factory) == []


async def test_database_refuses_a_decided_row_without_a_reason(factory) -> None:
    """T34(f) at the mechanism: the CHECK holds against a writer that is not the endpoint.

    The endpoint's 409 is `test_action_request_decision_routes.py`'s. This is the guarantee
    that survives T38's executor, T39's sweep and anything run by hand against the database.
    """
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                sa.update(ActionRequest)
                .where(ActionRequest.id == action_request_id)
                .values(status=ActionRequestStatus.DENIED, decided_at=datetime.now(UTC))
            )
            await session.commit()
        await session.rollback()


async def test_denial_writes_no_run(factory) -> None:
    """A denied change did not run, so the audit trail must not claim one."""
    user_id = await insert_user(factory, OPERATOR_EMAIL)
    action_request_id = await open_request(factory, requested_by_user_id=user_id)

    async with factory() as session:
        service, executor = build_live_decision_service(session)
        await service.deny(
            action_request_id=action_request_id,
            caller_user_id=user_id,
            reason="Ticket does not authorise this; asking the customer to confirm first.",
        )

    stored = await read_request(factory, action_request_id)

    assert stored.status is ActionRequestStatus.DENIED
    assert stored.tool_run_id is None
    assert await read_runs(factory) == []
    assert executor.started == []
