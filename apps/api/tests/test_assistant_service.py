from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from noa_api.api.routes.assistant import AssistantService
from noa_api.api.routes.assistant_tool_execution import build_tool_result_part
from noa_api.core.tools.registry import ToolDefinition
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    ToolRisk,
    ToolRunStatus,
)
from noa_api.storage.postgres.models import ActionRequest, ToolRun


def test_build_tool_result_part_shapes_payload() -> None:
    assert build_tool_result_part(
        tool_name="set_demo_flag",
        tool_call_id="tool-call-1",
        result={"ok": True},
        is_error=False,
    ) == {
        "type": "tool-result",
        "toolName": "set_demo_flag",
        "toolCallId": "tool-call-1",
        "result": {"ok": True},
        "isError": False,
    }


@dataclass
class _FakeRunner:
    async def run_turn(self, **kwargs):
        raise AssertionError("runner should not be called in these tests")


@dataclass
class _ProposalRunner:
    async def run_turn(self, **kwargs):
        _ = kwargs
        from noa_api.core.agent.runner import AgentMessage, AgentRunnerResult

        return AgentRunnerResult(
            messages=[
                AgentMessage(
                    role="assistant",
                    parts=[
                        {
                            "type": "tool-call",
                            "toolName": "request_approval",
                            "toolCallId": "request-approval-1",
                            "args": {
                                "actionRequestId": str(uuid4()),
                                "toolName": "set_demo_flag",
                                "risk": "CHANGE",
                                "arguments": {"key": "feature_x", "value": True},
                            },
                        }
                    ],
                )
            ],
            text_deltas=[],
        )


@dataclass
class _FakeSession:
    added: list[object] = field(default_factory=list)
    executed: list[object] = field(default_factory=list)

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: object):
        self.executed.append(stmt)

        from sqlalchemy.sql.dml import Insert

        class _FakeScalarResult:
            def __init__(self, values: list[object]):
                self._values = values

            def all(self):
                return list(self._values)

        class _FakeResult:
            def __init__(
                self, *, scalar_value: object | None, scalars_values: list[object]
            ):
                self._scalar_value = scalar_value
                self._scalars_values = scalars_values

            def scalar_one_or_none(self):
                return self._scalar_value

            def scalars(self):
                return _FakeScalarResult(self._scalars_values)

        if isinstance(stmt, Insert):
            # Treat inserts as successful by default.
            return _FakeResult(scalar_value=1, scalars_values=[])

        return _FakeResult(scalar_value=None, scalars_values=[])


@dataclass
class _FakeAssistantRepository:
    listed_messages: list[object] = field(default_factory=list)
    messages: list[dict[str, object]] = field(default_factory=list)
    audits: list[dict[str, object]] = field(default_factory=list)
    action_requests: list[ActionRequest] = field(default_factory=list)
    action_tool_runs: list[ToolRun] = field(default_factory=list)

    async def get_thread(self, *, owner_user_id: UUID, thread_id: UUID):
        return SimpleNamespace(id=thread_id, owner_user_id=owner_user_id)

    async def list_messages(self, *, thread_id: UUID):
        _ = thread_id
        return self.listed_messages

    async def get_pending_action_requests(self, *, thread_id: UUID):
        _ = thread_id
        return [
            request
            for request in self.action_requests
            if request.status == ActionRequestStatus.PENDING
        ]

    async def list_action_requests(self, *, thread_id: UUID):
        _ = thread_id
        return list(self.action_requests)

    async def list_action_tool_runs(self, *, thread_id: UUID):
        _ = thread_id
        return list(self.action_tool_runs)

    async def create_message(
        self, *, thread_id: UUID, role: str, parts: list[dict[str, object]]
    ):
        self.messages.append({"thread_id": thread_id, "role": role, "parts": parts})
        return SimpleNamespace(
            id=uuid4(), thread_id=thread_id, role=role, content=parts
        )

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None:
        self.audits.append(
            {
                "event_type": event_type,
                "actor_email": actor_email,
                "tool_name": tool_name,
                "metadata": metadata,
            }
        )


@dataclass
class _FakeWorkflowTodoService:
    todos: list[dict[str, str]] = field(default_factory=list)

    async def list_workflow(self, *, thread_id: UUID):
        _ = thread_id
        return list(self.todos)


@dataclass
class _FakeApprovedToolExecutor:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def execute(
        self,
        *,
        started_tool_run: ToolRun,
        approved_request: ActionRequest,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        repository: _FakeAssistantRepository,
        action_tool_run_service: ActionToolRunService,
    ) -> None:
        assert started_tool_run.status == ToolRunStatus.STARTED
        assert started_tool_run.thread_id == thread_id
        assert started_tool_run.requested_by_user_id == owner_user_id
        assert started_tool_run.action_request_id == approved_request.id
        _ = owner_user_email, repository, action_tool_run_service
        self.calls.append(("execute", approved_request.tool_name))


async def _async_return(value):
    return value


@dataclass
class _InMemoryActionToolRunRepository:
    action_requests: dict[UUID, ActionRequest]
    tool_runs: dict[UUID, ToolRun]

    async def get_action_request(
        self, *, action_request_id: UUID
    ) -> ActionRequest | None:
        return self.action_requests.get(action_request_id)

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

    async def get_tool_run(self, *, tool_run_id: UUID) -> ToolRun | None:
        return self.tool_runs.get(tool_run_id)

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


def _error_code(exc: HTTPException) -> str | None:
    error_code = getattr(exc, "error_code", None)
    if isinstance(error_code, str):
        return error_code
    headers = exc.headers or {}
    return headers.get("x-error-code") or headers.get("X-Error-Code")


def _assert_assistant_domain_error(
    exc: Exception,
    *,
    status_code: int,
    detail: str,
    error_code: str,
) -> None:
    assert type(exc).__name__ == "AssistantDomainError"
    assert getattr(exc, "status_code") == status_code
    assert getattr(exc, "detail") == detail
    assert getattr(exc, "error_code") == error_code


