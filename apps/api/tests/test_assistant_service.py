from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
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

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        return None


@dataclass
class _FakeAssistantRepository:
    messages: list[dict[str, object]] = field(default_factory=list)
    audits: list[dict[str, object]] = field(default_factory=list)

    async def get_thread(self, *, owner_user_id: UUID, thread_id: UUID):
        return SimpleNamespace(id=thread_id, owner_user_id=owner_user_id)

    async def list_messages(self, *, thread_id: UUID):
        _ = thread_id
        return []

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


async def test_assistant_service_approve_executes_pending_change_and_writes_audit() -> (
    None
):
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
        "noa_api.api.routes.assistant.get_tool_definition",
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
        "noa_api.api.routes.assistant.get_tool_definition",
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
        "noa_api.api.routes.assistant.get_tool_definition",
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
    assert "Approved tool execution failed" in caplog.text
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

    with pytest.raises(HTTPException, match="already decided"):
        await service.approve_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=str(request.id),
            is_user_active=True,
            authorize_tool_access=lambda _tool: _allow(),
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

    with pytest.raises(HTTPException, match="already decided"):
        await service.deny_action(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            action_request_id=str(request.id),
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

    with pytest.raises(HTTPException, match="Unknown tool call id"):
        await service.add_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=str(uuid4()),
            result={"ok": True},
        )

    with pytest.raises(HTTPException, match="not awaiting result"):
        await service.add_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=thread_id,
            tool_call_id=str(stale.id),
            result={"ok": True},
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
        "noa_api.api.routes.assistant.get_tool_definition",
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


async def _allow() -> bool:
    return True
