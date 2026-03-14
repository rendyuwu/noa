from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

import pytest

from noa_api.core.agent.runner import AgentRunner, LLMToolCall, LLMTurnResponse
from noa_api.core.tools.registry import ToolDefinition
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ToolRisk, ToolRunStatus
from noa_api.storage.postgres.models import ToolRun


@dataclass
class _InMemoryToolRunRepository:
    tool_runs: dict[UUID, ToolRun]

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


class _TwoTurnLLM:
    def __init__(self, *, tool_name: str) -> None:
        self._tool_name = tool_name
        self._turn = 0

    async def run_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta=None,
    ) -> LLMTurnResponse:
        _ = messages, tools, on_text_delta
        self._turn += 1
        if self._turn == 1:
            return LLMTurnResponse(
                text="",
                tool_calls=[LLMToolCall(name=self._tool_name, arguments={})],
            )
        return LLMTurnResponse(text="done", tool_calls=[])


async def _run_failed_tool_call(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    exc_factory: Callable[[], Exception],
) -> tuple[ToolRun, dict[str, object]]:
    repo = _InMemoryToolRunRepository(tool_runs={})

    async def failing_tool() -> dict[str, object]:
        raise exc_factory()

    tool = ToolDefinition(
        name=tool_name,
        description="Always fails.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=failing_tool,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(tool_name=tool.name),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    run = next(iter(repo.tool_runs.values()))
    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    return run, part


@pytest.mark.parametrize(
    ("exc_factory", "expected_code", "expected_message"),
    [
        (
            lambda: RuntimeError("token=secret"),
            "tool_execution_failed",
            "Tool execution failed",
        ),
        (
            lambda: asyncio.TimeoutError("slow backend"),
            "timeout",
            "Tool timed out",
        ),
    ],
    ids=["runtime-error", "timeout"],
)
async def test_tool_failure_contract_redacts_raw_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    exc_factory: Callable[[], Exception],
    expected_code: str,
    expected_message: str,
) -> None:
    run, part = await _run_failed_tool_call(
        monkeypatch=monkeypatch,
        tool_name=f"failing_tool_{expected_code}",
        exc_factory=exc_factory,
    )

    assert run.status == ToolRunStatus.FAILED
    assert run.error == expected_code
    assert part["type"] == "tool-result"
    assert part["isError"] is True
    assert part["result"] == {
        "error": expected_message,
        "error_code": expected_code,
    }


async def test_tool_failure_contract_logs_original_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("ERROR"):
        run, part = await _run_failed_tool_call(
            monkeypatch=monkeypatch,
            tool_name="failing_tool_logged",
            exc_factory=lambda: RuntimeError("token=secret"),
        )

    assert run.error == "tool_execution_failed"
    assert part["result"] == {
        "error": "Tool execution failed",
        "error_code": "tool_execution_failed",
    }
    assert "tool execution failed" in caplog.text
    assert "token=secret" in caplog.text