async def test_record_tool_result_rejects_foreign_thread() -> None:
    from noa_api.api.routes.assistant_tool_result_operations import (
        record_tool_result,
    )

    owner_id = uuid4()
    foreign_thread_id = uuid4()
    actual_thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    started = await repo.start_tool_run(
        thread_id=actual_thread_id,
        tool_name="get_current_time",
        args={},
        action_request_id=None,
        requested_by_user_id=owner_id,
    )
    assistant_repo = _FakeAssistantRepository()

    with pytest.raises(Exception) as exc_info:
        await record_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=foreign_thread_id,
            tool_call_id=str(started.id),
            result={"ok": True},
            repository=assistant_repo,
            action_tool_run_service=ActionToolRunService(repository=repo),
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=404,
        detail="Tool call not found",
        error_code="tool_call_not_found",
    )
    assert repo.tool_runs[started.id].status == ToolRunStatus.STARTED
    assert assistant_repo.messages == []
    assert assistant_repo.audits == []


async def test_assistant_service_load_state_includes_workflow_and_pending_approvals() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    pending_request_id = uuid4()
    denied_request_id = uuid4()
    finished_request_id = uuid4()
    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                id=uuid4(),
                role="assistant",
                content=[{"type": "text", "text": "From DB"}],
            )
        ],
        action_requests=[
            ActionRequest(
                id=pending_request_id,
                thread_id=thread_id,
                tool_name="set_demo_flag",
                args={"key": "feature_x", "value": True},
                risk=ToolRisk.CHANGE,
                status=ActionRequestStatus.PENDING,
                requested_by_user_id=owner_id,
                decided_by_user_id=None,
                decided_at=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            ActionRequest(
                id=denied_request_id,
                thread_id=thread_id,
                tool_name="set_demo_flag",
                args={"key": "feature_y", "value": False},
                risk=ToolRisk.CHANGE,
                status=ActionRequestStatus.DENIED,
                requested_by_user_id=owner_id,
                decided_by_user_id=owner_id,
                decided_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            ActionRequest(
                id=finished_request_id,
                thread_id=thread_id,
                tool_name="set_demo_flag",
                args={"key": "feature_z", "value": True},
                risk=ToolRisk.CHANGE,
                status=ActionRequestStatus.APPROVED,
                requested_by_user_id=owner_id,
                decided_by_user_id=owner_id,
                decided_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ],
        action_tool_runs=[
            ToolRun(
                id=uuid4(),
                thread_id=thread_id,
                tool_name="set_demo_flag",
                args={"key": "feature_z", "value": True},
                status=ToolRunStatus.COMPLETED,
                result={"ok": True},
                error=None,
                action_request_id=finished_request_id,
                requested_by_user_id=owner_id,
                created_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        ],
    )

    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(
            repository=_InMemoryActionToolRunRepository(
                action_requests={}, tool_runs={}
            )
        ),
        workflow_todo_service=_FakeWorkflowTodoService(
            todos=[
                {
                    "content": "Preflight",
                    "status": "completed",
                    "priority": "high",
                },
                {
                    "content": "Request approval",
                    "status": "in_progress",
                    "priority": "high",
                },
            ]
        ),
        session=_FakeSession(),
    )

    state = await service.load_state(owner_user_id=owner_id, thread_id=thread_id)

    assert state["workflow"] == [
        {"content": "Preflight", "status": "completed", "priority": "high"},
        {
            "content": "Request approval",
            "status": "in_progress",
            "priority": "high",
        },
    ]
    assert state["pendingApprovals"] == [
        {
            "actionRequestId": str(pending_request_id),
            "toolName": "set_demo_flag",
            "risk": "CHANGE",
            "arguments": {"key": "feature_x", "value": True},
            "status": "PENDING",
        }
    ]
    assert state["actionRequests"] == [
        {
            "actionRequestId": str(pending_request_id),
            "toolName": "set_demo_flag",
            "risk": "CHANGE",
            "arguments": {"key": "feature_x", "value": True},
            "status": "PENDING",
            "lifecycleStatus": "requested",
        },
        {
            "actionRequestId": str(denied_request_id),
            "toolName": "set_demo_flag",
            "risk": "CHANGE",
            "arguments": {"key": "feature_y", "value": False},
            "status": "DENIED",
            "lifecycleStatus": "denied",
        },
        {
            "actionRequestId": str(finished_request_id),
            "toolName": "set_demo_flag",
            "risk": "CHANGE",
            "arguments": {"key": "feature_z", "value": True},
            "status": "APPROVED",
            "lifecycleStatus": "finished",
        },
    ]


async def test_record_tool_result_rejects_stale_tool_run() -> None:
    from noa_api.api.routes.assistant_tool_result_operations import (
        record_tool_result,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name="get_current_time",
        args={},
        action_request_id=None,
        requested_by_user_id=owner_id,
    )
    started.status = ToolRunStatus.COMPLETED
    assistant_repo = _FakeAssistantRepository()

    with pytest.raises(Exception) as exc_info:
        await record_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=str(started.id),
            result={"ok": True},
            repository=assistant_repo,
            action_tool_run_service=ActionToolRunService(repository=repo),
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=409,
        detail="Tool call is not awaiting result",
        error_code="tool_call_not_awaiting_result",
    )
    assert repo.tool_runs[started.id].status == ToolRunStatus.COMPLETED
    assert assistant_repo.messages == []
    assert assistant_repo.audits == []


async def test_approve_action_starts_tool_run_before_execution() -> None:
    from noa_api.api.routes.assistant_action_operations import (
        approve_action_request,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    assistant_repo = _FakeAssistantRepository()
    operations = _FakeApprovedToolExecutor()

    await approve_action_request(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        execute_tool=operations.execute,
    )

    started = next(iter(repo.tool_runs.values()))
    assert request.status == ActionRequestStatus.APPROVED
    assert started.status == ToolRunStatus.STARTED
    assert assistant_repo.messages[-1] == {
        "thread_id": thread_id,
        "role": "assistant",
        "parts": [
            {
                "type": "tool-call",
                "toolName": "set_demo_flag",
                "toolCallId": str(started.id),
                "args": {"key": "feature_x", "value": True},
            }
        ],
    }
    assert [event["event_type"] for event in assistant_repo.audits] == [
        "action_approved",
        "tool_started",
    ]
    assert operations.calls == [("execute", "set_demo_flag")]


async def test_execute_approved_tool_run_fails_when_tool_definition_missing(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="missing_change",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )
    assistant_repo = _FakeAssistantRepository()

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda _name: None,
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.FAILED
    assert repo.tool_runs[started.id].error == "Requested tool is unavailable"
    assert assistant_repo.messages[-1] == {
        "thread_id": thread_id,
        "role": "tool",
        "parts": [
            {
                "type": "tool-result",
                "toolName": "missing_change",
                "toolCallId": str(started.id),
                "result": {"error": "Requested tool is unavailable"},
                "isError": True,
            }
        ],
    }
    assert assistant_repo.audits[-1] == {
        "event_type": "tool_failed",
        "actor_email": "owner@example.com",
        "tool_name": "missing_change",
        "metadata": {
            "thread_id": str(thread_id),
            "tool_run_id": str(started.id),
            "action_request_id": str(request.id),
            "error": "Requested tool is unavailable",
        },
    }


async def test_execute_approved_tool_run_fails_on_risk_mismatch(monkeypatch) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="read_only_tool",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )
    assistant_repo = _FakeAssistantRepository()
    tool = ToolDefinition(
        name="read_only_tool",
        description="Actually read-only.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=_allow,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.FAILED
    assert repo.tool_runs[started.id].error == "Approved tool risk mismatch"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Approved tool risk mismatch",
        "expectedRisk": "CHANGE",
        "actualRisk": "READ",
    }
    assert assistant_repo.audits[-1] == {
        "event_type": "tool_failed",
        "actor_email": "owner@example.com",
        "tool_name": "read_only_tool",
        "metadata": {
            "thread_id": str(thread_id),
            "tool_run_id": str(started.id),
            "action_request_id": str(request.id),
            "error": "Approved tool risk mismatch",
        },
    }


async def test_execute_approved_tool_run_sanitizes_execution_errors(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="failing_change_direct",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def failing_change(*, session, **kwargs):
        _ = session, kwargs
        raise RuntimeError("boom")

    tool = ToolDefinition(
        name="failing_change_direct",
        description="Always fails.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=failing_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()

    with caplog.at_level("ERROR"):
        await execute_approved_tool_run(
            started_tool_run=started,
            approved_request=request,
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            repository=assistant_repo,
            action_tool_run_service=ActionToolRunService(repository=repo),
            session=_FakeSession(),
        )

    run = next(iter(repo.tool_runs.values()))
    assert run.error == "tool_execution_failed"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Tool execution failed",
        "error_code": "tool_execution_failed",
    }
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_approved_tool_execution_failed"
    )
    assert getattr(record, "error_code") == "tool_execution_failed"
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "tool_name") == "failing_change_direct"
    assert getattr(record, "user_id") == str(owner_id)
    assert "boom" in caplog.text


