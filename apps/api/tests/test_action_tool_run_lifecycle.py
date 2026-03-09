from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from noa_api.storage.postgres.models import ActionRequest, ToolRun


async def test_lifecycle_enums_define_machine_stable_values() -> None:
    assert ToolRisk.READ.value == "READ"
    assert ToolRisk.CHANGE.value == "CHANGE"
    assert ActionRequestStatus.PENDING.value == "PENDING"
    assert ActionRequestStatus.APPROVED.value == "APPROVED"
    assert ActionRequestStatus.DENIED.value == "DENIED"
    assert ToolRunStatus.STARTED.value == "STARTED"
    assert ToolRunStatus.COMPLETED.value == "COMPLETED"
    assert ToolRunStatus.FAILED.value == "FAILED"


@dataclass
class _FakeActionToolRunRepository:
    action_requests: dict[UUID, ActionRequest]
    tool_runs: dict[UUID, ToolRun]

    async def create_action_request(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        risk: ToolRisk,
        requested_by_user_id: UUID,
    ) -> ActionRequest:
        created = ActionRequest(
            id=uuid4(),
            thread_id=thread_id,
            tool_name=tool_name,
            args=args,
            risk=risk,
            status=ActionRequestStatus.PENDING,
            requested_by_user_id=requested_by_user_id,
            decided_by_user_id=None,
            decided_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.action_requests[created.id] = created
        return created

    async def decide_action_request(
        self,
        *,
        action_request_id: UUID,
        decided_by_user_id: UUID,
        status: ActionRequestStatus,
    ) -> ActionRequest | None:
        existing = self.action_requests.get(action_request_id)
        if existing is None:
            return None
        existing.status = status
        existing.decided_by_user_id = decided_by_user_id
        existing.decided_at = datetime.now(UTC)
        existing.updated_at = datetime.now(UTC)
        return existing

    async def start_tool_run(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        action_request_id: UUID | None,
        requested_by_user_id: UUID | None,
    ) -> ToolRun:
        started = ToolRun(
            id=uuid4(),
            thread_id=thread_id,
            tool_name=tool_name,
            args=args,
            status=ToolRunStatus.STARTED,
            result=None,
            error=None,
            action_request_id=action_request_id,
            requested_by_user_id=requested_by_user_id,
            created_at=datetime.now(UTC),
            completed_at=None,
        )
        self.tool_runs[started.id] = started
        return started

    async def finish_tool_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result: dict[str, object] | None,
        error: str | None,
    ) -> ToolRun | None:
        existing = self.tool_runs.get(tool_run_id)
        if existing is None:
            return None
        existing.status = status
        existing.result = result
        existing.error = error
        existing.completed_at = datetime.now(UTC)
        return existing


async def test_action_tool_run_service_transitions_core_states() -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)
    thread_id = uuid4()
    actor_id = uuid4()

    request = await service.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "k", "value": "v"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=actor_id,
    )
    assert request.status == ActionRequestStatus.PENDING
    assert request.risk == ToolRisk.CHANGE

    approved = await service.approve_action_request(action_request_id=request.id, decided_by_user_id=actor_id)
    assert approved is not None
    assert approved.status == ActionRequestStatus.APPROVED

    denied = await service.deny_action_request(action_request_id=request.id, decided_by_user_id=actor_id)
    assert denied is not None
    assert denied.status == ActionRequestStatus.DENIED

    run = await service.start_tool_run(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "k", "value": "v"},
        action_request_id=request.id,
        requested_by_user_id=actor_id,
    )
    assert run.status == ToolRunStatus.STARTED

    completed = await service.complete_tool_run(tool_run_id=run.id, result={"ok": True})
    assert completed is not None
    assert completed.status == ToolRunStatus.COMPLETED
    assert completed.result == {"ok": True}
    assert completed.error is None


async def test_action_tool_run_service_can_fail_tool_run() -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)
    thread_id = uuid4()

    run = await service.start_tool_run(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "k", "value": "v"},
        action_request_id=None,
        requested_by_user_id=None,
    )

    failed = await service.fail_tool_run(tool_run_id=run.id, error="boom")

    assert failed is not None
    assert failed.status == ToolRunStatus.FAILED
    assert failed.result is None
    assert failed.error == "boom"
