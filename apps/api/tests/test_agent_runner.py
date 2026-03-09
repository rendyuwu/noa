from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from noa_api.core.agent.runner import AgentRunner, AgentRunnerResult, LLMToolCall, LLMTurnResponse
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from noa_api.storage.postgres.models import ActionRequest, ToolRun


@dataclass
class _FakeLLMClient:
    response: LLMTurnResponse

    async def run_turn(self, *, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> LLMTurnResponse:
        _ = messages, tools
        return self.response


@dataclass
class _InMemoryActionToolRunRepository:
    action_requests: dict[UUID, ActionRequest]
    tool_runs: dict[UUID, ToolRun]

    async def get_action_request(self, *, action_request_id: UUID) -> ActionRequest | None:
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
        _ = decided_by_user_id, status
        return self.action_requests.get(action_request_id)

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


async def test_agent_runner_executes_read_tools_and_appends_result_messages() -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    runner = AgentRunner(
        llm_client=_FakeLLMClient(
            response=LLMTurnResponse(
                text="I'll check the server time.",
                tool_calls=[LLMToolCall(name="get_current_time", arguments={})],
            )
        ),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result: AgentRunnerResult = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "What time is it?"}]}],
        available_tool_names={"get_current_time"},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert len(result.messages) == 3
    assert result.messages[0].role == "assistant"
    assert result.messages[0].parts[0]["type"] == "text"
    assert result.messages[1].parts[0]["type"] == "tool-call"
    assert result.messages[2].parts[0]["type"] == "tool-result"

    assert len(repo.tool_runs) == 1
    run = next(iter(repo.tool_runs.values()))
    assert run.tool_name == "get_current_time"
    assert run.status == ToolRunStatus.COMPLETED
    assert run.result is not None
    assert "time" in run.result


async def test_agent_runner_creates_action_request_for_change_tools_without_execution() -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    runner = AgentRunner(
        llm_client=_FakeLLMClient(
            response=LLMTurnResponse(
                text="I can set that flag after your approval.",
                tool_calls=[
                    LLMToolCall(
                        name="set_demo_flag",
                        arguments={"key": "feature_x", "value": True},
                    )
                ],
            )
        ),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "Set demo flag"}]}],
        available_tool_names={"set_demo_flag"},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert len(repo.action_requests) == 1
    request = next(iter(repo.action_requests.values()))
    assert request.tool_name == "set_demo_flag"
    assert request.status == ActionRequestStatus.PENDING

    assert repo.tool_runs == {}
    assert len(result.messages) == 3
    approval_part = result.messages[2].parts[0]
    assert approval_part["type"] == "tool-call"
    assert approval_part["toolName"] == "request_approval"
    assert approval_part["args"]["actionRequestId"] == str(request.id)