async def test_execute_approved_tool_run_completes_and_persists_result(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="successful_change",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def successful_change(*, session, **kwargs):
        _ = session, kwargs
        return {"when": datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)}

    tool = ToolDefinition(
        name="successful_change",
        description="Succeeds.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=successful_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.COMPLETED
    assert assistant_repo.messages[-1]["role"] == "tool"
    part = assistant_repo.messages[-1]["parts"][0]
    assert part["type"] == "tool-result"
    assert part["result"]["when"] == "2026-03-13T12:00:00+00:00"
    assert assistant_repo.audits[-1] == {
        "event_type": "tool_completed",
        "actor_email": "owner@example.com",
        "tool_name": "successful_change",
        "metadata": {
            "thread_id": str(thread_id),
            "tool_run_id": str(started.id),
            "action_request_id": str(request.id),
        },
    }


async def test_execute_approved_tool_run_revalidates_required_preflight_before_execution(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={"server_ref": "web1", "username": "alice"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def should_not_run(*, session, **kwargs):
        _ = session, kwargs
        raise AssertionError("tool should not execute without matching preflight")

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
            },
            "required": ["server_ref", "username"],
            "additionalProperties": False,
        },
        execute=should_not_run,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.FAILED
    assert repo.tool_runs[started.id].error == "preflight_required"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Required WHM preflight evidence is missing",
        "error_code": "preflight_required",
        "details": [
            "Run whm_preflight_account with the same server_ref and username before requesting this change."
        ],
    }
    assert assistant_repo.audits[-1] == {
        "event_type": "tool_failed",
        "actor_email": "owner@example.com",
        "tool_name": "whm_suspend_account",
        "metadata": {
            "thread_id": str(thread_id),
            "tool_run_id": str(started.id),
            "action_request_id": str(request.id),
            "error": "Required WHM preflight evidence is missing",
            "error_code": "preflight_required",
        },
    }


