from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from noa_api.core.agent.runner import (
    _build_approval_context,
    AgentRunner,
    AgentRunnerResult,
    LLMToolCall,
    LLMTurnResponse,
    RuleBasedLLMClient,
)
from noa_api.core.tools.registry import ToolDefinition
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    ToolRisk,
    ToolRunStatus,
)
from noa_api.storage.postgres.models import ActionRequest, ToolRun


@dataclass
class _FakeLLMClient:
    response: LLMTurnResponse

    async def run_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta=None,
    ) -> LLMTurnResponse:
        _ = messages, tools, on_text_delta
        return self.response


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

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="I'll check the server time.",
                    tool_calls=[LLMToolCall(name="get_current_time", arguments={})],
                )
            return LLMTurnResponse(text="The current time is available.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result: AgentRunnerResult = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "What time is it?"}]}
        ],
        available_tool_names={"get_current_time"},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert len(result.messages) == 4
    assert result.messages[0].role == "assistant"
    assert result.messages[0].parts[0]["type"] == "text"
    assert result.messages[1].parts[0]["type"] == "tool-call"
    assert result.messages[2].parts[0]["type"] == "tool-result"
    final_part = result.messages[3].parts[0]
    assert isinstance(final_part, dict)
    assert final_part.get("type") == "text"
    assert final_part.get("text") == "The current time is available."

    assert len(repo.tool_runs) == 1
    run = next(iter(repo.tool_runs.values()))
    assert run.tool_name == "get_current_time"
    assert run.status == ToolRunStatus.COMPLETED
    assert run.result is not None
    assert "time" in run.result


async def test_agent_runner_sanitizes_tool_result_message_parts(monkeypatch) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def json_unsafe_tool() -> dict[str, object]:
        return {
            "when": datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
            "id": uuid4(),
        }

    tool = ToolDefinition(
        name="json_unsafe_tool",
        description="Returns non-JSON-native values.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=json_unsafe_tool,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[LLMToolCall(name=tool.name, arguments={})],
                )
            return LLMTurnResponse(text="done", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    tool_msg = next(message for message in result.messages if message.role == "tool")
    part = tool_msg.parts[0]
    assert isinstance(part, dict)
    tool_result = part.get("result")
    assert isinstance(tool_result, dict)
    assert tool_result["when"] == "2026-03-13T12:00:00+00:00"
    assert isinstance(tool_result["id"], str)


async def test_agent_runner_redacts_tool_execution_errors(monkeypatch) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def failing_tool() -> dict[str, object]:
        raise RuntimeError("token=secret")

    tool = ToolDefinition(
        name="failing_tool",
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
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[LLMToolCall(name=tool.name, arguments={})],
                )
            return LLMTurnResponse(text="done", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "tool_execution_failed"
    assert "token=secret" not in str(run.error)

    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["type"] == "tool-result"
    assert part["isError"] is True
    assert part["result"] == {
        "error": "Tool execution failed",
        "error_code": "tool_execution_failed",
    }


async def test_agent_runner_emits_clear_message_when_tool_not_allowed() -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, on_text_delta
            self.turn += 1
            if self.turn == 1:
                has_time_tool = False
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    if tool.get("type") != "function":
                        continue
                    fn = tool.get("function")
                    if not isinstance(fn, dict):
                        continue
                    if fn.get("name") == "get_current_time":
                        has_time_tool = True
                        break

                # Mimic OpenAI tool calling behavior: if the tool is not present
                # in the provided tool catalog, the model cannot call it.
                if not has_time_tool:
                    return LLMTurnResponse(
                        text=(
                            "I'm not able to access a real-time clock, so I can't give you the exact current time. "
                            "If you need the precise time, you can check a clock on your device or use an online time service."
                        ),
                        tool_calls=[],
                    )
                return LLMTurnResponse(
                    text="I'll check the server time.",
                    tool_calls=[LLMToolCall(name="get_current_time", arguments={})],
                )
            # If the runner loops and calls the LLM again after a denied tool call,
            # mimic a typical model behavior: fall back to a non-tool answer.
            return LLMTurnResponse(
                text=(
                    "I'm not able to retrieve the current time directly at the moment. "
                    "If you need the exact time, you can check a clock on your device."
                ),
                tool_calls=[],
            )

    llm = _TwoTurnLLM()

    runner = AgentRunner(
        llm_client=llm,
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "What time is it?"}]}
        ],
        available_tool_names=set(),
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    expected_denied_message = (
        "You don't have permission to use tool 'get_current_time'. "
        "Please ask SimondayCE Team to enable tool access for your account."
    )
    texts = [
        m.parts[0].get("text") for m in result.messages if isinstance(m.parts[0], dict)
    ]
    assert expected_denied_message in texts
    assert llm.turn == 1
    assert len(repo.tool_runs) == 0
    assert len(repo.action_requests) == 0