async def test_execute_approved_tool_run_allows_execution_with_matching_preflight(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={"server_ref": "web1", "username": "alice"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def successful_change(*, session, **kwargs):
        _ = session, kwargs
        return {"ok": True}

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
            },
            "required": ["server_ref", "username"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        execute=successful_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Suspend alice on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {"ok": True, "account": {"user": "alice"}},
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.COMPLETED
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {"ok": True}


async def test_execute_approved_tool_run_allows_execution_after_reason_follow_up(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={
            "server_ref": "web1",
            "username": "alice",
            "reason": "requested by customer",
        },
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def successful_change(*, session, **kwargs):
        _ = session, kwargs
        return {"ok": True}

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        execute=successful_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Suspend alice on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {"ok": True, "account": {"user": "alice"}},
                        "isError": False,
                    }
                ],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "text",
                        "text": (
                            "To proceed with suspending the account, I need a brief "
                            "human-readable reason for the change. Could you provide the reason?"
                        ),
                    }
                ],
            ),
            SimpleNamespace(
                role="user",
                content=[
                    {"type": "text", "text": "Requested by customer in ticket #121233."}
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.COMPLETED
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {"ok": True}


async def test_execute_approved_tool_run_rejects_mismatched_account_preflight_result(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={"server_ref": "web1", "username": "alice"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def should_not_run(*, session, **kwargs):
        _ = session, kwargs
        raise AssertionError("tool should not execute with mismatched preflight result")

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
            },
            "required": ["server_ref", "username"],
            "additionalProperties": False,
        },
        execute=should_not_run,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Suspend alice on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {"ok": True, "account": {"user": "bob"}},
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.FAILED
    assert repo.tool_runs[started.id].error == "preflight_mismatch"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Required WHM preflight evidence does not match this change request",
        "error_code": "preflight_mismatch",
        "details": [
            "No successful whm_preflight_account was found for server_ref 'web1' and username 'alice' in the current turn."
        ],
    }


async def test_execute_approved_tool_run_rejects_mismatched_firewall_preflight_result(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_firewall_unblock",
        args={"server_ref": "web1", "targets": ["1.2.3.4"]},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def should_not_run(*, session, **kwargs):
        _ = session, kwargs
        raise AssertionError("tool should not execute with mismatched preflight result")

    tool = ToolDefinition(
        name="whm_firewall_unblock",
        description="Unblocks firewall targets.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
            "required": ["server_ref", "targets"],
            "additionalProperties": False,
        },
        execute=should_not_run,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Unblock 1.2.3.4 on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_firewall_entries",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "target": "1.2.3.4"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_firewall_entries",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "target": "9.9.9.9",
                            "combined_verdict": "blocked",
                            "matches": ["/etc/csf/csf.deny"],
                            "available_tools": {"csf": True, "imunify": False},
                        },
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.FAILED
    assert repo.tool_runs[started.id].error == "preflight_mismatch"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Required firewall preflight evidence does not match this change request",
        "error_code": "preflight_mismatch",
        "details": [
            "Missing successful whm_preflight_firewall_entries results for target(s): '1.2.3.4'"
        ],
    }


async def test_execute_approved_tool_run_allows_matching_server_id_preflight(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={"server_ref": "web1", "username": "alice"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def successful_change(*, session, **kwargs):
        _ = session, kwargs
        return {"ok": True}

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
            },
            "required": ["server_ref", "username"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        execute=successful_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations._resolve_requested_server_id",
        lambda **kwargs: _async_return("server-1"),
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Suspend alice on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "whm.example.com", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "server_id": "server-1",
                            "account": {"user": "alice"},
                        },
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.COMPLETED


async def test_execute_approved_tool_run_persists_completed_whm_workflow_with_evidence(
    monkeypatch,
) -> None:
    from noa_api.api.assistant.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={
            "server_ref": "web1",
            "username": "alice",
            "reason": "billing hold",
        },
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def successful_change(*, session, **kwargs):
        _ = session, kwargs
        return {"ok": True, "status": "changed", "message": "Account suspended"}

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "status": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["ok", "status", "message"],
            "additionalProperties": False,
        },
        execute=successful_change,
        workflow_family="whm-account-lifecycle",
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    async def _postflight(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "account": {
                "user": "alice",
                "suspended": True,
                "domain": "example.com",
            },
        }

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )
    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.fetch_postflight_result",
        _postflight,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Suspend alice on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "account": {
                                "user": "alice",
                                "suspended": False,
                                "domain": "example.com",
                            },
                        },
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.COMPLETED
    assert captured["thread_id"] == thread_id
    todos = cast(list[dict[str, str]], captured["todos"])
    assert len(todos) == 5
    assert todos[2]["status"] == "completed"
    assert todos[3]["status"] == "completed"
    assert todos[4]["status"] == "completed"
    assert "expected suspended" in todos[4]["content"]

    todo_tool_call = next(
        part
        for message in assistant_repo.messages
        if message.get("role") == "assistant"
        for part in message.get("parts", [])
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "update_workflow_todo"
    )
    assert isinstance(todo_tool_call.get("args"), dict)


async def test_execute_approved_tool_run_persists_failed_whm_workflow_when_execution_errors(
    monkeypatch,
) -> None:
    from noa_api.api.assistant.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={
            "server_ref": "web1",
            "username": "alice",
            "reason": "billing hold",
        },
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def failing_change(*, session, **kwargs):
        _ = session, kwargs
        raise RuntimeError("backend exploded")

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "status": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["ok", "status", "message"],
            "additionalProperties": False,
        },
        execute=failing_change,
        workflow_family="whm-account-lifecycle",
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Suspend alice on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "account": {
                                "user": "alice",
                                "suspended": False,
                                "domain": "example.com",
                            },
                        },
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.FAILED
    assert repo.tool_runs[started.id].error == "tool_execution_failed"
    assert captured["thread_id"] == thread_id
    todos = cast(list[dict[str, str]], captured["todos"])
    assert [todo["status"] for todo in todos] == [
        "completed",
        "completed",
        "completed",
        "cancelled",
        "cancelled",
    ]

    tool_message = next(
        message
        for message in reversed(assistant_repo.messages)
        if message.get("role") == "tool"
        and isinstance(message.get("parts"), list)
        and isinstance(message["parts"][0], dict)
        and message["parts"][0].get("type") == "tool-result"
        and message["parts"][0].get("toolName") == "whm_suspend_account"
    )
    part = tool_message["parts"][0]
    assert part["isError"] is True
    assert part["result"]["error_code"] == "tool_execution_failed"

    receipt_message = next(
        message
        for message in reversed(assistant_repo.messages)
        if message.get("role") == "tool"
        and isinstance(message.get("parts"), list)
        and isinstance(message["parts"][0], dict)
        and message["parts"][0].get("type") == "tool-result"
        and message["parts"][0].get("toolName") == "workflow_receipt"
    )
    assert receipt_message["role"] == "tool"

    todo_tool_call = next(
        part
        for message in assistant_repo.messages
        if message.get("role") == "assistant"
        for part in message.get("parts", [])
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "update_workflow_todo"
    )
    assert isinstance(todo_tool_call.get("args"), dict)