async def test_agent_runner_calls_llm_again_after_tool_results() -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    class _LoopingLLM:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, object]]] = []

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = tools, on_text_delta
            self.calls.append(list(messages))

            if len(self.calls) == 1:
                return LLMTurnResponse(
                    text="I'll check today's server date.",
                    tool_calls=[LLMToolCall(name="get_current_date", arguments={})],
                )

            return LLMTurnResponse(text="Today's date is available.", tool_calls=[])

    llm = _LoopingLLM()
    runner = AgentRunner(
        llm_client=llm,
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "What's the date?"}]},
        ],
        available_tool_names={"get_current_date"},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert len(llm.calls) == 2

    saw_tool_result_in_second_call = False
    for msg in llm.calls[1]:
        if msg.get("role") != "tool":
            continue
        parts = msg.get("parts")
        if not isinstance(parts, list):
            continue
        if any(
            isinstance(part, dict) and part.get("type") == "tool-result"
            for part in parts
        ):
            saw_tool_result_in_second_call = True
            break
    assert saw_tool_result_in_second_call is True

    assert [m.role for m in result.messages] == [
        "assistant",
        "assistant",
        "tool",
        "assistant",
    ]
    assert [
        m.parts[0].get("type") if isinstance(m.parts[0], dict) else None
        for m in result.messages
    ] == [
        "text",
        "tool-call",
        "tool-result",
        "text",
    ]
    final_part = result.messages[3].parts[0]
    assert isinstance(final_part, dict)
    assert final_part.get("text") == "Today's date is available."


async def test_agent_runner_creates_action_request_for_change_tools_without_execution() -> (
    None
):
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
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "Set demo flag"}]}
        ],
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
    assert isinstance(approval_part, dict)
    assert approval_part["type"] == "tool-call"
    assert approval_part["toolName"] == "request_approval"
    approval_args = approval_part.get("args")
    assert isinstance(approval_args, dict)
    assert approval_args.get("actionRequestId") == str(request.id)