async def test_execute_approved_tool_run_rejects_mismatched_server_id_preflight(
    monkeypatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_suspend_account",
        args={"server_ref": "web1", "username": "alice"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def should_not_run(*, session, **kwargs):
        _ = session, kwargs
        raise AssertionError("tool should not execute with mismatched server id")

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Suspends an account.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
            },
            "required": ["server_ref", "username"],
            "additionalProperties": False,
        },
        execute=should_not_run,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations._resolve_requested_server_id",
        lambda **kwargs: _async_return("server-1"),
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[{"type": "text", "text": "Suspend alice on web1."}],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "server_id": "server-2",
                            "account": {"user": "alice"},
                        },
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.FAILED
    assert repo.tool_runs[started.id].error == "preflight_mismatch"


async def test_deny_action_request_writes_message_and_audit_metadata() -> None:
    from noa_api.api.routes.assistant_action_operations import deny_action_request

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    assistant_repo = _FakeAssistantRepository()

    await deny_action_request(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    assert request.status == ActionRequestStatus.DENIED
    assert assistant_repo.messages[-1] == {
        "thread_id": thread_id,
        "role": "assistant",
        "parts": [
            {
                "type": "text",
                "text": "Denied action request for tool 'set_demo_flag'.",
            }
        ],
    }
    assert assistant_repo.audits[-1] == {
        "event_type": "action_denied",
        "actor_email": "owner@example.com",
        "tool_name": "set_demo_flag",
        "metadata": {
            "thread_id": str(thread_id),
            "action_request_id": str(request.id),
        },
    }


async def test_assistant_service_approve_executes_pending_change_and_writes_audit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def set_demo_flag(*, session, key: str, value: bool, **kwargs):
        _ = session, key, value, kwargs
        return {"ok": True}

    tool = ToolDefinition(
        name="set_demo_flag",
        description="Sets a demo flag.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 1},
                "value": {"type": "boolean"},
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        execute=set_demo_flag,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with caplog.at_level("INFO"):
        await service.approve_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=str(request.id),
            is_user_active=True,
            authorize_tool_access=lambda _tool: _allow(),
        )

    assert request.status == ActionRequestStatus.APPROVED
    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.COMPLETED
    assert assistant_repo.messages[-1]["role"] == "tool"
    assert [event["event_type"] for event in assistant_repo.audits] == [
        "action_approved",
        "tool_started",
        "tool_completed",
    ]
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_action_approved"
    )
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "user_id") == str(owner_id)
    assert getattr(record, "tool_name") == "set_demo_flag"
    assert getattr(record, "action_request_id") == str(request.id)
    assert getattr(record, "tool_run_id") == str(run.id)


async def test_assistant_service_approve_change_tool_failure_is_persisted_and_does_not_raise(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="failing_change",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def failing_change(*, session, **kwargs):
        _ = session, kwargs
        raise RuntimeError("boom")

    tool = ToolDefinition(
        name="failing_change",
        description="Always fails.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=failing_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.approve_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
    )

    assert request.status == ActionRequestStatus.APPROVED
    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "tool_execution_failed"
    assert "boom" not in str(run.error)

    tool_message = assistant_repo.messages[-1]
    assert tool_message["role"] == "tool"
    part = tool_message["parts"][0]
    assert part["type"] == "tool-result"
    assert part["isError"] is True
    assert part["result"] == {
        "error": "Tool execution failed",
        "error_code": "tool_execution_failed",
    }

    assert [event["event_type"] for event in assistant_repo.audits] == [
        "action_approved",
        "tool_started",
        "tool_failed",
    ]


async def test_assistant_service_approve_change_tool_timeout_is_sanitized(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="timeout_change",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def timeout_change(*, session, **kwargs):
        _ = session, kwargs
        raise asyncio.TimeoutError("slow backend")

    tool = ToolDefinition(
        name="timeout_change",
        description="Always times out.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=timeout_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.approve_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "timeout"
    assert "slow backend" not in str(run.error)

    tool_message = assistant_repo.messages[-1]
    part = tool_message["parts"][0]
    assert part["type"] == "tool-result"
    assert part["isError"] is True
    assert part["result"] == {
        "error": "Tool timed out",
        "error_code": "timeout",
    }


async def test_assistant_service_approve_change_tool_invalid_args_is_persisted(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="invalid_change_args",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def guarded_change(*, session, reason: str, **kwargs):
        _ = session, reason, kwargs
        raise AssertionError("tool should not execute when args are invalid")

    tool = ToolDefinition(
        name="invalid_change_args",
        description="Requires a reason argument.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.approve_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "invalid_tool_arguments"

    tool_message = assistant_repo.messages[-1]
    part = tool_message["parts"][0]
    assert part["type"] == "tool-result"
    assert part["isError"] is True
    assert part["result"] == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["Missing required field 'reason'"],
    }

    assert [event["event_type"] for event in assistant_repo.audits] == [
        "action_approved",
        "tool_started",
        "tool_failed",
    ]


async def test_assistant_service_approve_change_tool_blank_string_args_are_rejected(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="invalid_blank_change_args",
        args={"reason": "   "},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def guarded_change(*, session, reason: str, **kwargs):
        _ = session, reason, kwargs
        raise AssertionError("tool should not execute when args are blank")

    tool = ToolDefinition(
        name="invalid_blank_change_args",
        description="Requires a non-blank reason argument.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.approve_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "invalid_tool_arguments"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["reason must not be blank"],
    }


async def test_assistant_service_approve_change_tool_invalid_result_is_persisted(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="invalid_result_change",
        args={"reason": "customer request"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def bad_result_change(*, session, reason: str, **kwargs):
        _ = session, reason, kwargs
        return {"ok": True}

    tool = ToolDefinition(
        name="invalid_result_change",
        description="Returns an invalid change result.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        execute=bad_result_change,
        result_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean", "enum": [True]},
                "status": {"type": "string", "enum": ["changed", "no-op"]},
                "message": {"type": "string"},
            },
            "required": ["ok", "status", "message"],
        },
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.approve_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "invalid_tool_result"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Tool returned an invalid result",
        "error_code": "invalid_tool_result",
        "details": [
            "Missing required field 'status'",
            "Missing required field 'message'",
        ],
    }


async def test_assistant_service_approve_change_tool_logs_original_exception(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="failing_change_logged",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def failing_change(*, session, **kwargs):
        _ = session, kwargs
        raise RuntimeError("boom")

    tool = ToolDefinition(
        name="failing_change_logged",
        description="Always fails.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=failing_change,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with caplog.at_level("ERROR"):
        await service.approve_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=str(request.id),
            is_user_active=True,
            authorize_tool_access=lambda _tool: _allow(),
        )

    run = next(iter(repo.tool_runs.values()))
    assert run.error == "tool_execution_failed"
    assert assistant_repo.messages[-1]["parts"][0]["result"] == {
        "error": "Tool execution failed",
        "error_code": "tool_execution_failed",
    }
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_approved_tool_execution_failed"
    )
    assert getattr(record, "error_code") == "tool_execution_failed"
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "tool_name") == "failing_change_logged"
    assert getattr(record, "user_id") == str(owner_id)
    assert "boom" in caplog.text


async def test_assistant_service_deny_marks_denied_with_audit_and_message() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.deny_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
    )

    assert request.status == ActionRequestStatus.DENIED
    assert repo.tool_runs == {}
    assert assistant_repo.messages[-1]["parts"][0]["type"] == "text"
    assert assistant_repo.audits[-1]["event_type"] == "action_denied"


async def test_assistant_service_add_tool_result_logs_success_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name="get_current_time",
        args={},
        action_request_id=None,
        requested_by_user_id=owner_id,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with caplog.at_level("INFO"):
        await service.add_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=str(started.id),
            result={"ok": True},
        )

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_tool_result_recorded"
    )
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "user_id") == str(owner_id)
    assert getattr(record, "tool_name") == "get_current_time"
    assert getattr(record, "tool_run_id") == str(started.id)


async def test_assistant_service_deny_logs_success_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with caplog.at_level("INFO"):
        await service.deny_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=str(request.id),
        )

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_action_denied"
    )
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "user_id") == str(owner_id)
    assert getattr(record, "tool_name") == "set_demo_flag"
    assert getattr(record, "action_request_id") == str(request.id)


async def test_assistant_service_rejects_approval_replay() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED

    service = AssistantService(
        _FakeAssistantRepository(),
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with pytest.raises(Exception) as exc_info:
        await service.approve_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=str(request.id),
            is_user_active=True,
            authorize_tool_access=lambda _tool: _allow(),
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=409,
        detail="Action request already decided",
        error_code="action_request_already_decided",
    )


async def test_assistant_service_rejects_deny_replay() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.DENIED

    service = AssistantService(
        _FakeAssistantRepository(),
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with pytest.raises(Exception) as exc_info:
        await service.deny_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=str(request.id),
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=409,
        detail="Action request already decided",
        error_code="action_request_already_decided",
    )


async def test_assistant_service_add_tool_result_rejects_unknown_or_stale_ids() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name="get_current_time",
        args={},
        action_request_id=None,
        requested_by_user_id=owner_id,
    )
    stale = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name="get_current_time",
        args={},
        action_request_id=None,
        requested_by_user_id=owner_id,
    )
    stale.status = ToolRunStatus.COMPLETED

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with pytest.raises(Exception) as unknown_exc_info:
        await service.add_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=str(uuid4()),
            result={"ok": True},
        )

    _assert_assistant_domain_error(
        unknown_exc_info.value,
        status_code=400,
        detail="Unknown tool call id",
        error_code="unknown_tool_call_id",
    )

    with pytest.raises(Exception) as stale_exc_info:
        await service.add_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=str(stale.id),
            result={"ok": True},
        )

    _assert_assistant_domain_error(
        stale_exc_info.value,
        status_code=409,
        detail="Tool call is not awaiting result",
        error_code="tool_call_not_awaiting_result",
    )

    await service.add_tool_result(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        tool_call_id=str(started.id),
        result={"ok": True},
    )

    assert repo.tool_runs[started.id].status == ToolRunStatus.COMPLETED
    assert assistant_repo.messages[-1]["role"] == "tool"


async def test_assistant_service_add_tool_result_rejects_invalid_result_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name="set_demo_flag",
        args={"key": "feature_x", "value": True},
        action_request_id=None,
        requested_by_user_id=owner_id,
    )

    async def set_demo_flag(*, session, key: str, value: bool, **kwargs):
        _ = session, key, value, kwargs
        return {"ok": True, "status": "changed", "message": "Flag updated"}

    tool = ToolDefinition(
        name="set_demo_flag",
        description="Sets a demo flag.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 1},
                "value": {"type": "boolean"},
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "status": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["ok", "status", "message"],
            "additionalProperties": False,
        },
        execute=set_demo_flag,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    with pytest.raises(Exception) as exc_info:
        await service.add_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=str(started.id),
            result={"ok": True},
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=400,
        detail="Invalid tool result payload",
        error_code="invalid_tool_result",
    )
    assert repo.tool_runs[started.id].status == ToolRunStatus.STARTED
    assert assistant_repo.messages == []
    assert assistant_repo.audits == []


@pytest.mark.parametrize(
    ("tool_call_id", "detail", "error_code"),
    [
        (None, "Missing toolCallId", "missing_tool_call_id"),
        ("not-a-uuid", "Invalid toolCallId", "invalid_tool_call_id"),
    ],
)
async def test_assistant_service_add_tool_result_rejects_missing_or_invalid_tool_call_id(
    tool_call_id: str | None,
    detail: str,
    error_code: str,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(
            repository=_InMemoryActionToolRunRepository(
                action_requests={}, tool_runs={}
            )
        ),
        session=_FakeSession(),
    )

    with pytest.raises(Exception) as exc_info:
        await service.add_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            result={"ok": True},
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=400,
        detail=detail,
        error_code=error_code,
    )


@pytest.mark.parametrize(
    ("action_request_id", "detail", "error_code"),
    [
        (None, "Missing actionRequestId", "missing_action_request_id"),
        ("not-a-uuid", "Invalid actionRequestId", "invalid_action_request_id"),
    ],
)
async def test_assistant_service_approve_action_rejects_missing_or_invalid_action_request_id(
    action_request_id: str | None,
    detail: str,
    error_code: str,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(
            repository=_InMemoryActionToolRunRepository(
                action_requests={}, tool_runs={}
            )
        ),
        session=_FakeSession(),
    )

    with pytest.raises(Exception) as exc_info:
        await service.approve_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=action_request_id,
            is_user_active=True,
            authorize_tool_access=lambda _tool: _allow(),
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=400,
        detail=detail,
        error_code=error_code,
    )