async def test_agent_runner_fails_read_tool_run_when_args_do_not_match_schema(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_tool(*, name: str) -> dict[str, object]:
        raise AssertionError(f"unexpected execution for {name}")

    tool = ToolDefinition(
        name="guarded_read",
        description="Requires a name argument.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        execute=guarded_tool,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[LLMToolCall(name=tool.name, arguments={})],
                )
            return LLMTurnResponse(text="Need a name.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "invalid_tool_arguments"

    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["isError"] is True
    assert part["result"] == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["Missing required field 'name'"],
    }


async def test_agent_runner_rejects_change_tool_when_args_do_not_match_schema(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_change(*, reason: str) -> dict[str, object]:
        raise AssertionError(f"unexpected execution for {reason}")

    tool = ToolDefinition(
        name="guarded_change",
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
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[LLMToolCall(name=tool.name, arguments={})],
                )
            return LLMTurnResponse(text="A reason is required.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert repo.action_requests == {}
    assert repo.tool_runs == {}

    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["isError"] is True
    assert part["result"] == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["Missing required field 'reason'"],
    }


async def test_agent_runner_rejects_whitespace_only_string_arguments(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_tool(*, name: str) -> dict[str, object]:
        raise AssertionError(f"unexpected execution for {name}")

    tool = ToolDefinition(
        name="guarded_blank_read",
        description="Requires a non-blank name argument.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        execute=guarded_tool,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[LLMToolCall(name=tool.name, arguments={"name": "   "})],
                )
            return LLMTurnResponse(text="Need a real name.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "invalid_tool_arguments"

    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["result"] == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["name must not be blank"],
    }


async def test_agent_runner_fails_when_tool_returns_invalid_result_shape(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def bad_result_tool() -> dict[str, object]:
        return {"ok": True}

    tool = ToolDefinition(
        name="bad_result_tool",
        description="Returns an invalid result payload.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=bad_result_tool,
        result_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean", "enum": [True]},
                "flag": {"type": "object"},
            },
            "required": ["ok", "flag"],
        },
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[LLMToolCall(name=tool.name, arguments={})],
                )
            return LLMTurnResponse(text="Tool result was invalid.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error == "invalid_tool_result"

    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["result"] == {
        "error": "Tool returned an invalid result",
        "error_code": "invalid_tool_result",
        "details": ["Missing required field 'flag'"],
    }


async def test_agent_runner_requires_account_preflight_before_change_proposal(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_change(
        *, server_ref: str, username: str, reason: str
    ) -> dict[str, object]:
        raise AssertionError(
            f"unexpected execution for {server_ref} {username} {reason}"
        )

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Requires account preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={
                                "server_ref": "web1",
                                "username": "alice",
                                "reason": "requested by customer",
                            },
                        )
                    ],
                )
            return LLMTurnResponse(text="Run preflight first.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "Suspend alice"}]}
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert repo.action_requests == {}
    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["result"] == {
        "error": "Required WHM preflight evidence is missing",
        "error_code": "preflight_required",
        "details": [
            "Run whm_preflight_account with the same server_ref and username before requesting this change."
        ],
    }


async def test_agent_runner_rejects_account_change_when_preflight_targets_other_account(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_change(
        *, server_ref: str, username: str, reason: str
    ) -> dict[str, object]:
        raise AssertionError(
            f"unexpected execution for {server_ref} {username} {reason}"
        )

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Requires matching account preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={
                                "server_ref": "web1",
                                "username": "alice",
                                "reason": "requested by customer",
                            },
                        )
                    ],
                )
            return LLMTurnResponse(text="Wrong preflight.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "Suspend alice"}]},
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "bob"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {"ok": True, "account": {"user": "bob"}},
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert repo.action_requests == {}
    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["result"] == {
        "error": "Required WHM preflight evidence does not match this change request",
        "error_code": "preflight_mismatch",
        "details": [
            "No successful whm_preflight_account was found for server_ref 'web1' and username 'alice' in the current turn."
        ],
    }


async def test_agent_runner_rejects_account_change_when_preflight_result_user_differs(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_change(
        *, server_ref: str, username: str, reason: str
    ) -> dict[str, object]:
        raise AssertionError(
            f"unexpected execution for {server_ref} {username} {reason}"
        )

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Requires matching account preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={
                                "server_ref": "web1",
                                "username": "alice",
                                "reason": "requested by customer",
                            },
                        )
                    ],
                )
            return LLMTurnResponse(text="Wrong preflight result.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "Suspend alice"}]},
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {"ok": True, "account": {"user": "bob"}},
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert repo.action_requests == {}
    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["result"] == {
        "error": "Required WHM preflight evidence does not match this change request",
        "error_code": "preflight_mismatch",
        "details": [
            "No successful whm_preflight_account was found for server_ref 'web1' and username 'alice' in the current turn."
        ],
    }


async def test_agent_runner_rejects_csf_change_when_preflight_is_missing_for_some_targets(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_change(
        *, server_ref: str, targets: list[str], reason: str
    ) -> dict[str, object]:
        raise AssertionError(
            f"unexpected execution for {server_ref} {targets} {reason}"
        )

    tool = ToolDefinition(
        name="whm_csf_unblock",
        description="Requires target-by-target preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "targets", "reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={
                                "server_ref": "web1",
                                "targets": ["1.2.3.4", "5.6.7.8"],
                                "reason": "customer unblock",
                            },
                        )
                    ],
                )
            return LLMTurnResponse(text="Need one more preflight.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "Unblock both IPs"}]},
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_csf_entries",
                        "toolCallId": "preflight-csf-1",
                        "args": {"server_ref": "web1", "target": "1.2.3.4"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_csf_entries",
                        "toolCallId": "preflight-csf-1",
                        "result": {
                            "ok": True,
                            "target": "1.2.3.4",
                            "verdict": "blocked",
                            "matches": ["/etc/csf/csf.deny"],
                        },
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert repo.action_requests == {}
    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["result"] == {
        "error": "Required WHM preflight evidence does not match this change request",
        "error_code": "preflight_mismatch",
        "details": [
            "Missing successful whm_preflight_csf_entries results for target(s): '5.6.7.8'"
        ],
    }


async def test_agent_runner_rejects_csf_change_when_preflight_result_target_differs(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_change(
        *, server_ref: str, targets: list[str], reason: str
    ) -> dict[str, object]:
        raise AssertionError(
            f"unexpected execution for {server_ref} {targets} {reason}"
        )

    tool = ToolDefinition(
        name="whm_csf_unblock",
        description="Requires target-by-target preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "targets", "reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={
                                "server_ref": "web1",
                                "targets": ["1.2.3.4"],
                                "reason": "customer unblock",
                            },
                        )
                    ],
                )
            return LLMTurnResponse(text="Wrong preflight result.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "Unblock 1.2.3.4"}]},
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_csf_entries",
                        "toolCallId": "preflight-csf-1",
                        "args": {"server_ref": "web1", "target": "1.2.3.4"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_csf_entries",
                        "toolCallId": "preflight-csf-1",
                        "result": {
                            "ok": True,
                            "target": "9.9.9.9",
                            "verdict": "blocked",
                            "matches": ["/etc/csf/csf.deny"],
                        },
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert repo.action_requests == {}
    tool_message = next(
        message for message in result.messages if message.role == "tool"
    )
    part = tool_message.parts[0]
    assert isinstance(part, dict)
    assert part["result"] == {
        "error": "Required WHM preflight evidence does not match this change request",
        "error_code": "preflight_mismatch",
        "details": [
            "Missing successful whm_preflight_csf_entries results for target(s): '1.2.3.4'"
        ],
    }


async def test_agent_runner_allows_change_proposal_when_matching_preflight_exists(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def guarded_change(
        *, server_ref: str, username: str, reason: str
    ) -> dict[str, object]:
        raise AssertionError(
            f"unexpected execution for {server_ref} {username} {reason}"
        )

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Requires matching account preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        execute=guarded_change,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    runner = AgentRunner(
        llm_client=_FakeLLMClient(
            response=LLMTurnResponse(
                text="I can suspend that account after approval.",
                tool_calls=[
                    LLMToolCall(
                        name=tool.name,
                        arguments={
                            "server_ref": "web1",
                            "username": "alice",
                            "reason": "requested by customer",
                        },
                    )
                ],
            )
        ),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "Suspend alice"}]},
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-allow-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-allow-1",
                        "result": {"ok": True, "account": {"user": "alice"}},
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert len(repo.action_requests) == 1
    request = next(iter(repo.action_requests.values()))
    assert request.tool_name == tool.name
    approval_part = result.messages[-1].parts[0]
    assert isinstance(approval_part, dict)
    assert approval_part["toolName"] == "request_approval"


async def test_build_approval_context_uses_correct_change_arguments_in_activity() -> (
    None
):
    change_email_context = _build_approval_context(
        tool_name="whm_change_contact_email",
        args={"username": "alice", "new_email": "alice@example.com"},
        working_messages=[],
    )
    unblock_context = _build_approval_context(
        tool_name="whm_csf_unblock",
        args={"targets": ["1.2.3.4", "5.6.7.8"]},
        working_messages=[],
    )

    assert (
        change_email_context["activity"]
        == "Change contact email for 'alice' to 'alice@example.com'"
    )
    assert unblock_context["activity"] == "Remove CSF block for '1.2.3.4, 5.6.7.8'"


async def test_rule_based_llm_responds_to_date_tool_result() -> None:
    client = RuleBasedLLMClient()

    turn = await client.run_turn(
        messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "What's the date?"}],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-1",
                        "args": {},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-1",
                        "result": {"date": "2026-03-12"},
                        "isError": False,
                    }
                ],
            },
        ],
        tools=[],
        on_text_delta=None,
    )

    assert turn.text == "Today's date is 2026-03-12."
    assert turn.tool_calls == []