@pytest.mark.parametrize(
    ("action_request_id", "detail", "error_code"),
    [
        (None, "Missing actionRequestId", "missing_action_request_id"),
        ("not-a-uuid", "Invalid actionRequestId", "invalid_action_request_id"),
    ],
)
async def test_assistant_service_deny_action_rejects_missing_or_invalid_action_request_id(
    action_request_id: str | None,
    detail: str,
    error_code: str,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(
            repository=_InMemoryActionToolRunRepository(
                action_requests={}, tool_runs={}
            )
        ),
        session=_FakeSession(),
    )

    with pytest.raises(Exception) as exc_info:
        await service.deny_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=action_request_id,
        )

    _assert_assistant_domain_error(
        exc_info.value,
        status_code=400,
        detail=detail,
        error_code=error_code,
    )


async def test_assistant_service_run_agent_turn_emits_action_requested_audit() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _ProposalRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.run_agent_turn(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        available_tool_names={"set_demo_flag"},
    )

    assert any(
        event["event_type"] == "action_requested" for event in assistant_repo.audits
    )


async def test_assistant_service_sanitizes_tool_result_messages_for_change_tools(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="json_unsafe_change",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def change_tool(*, session, **kwargs):
        _ = session, kwargs
        return {"when": datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)}

    tool = ToolDefinition(
        name="json_unsafe_change",
        description="Returns non-JSON-native values.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=change_tool,
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.approve_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
    )

    tool_message = assistant_repo.messages[-1]
    assert tool_message["role"] == "tool"
    part = tool_message["parts"][0]
    assert part["type"] == "tool-result"
    assert part["result"]["when"] == "2026-03-13T12:00:00+00:00"


async def test_deny_action_request_persists_denied_whm_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noa_api.api.routes.assistant_action_operations import deny_action_request

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_change_contact_email",
        args={
            "server_ref": "web1",
            "username": "alice",
            "new_email": "new@example.com",
            "reason": "customer request",
        },
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[
                    {"type": "text", "text": "Change alice contact email on web1."}
                ],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "account": {
                                "user": "alice",
                                "contactemail": "old@example.com",
                            },
                        },
                        "isError": False,
                    }
                ],
            ),
        ]
    )
    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    await deny_action_request(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    todos = cast(list[dict[str, str]], captured["todos"])
    assert captured["thread_id"] == thread_id
    assert len(todos) == 5
    assert [todo["status"] for todo in todos] == [
        "completed",
        "completed",
        "cancelled",
        "cancelled",
        "cancelled",
    ]
    denied_message = next(
        message
        for message in reversed(assistant_repo.messages)
        if message.get("role") == "assistant"
        and any(
            isinstance(part, dict)
            and part.get("type") == "text"
            and "Denied. Receipt below." in cast(str, part.get("text"))
            for part in cast(list[object], message.get("parts", []))
        )
    )
    assert denied_message["role"] == "assistant"

    receipt_message = next(
        message
        for message in reversed(assistant_repo.messages)
        if message.get("role") == "tool"
        and isinstance(message.get("parts"), list)
        and isinstance(message["parts"][0], dict)
        and message["parts"][0].get("type") == "tool-result"
        and message["parts"][0].get("toolName") == "workflow_receipt"
    )
    assert receipt_message["role"] == "tool"


async def test_execute_approved_tool_run_persists_completed_contact_email_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noa_api.api.assistant.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_change_contact_email",
        args={
            "server_ref": "web1",
            "username": "alice",
            "new_email": "new@example.com",
            "reason": "customer request",
        },
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def successful_change(*, session, **kwargs):
        _ = session, kwargs
        return {"ok": True, "status": "changed", "message": "Contact email updated"}

    tool = ToolDefinition(
        name="whm_change_contact_email",
        description="Updates contact email.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "username": {"type": "string"},
                "new_email": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["server_ref", "username", "new_email", "reason"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "status": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["ok", "status", "message"],
            "additionalProperties": False,
        },
        execute=successful_change,
        workflow_family="whm-account-contact-email",
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    async def _postflight(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "account": {"user": "alice", "contactemail": "new@example.com"},
        }

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )
    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.fetch_postflight_result",
        _postflight,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[
                    {"type": "text", "text": "Change alice contact email on web1."}
                ],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "account": {
                                "user": "alice",
                                "contactemail": "old@example.com",
                            },
                        },
                        "isError": False,
                    }
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    todos = cast(list[dict[str, str]], captured["todos"])
    assert captured["thread_id"] == thread_id
    assert todos[4]["status"] == "completed"
    assert "expected contact email 'new@example.com'" in todos[4]["content"]
    assert len(todos) == 5

    tool_message = next(
        message
        for message in reversed(assistant_repo.messages)
        if message.get("role") == "tool"
        and isinstance(message.get("parts"), list)
        and isinstance(message["parts"][0], dict)
        and message["parts"][0].get("type") == "tool-result"
        and message["parts"][0].get("toolName") == "whm_change_contact_email"
    )
    assert tool_message["role"] == "tool"

    assistant_text_messages = [
        part.get("text")
        for message in assistant_repo.messages
        if message.get("role") == "assistant"
        for part in cast(list[dict[str, object]], message.get("parts", []))
        if part.get("type") == "text"
    ]
    assert not any(
        isinstance(text, str) and "Contact email change completed" in text
        for text in assistant_text_messages
    )