async def test_rule_based_llm_responds_to_time_tool_result() -> None:
    client = RuleBasedLLMClient()

    turn = await client.run_turn(
        messages=[
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "get_current_time",
                        "toolCallId": "tc-2",
                        "result": {"time": "2026-03-12T22:30:00+00:00"},
                        "isError": False,
                    }
                ],
            },
        ],
        tools=[],
        on_text_delta=None,
    )

    assert turn.text == "The current time is 2026-03-12T22:30:00+00:00."
    assert turn.tool_calls == []


async def test_rule_based_llm_ignores_errored_tool_result() -> None:
    client = RuleBasedLLMClient()

    turn = await client.run_turn(
        messages=[
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-3",
                        "result": {"error": "boom"},
                        "isError": True,
                    }
                ],
            },
        ],
        tools=[],
        on_text_delta=None,
    )

    assert (
        turn.text
        == "I can help with date/time checks and demo flag requests in this MVP."
    )
    assert turn.tool_calls == []


async def test_rule_based_llm_does_not_use_stale_success_when_latest_result_errors() -> (
    None
):
    client = RuleBasedLLMClient()

    turn = await client.run_turn(
        messages=[
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-ok",
                        "result": {"date": "2026-03-11"},
                        "isError": False,
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-err",
                        "result": {"error": "boom"},
                        "isError": True,
                    }
                ],
            },
        ],
        tools=[],
        on_text_delta=None,
    )

    assert (
        turn.text
        == "I can help with date/time checks and demo flag requests in this MVP."
    )
    assert turn.tool_calls == []


async def test_rule_based_llm_prioritizes_latest_user_turn_over_old_tool_result() -> (
    None
):
    client = RuleBasedLLMClient()

    turn = await client.run_turn(
        messages=[
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-old",
                        "result": {"date": "2026-03-11"},
                        "isError": False,
                    }
                ],
            },
            {
                "role": "user",
                "parts": [{"type": "text", "text": "hello"}],
            },
        ],
        tools=[],
        on_text_delta=None,
    )

    assert (
        turn.text
        == "I can help with date/time checks and demo flag requests in this MVP."
    )
    assert turn.tool_calls == []


async def test_rule_based_llm_handles_set_demo_flag_tool_result_without_reasking() -> (
    None
):
    client = RuleBasedLLMClient()

    turn = await client.run_turn(
        messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Set demo flag feature_x = true"}],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "set_demo_flag",
                        "toolCallId": "tc-change-1",
                        "args": {"key": "feature_x", "value": True},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "set_demo_flag",
                        "toolCallId": "tc-change-1",
                        "result": {
                            "ok": True,
                            "flag": {"key": "feature_x", "value": True},
                        },
                        "isError": False,
                    }
                ],
            },
        ],
        tools=[],
        on_text_delta=None,
    )

    assert turn.text == "The demo flag was updated."
    assert turn.tool_calls == []