async def test_execute_approved_tool_run_persists_completed_firewall_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noa_api.api.assistant.assistant_action_operations import (
        execute_approved_tool_run,
    )

    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="whm_firewall_unblock",
        args={
            "server_ref": "web1",
            "targets": ["1.2.3.4", "5.6.7.8"],
            "reason": "customer request",
        },
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )
    request.status = ActionRequestStatus.APPROVED
    started = await repo.start_tool_run(
        thread_id=thread_id,
        tool_name=request.tool_name,
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=owner_id,
    )

    async def successful_change(*, session, **kwargs):
        _ = session, kwargs
        return {
            "ok": True,
            "results": [
                {
                    "target": "1.2.3.4",
                    "ok": True,
                    "status": "changed",
                    "verdict": "clear",
                    "matches": [],
                },
                {
                    "target": "5.6.7.8",
                    "ok": True,
                    "status": "no-op",
                    "verdict": "clear",
                    "matches": [],
                },
            ],
        }

    tool = ToolDefinition(
        name="whm_firewall_unblock",
        description="Unblocks firewall targets.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string"},
                "targets": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["server_ref", "targets", "reason"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}, "results": {"type": "array"}},
            "required": ["ok", "results"],
            "additionalProperties": False,
        },
        execute=successful_change,
        workflow_family="whm-firewall-batch-change",
    )

    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    async def _postflight(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "results": [
                {
                    "ok": True,
                    "target": "1.2.3.4",
                    "combined_verdict": "not_found",
                    "matches": [],
                    "available_tools": {"csf": True, "imunify": True},
                    "csf": {
                        "verdict": "not_found",
                        "raw_output": "ip6tables:\n\nNo matches found for 1.2.3.4 in ip6tables",
                    },
                    "imunify": {"verdict": "not_found", "entries": []},
                },
                {
                    "ok": True,
                    "target": "5.6.7.8",
                    "combined_verdict": "not_found",
                    "matches": [],
                    "available_tools": {"csf": True, "imunify": False},
                    "csf": {
                        "verdict": "not_found",
                        "raw_output": "ip6tables:\n\nNo matches found for 5.6.7.8 in ip6tables",
                    },
                },
            ],
        }

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )
    monkeypatch.setattr(
        "noa_api.api.assistant.assistant_action_operations.fetch_postflight_result",
        _postflight,
    )

    assistant_repo = _FakeAssistantRepository(
        listed_messages=[
            SimpleNamespace(
                role="user",
                content=[
                    {"type": "text", "text": "Unblock 1.2.3.4 and 5.6.7.8 on web1."}
                ],
            ),
            SimpleNamespace(
                role="assistant",
                content=[
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_firewall_entries",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "target": "1.2.3.4"},
                    },
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_firewall_entries",
                        "toolCallId": "preflight-2",
                        "args": {"server_ref": "web1", "target": "5.6.7.8"},
                    },
                ],
            ),
            SimpleNamespace(
                role="tool",
                content=[
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_firewall_entries",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "target": "1.2.3.4",
                            "combined_verdict": "blocked",
                            "matches": ["deny"],
                            "available_tools": {"csf": True, "imunify": True},
                            "csf": {
                                "verdict": "blocked",
                                "raw_output": (
                                    "filter DENYIN\n\n"
                                    "Temporary Blocks: IP:1.2.3.4 Port: Dir:in TTL:432000"
                                    " (osTicket #121312)"
                                ),
                            },
                            "imunify": {
                                "verdict": "blacklisted",
                                "entries": [{"ip": "1.2.3.4", "purpose": "drop"}],
                            },
                        },
                        "isError": False,
                    },
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_firewall_entries",
                        "toolCallId": "preflight-2",
                        "result": {
                            "ok": True,
                            "target": "5.6.7.8",
                            "combined_verdict": "not_found",
                            "matches": [],
                            "available_tools": {"csf": True, "imunify": False},
                            "csf": {
                                "verdict": "not_found",
                                "raw_output": "ip6tables:\n\nNo matches found for 5.6.7.8 in ip6tables",
                            },
                        },
                        "isError": False,
                    },
                ],
            ),
        ]
    )

    await execute_approved_tool_run(
        started_tool_run=started,
        approved_request=request,
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        repository=assistant_repo,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    tool_message = next(
        message
        for message in reversed(assistant_repo.messages)
        if message.get("role") == "tool"
        and isinstance(message.get("parts"), list)
        and isinstance(message["parts"][0], dict)
        and message["parts"][0].get("type") == "tool-result"
        and message["parts"][0].get("toolName") == "whm_firewall_unblock"
    )
    assert tool_message["role"] == "tool"

    receipt_message = next(
        message
        for message in reversed(assistant_repo.messages)
        if message.get("role") == "tool"
        and isinstance(message.get("parts"), list)
        and isinstance(message["parts"][0], dict)
        and message["parts"][0].get("type") == "tool-result"
        and message["parts"][0].get("toolName") == "workflow_receipt"
    )
    receipt_part = cast(dict[str, object], receipt_message["parts"][0])
    receipt_result = cast(dict[str, object], receipt_part["result"])
    evidence_sections = cast(
        list[dict[str, object]], receipt_result["evidenceSections"]
    )
    before_state = next(
        section for section in evidence_sections if section.get("key") == "before_state"
    )
    after_state = next(
        section for section in evidence_sections if section.get("key") == "after_state"
    )
    before_items = cast(list[dict[str, str]], before_state["items"])
    after_items = cast(list[dict[str, str]], after_state["items"])
    assert any(
        item.get("label") == "1.2.3.4 · CSF"
        and item.get("value")
        == "Temporary Blocks: IP:1.2.3.4 Port: Dir:in TTL:432000 (osTicket #121312)"
        for item in before_items
    )
    assert any(
        item.get("label") == "1.2.3.4 · CSF"
        and item.get("value") == "No matches found for 1.2.3.4 in ip6tables"
        for item in after_items
    )

    todos = cast(list[dict[str, str]], captured["todos"])
    assert captured["thread_id"] == thread_id
    assert todos[4]["status"] == "completed"
    assert "1.2.3.4: expected not blocked, observed not_found" in todos[4]["content"]
    assert "5.6.7.8: expected not blocked, observed not_found" in todos[4]["content"]
    assert len(todos) == 5

    assistant_text_messages = [
        part.get("text")
        for message in assistant_repo.messages
        if message.get("role") == "assistant"
        for part in cast(list[dict[str, object]], message.get("parts", []))
        if part.get("type") == "text"
    ]
    assert not any(
        isinstance(text, str) and "Firewall change partially completed" in text
        for text in assistant_text_messages
    )


async def _allow() -> bool:
    return True
