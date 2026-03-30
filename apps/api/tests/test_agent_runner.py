from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
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


async def _async_return(value):
    return value


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


async def test_agent_runner_recovers_when_model_calls_request_approval_directly(
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
        description="Requires approval before execution.",
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
    monkeypatch.setattr(
        "noa_api.core.agent.runner._resolve_requested_server_id",
        lambda **kwargs: _async_return("server-1"),
    )

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
            _ = tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="",
                    tool_calls=[LLMToolCall(name="request_approval", arguments={})],
                )

            saw_internal_guidance = False
            for message in messages:
                parts = message.get("parts")
                if not isinstance(parts, list):
                    continue
                if any(
                    isinstance(part, dict)
                    and part.get("text")
                    == (
                        "Approval requests are created automatically after you call "
                        "the underlying CHANGE tool. Do not call request_approval "
                        "directly."
                    )
                    for part in parts
                ):
                    saw_internal_guidance = True
                    break
            assert saw_internal_guidance is True
            return LLMTurnResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        name="whm_suspend_account",
                        arguments={
                            "server_ref": "web1",
                            "username": "alice",
                            "reason": "requested by customer",
                        },
                    )
                ],
            )

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

    texts: list[str] = []
    for message in result.messages:
        part = message.parts[0]
        if not isinstance(part, dict):
            continue
        if part.get("type") != "text":
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
    assert not any(
        text and "You don't have permission to use tool 'request_approval'" in text
        for text in texts
    )
    assert len(repo.action_requests) == 1
    approval_part = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "request_approval"
    )
    assert approval_part.get("toolName") == "request_approval"


async def test_agent_runner_stops_after_repeated_direct_request_approval_calls(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    tool = ToolDefinition(
        name="whm_suspend_account",
        description="Requires approval before execution.",
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
        execute=lambda **kwargs: _async_return(kwargs),
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _LoopingLLM:
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
            return LLMTurnResponse(
                text="",
                tool_calls=[LLMToolCall(name="request_approval", arguments={})],
            )

    llm = _LoopingLLM()
    runner = AgentRunner(
        llm_client=llm,
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

    assert llm.turn == 2
    assert not any(
        isinstance(part, dict)
        and part.get("text") == "Tool loop exceeded safety limits."
        for message in result.messages
        for part in message.parts
    )
    final_part = result.messages[-1].parts[0]
    assert isinstance(final_part, dict)
    assert final_part["type"] == "text"
    assert "Do not call request_approval directly" in cast(str, final_part["text"])


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


async def test_agent_runner_persists_deterministic_whm_workflow_when_reason_missing(
    monkeypatch,
) -> None:
    from noa_api.core.tools.registry import get_tool_definition

    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    tool = get_tool_definition("whm_suspend_account")
    assert tool is not None

    class _SingleTurnLLM:
        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools, on_text_delta
            return LLMTurnResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        name="whm_suspend_account",
                        arguments={"server_ref": "web1", "username": "alice"},
                    )
                ],
            )

    thread_id = uuid4()
    runner = AgentRunner(
        llm_client=_SingleTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=cast(Any, object()),
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
                        "result": {
                            "ok": True,
                            "account": {"user": "alice", "suspended": False},
                        },
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=thread_id,
        requested_by_user_id=uuid4(),
    )

    assert repo.action_requests == {}
    assert captured["thread_id"] == thread_id
    todos = cast(list[dict[str, str]], captured["todos"])
    assert len(todos) == 5
    assert todos[0]["status"] == "completed"
    assert todos[1]["status"] == "waiting_on_user"
    assert todos[2]["status"] == "pending"

    tool_part = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-result"
        and part.get("toolName") == tool.name
    )
    result_payload = cast(dict[str, object], tool_part["result"])
    assert result_payload["error_code"] == "invalid_tool_arguments"

    todo_tool_call = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "update_workflow_todo"
    )
    assert isinstance(todo_tool_call.get("args"), dict)

    final_part = result.messages[-1].parts[0]
    assert isinstance(final_part, dict)
    assert final_part["type"] == "text"
    assert "short, human-readable reason" in cast(str, final_part["text"])


async def test_agent_runner_stops_retry_loop_when_reason_is_missing(
    monkeypatch,
) -> None:
    from noa_api.core.tools.registry import get_tool_definition

    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def _record_replace(self, *, thread_id, todos):
        _ = thread_id, todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    tool = get_tool_definition("whm_suspend_account")
    assert tool is not None

    class _LoopingLLM:
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
            return LLMTurnResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        name="whm_suspend_account",
                        arguments={"server_ref": "web1", "username": "alice"},
                    )
                ],
            )

    llm = _LoopingLLM()
    runner = AgentRunner(
        llm_client=llm,
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=cast(Any, object()),
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
                        "result": {
                            "ok": True,
                            "account": {"user": "alice", "suspended": False},
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

    assert llm.turn == 1
    assert not any(
        isinstance(part, dict)
        and part.get("text") == "Tool loop exceeded safety limits."
        for message in result.messages
        for part in message.parts
    )

    final_part = result.messages[-1].parts[0]
    assert isinstance(final_part, dict)
    assert final_part["type"] == "text"
    assert "Please provide the reason" in cast(str, final_part["text"])


async def test_agent_runner_persists_deterministic_whm_workflow_while_waiting_for_approval(
    monkeypatch,
) -> None:
    from noa_api.core.tools.registry import get_tool_definition

    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    tool = get_tool_definition("whm_suspend_account")
    assert tool is not None

    thread_id = uuid4()
    runner = AgentRunner(
        llm_client=_FakeLLMClient(
            response=LLMTurnResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        name="whm_suspend_account",
                        arguments={
                            "server_ref": "web1",
                            "username": "alice",
                            "reason": "billing hold",
                        },
                    )
                ],
            )
        ),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=cast(Any, object()),
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
                        "result": {
                            "ok": True,
                            "account": {"user": "alice", "suspended": False},
                        },
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=thread_id,
        requested_by_user_id=uuid4(),
    )

    assert len(repo.action_requests) == 1
    assert captured["thread_id"] == thread_id
    todos = cast(list[dict[str, str]], captured["todos"])
    assert len(todos) == 5
    assert todos[0]["status"] == "completed"
    assert todos[1]["status"] == "completed"
    assert todos[2]["status"] == "waiting_on_approval"

    approval_part = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "request_approval"
    )
    assert approval_part.get("toolName") == "request_approval"

    todo_tool_call = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "update_workflow_todo"
    )
    assert isinstance(todo_tool_call.get("args"), dict)


async def test_agent_runner_replaces_prior_whm_family_workflow_with_csf_waiting_state(
    monkeypatch,
) -> None:
    from noa_api.core.tools.registry import get_tool_definition

    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    captured: dict[str, object] = {
        "previous_todos": [
            {
                "content": "Inspect account 'alice' on 'web1'.",
                "status": "completed",
                "priority": "high",
            },
            {
                "content": "Request approval to suspend account 'alice' on 'web1'.",
                "status": "waiting_on_approval",
                "priority": "high",
            },
        ]
    }

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    tool = get_tool_definition("whm_csf_unblock")
    assert tool is not None

    thread_id = uuid4()
    runner = AgentRunner(
        llm_client=_FakeLLMClient(
            response=LLMTurnResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        name="whm_csf_unblock",
                        arguments={
                            "server_ref": "web2",
                            "targets": ["1.2.3.4", "5.6.7.8"],
                            "reason": "customer request",
                        },
                    )
                ],
            )
        ),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=cast(Any, object()),
    )

    result = await runner.run_turn(
        thread_messages=[
            {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Remove CSF block for 1.2.3.4 and 5.6.7.8 on web2",
                    }
                ],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_csf_entries",
                        "toolCallId": "preflight-csf-1",
                        "args": {"server_ref": "web2", "target": "1.2.3.4"},
                    },
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_csf_entries",
                        "toolCallId": "preflight-csf-2",
                        "args": {"server_ref": "web2", "target": "5.6.7.8"},
                    },
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
                            "matches": ["/etc/csf/csf.deny"],
                            "verdict": "blocked",
                        },
                        "isError": False,
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_csf_entries",
                        "toolCallId": "preflight-csf-2",
                        "result": {
                            "ok": True,
                            "target": "5.6.7.8",
                            "matches": ["/etc/csf/csf.deny"],
                            "verdict": "blocked",
                        },
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names={tool.name},
        thread_id=thread_id,
        requested_by_user_id=uuid4(),
    )

    assert len(repo.action_requests) == 1
    assert captured["thread_id"] == thread_id
    todos = cast(list[dict[str, str]], captured["todos"])
    assert len(todos) == 5
    assert all("alice" not in todo["content"] for todo in todos)
    assert any("1.2.3.4" in todo["content"] for todo in todos)
    assert any("5.6.7.8" in todo["content"] for todo in todos)
    assert todos[2]["status"] == "waiting_on_approval"

    approval_part = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "request_approval"
    )
    assert approval_part.get("toolName") == "request_approval"

    todo_tool_call = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "update_workflow_todo"
    )
    assert isinstance(todo_tool_call.get("args"), dict)


async def test_agent_runner_seeds_waiting_workflow_when_assistant_asks_for_reason_after_preflight(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    thread_id = uuid4()
    runner = AgentRunner(
        llm_client=_FakeLLMClient(
            response=LLMTurnResponse(
                text=(
                    "To proceed with unsuspending the account, I need a brief human-readable "
                    "reason for the change. Could you provide the reason?"
                ),
                tool_calls=[],
            )
        ),
        action_tool_run_service=ActionToolRunService(
            repository=_InMemoryActionToolRunRepository(
                action_requests={}, tool_runs={}
            )
        ),
        session=cast(Any, object()),
    )

    result = await runner.run_turn(
        thread_messages=[
            {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Unsuspend account rendy on web2",
                    }
                ],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web2", "username": "rendy"},
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
                        "result": {
                            "ok": True,
                            "account": {"user": "rendy", "suspended": True},
                        },
                        "isError": False,
                    }
                ],
            },
        ],
        available_tool_names=set(),
        thread_id=thread_id,
        requested_by_user_id=uuid4(),
    )

    assert result.messages[0].parts[0]["type"] == "text"
    assert captured["thread_id"] == thread_id
    todos = cast(list[dict[str, str]], captured["todos"])
    assert len(todos) == 5
    assert todos[0]["status"] == "completed"
    assert todos[1]["status"] == "waiting_on_user"
    assert todos[2]["status"] == "pending"

    todo_tool_call = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "update_workflow_todo"
    )
    assert isinstance(todo_tool_call.get("args"), dict)


async def test_agent_runner_reprompts_model_after_empty_post_tool_turn(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def preflight_tool(*, server_ref: str, target: str) -> dict[str, object]:
        assert server_ref == "web2"
        assert target == "187.150.230.11"
        return {
            "ok": True,
            "server_id": str(uuid4()),
            "target": target,
            "verdict": "blocked",
            "matches": ["filter DENYIN ... 187.150.230.11"],
        }

    tool = ToolDefinition(
        name="whm_preflight_csf_entries",
        description="Inspect CSF state for one target.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "target"],
            "additionalProperties": False,
        },
        execute=preflight_tool,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _ThreeTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="Saya akan memeriksa status CSF untuk IP tersebut di server web2.",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={
                                "server_ref": "web2",
                                "target": "187.150.230.11",
                            },
                        )
                    ],
                )

            if self.turn == 2:
                assert any(message.get("role") == "tool" for message in messages)
                return LLMTurnResponse(text="", tool_calls=[])

            assert any(
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
                and "Using the latest tool result you already have" in part["text"]
                for message in messages
                for part in cast(list[object], message.get("parts") or [])
            )
            return LLMTurnResponse(
                text="IP 187.150.230.11 terblokir di CSF server web2.",
                tool_calls=[],
            )

    runner = AgentRunner(
        llm_client=_ThreeTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Cek apakah IP 187.150.230.11 terblock csf, di whm web2",
                    }
                ],
            }
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert result.messages[-1].role == "assistant"
    assert (
        result.messages[-1].parts[0]["text"]
        == "IP 187.150.230.11 terblokir di CSF server web2."
    )


async def test_agent_runner_falls_back_when_model_stays_empty_after_tool_result(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def preflight_tool(*, server_ref: str, target: str) -> dict[str, object]:
        return {
            "ok": True,
            "server_id": str(uuid4()),
            "target": target,
            "verdict": "blocked",
            "matches": ["filter DENYIN ... 187.150.230.11"],
            "raw_output": "filter DENYIN ... 187.150.230.11",
        }

    tool = ToolDefinition(
        name="whm_preflight_csf_entries",
        description="Inspect CSF state for one target.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "target"],
            "additionalProperties": False,
        },
        execute=preflight_tool,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _SilentAfterToolLLM:
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
                    text="Saya akan memeriksa status CSF.",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={
                                "server_ref": "web2",
                                "target": "187.150.230.11",
                            },
                        )
                    ],
                )
            return LLMTurnResponse(text="", tool_calls=[])

    runner = AgentRunner(
        llm_client=_SilentAfterToolLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Check whether 187.150.230.11 is blocked in CSF on web2",
                    }
                ],
            }
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert result.messages[-1].role == "assistant"
    assert result.messages[-1].parts[0]["text"] == (
        "CSF result: 187.150.230.11 on server web2 is blocked.\n\n"
        "Evidence: Found 1 matching CSF entry.\n\n"
        "Raw preflight output:\n"
        "```\n"
        "filter DENYIN ... 187.150.230.11\n"
        "```"
    )


async def test_agent_runner_appends_csf_preflight_raw_output_to_followup_text_before_approval(
    monkeypatch,
) -> None:
    from noa_api.core.tools.registry import get_tool_definition

    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    raw_output = "ALLOWIN 103.103.11.123 # allow\nALLOWOUT 103.103.11.123 # allow"

    async def preflight_tool(*, server_ref: str, target: str) -> dict[str, object]:
        assert server_ref == "web2"
        assert target == "103.103.11.123"
        return {
            "ok": True,
            "server_id": str(uuid4()),
            "target": target,
            "verdict": "allowlisted",
            "matches": ["/etc/csf/csf.allow"],
            "raw_output": raw_output,
        }

    preflight_def = ToolDefinition(
        name="whm_preflight_csf_entries",
        description="Inspect CSF state for one target.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "target"],
            "additionalProperties": False,
        },
        execute=preflight_tool,
    )

    change_def = get_tool_definition("whm_csf_allowlist_remove")
    assert change_def is not None

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: (
            preflight_def
            if name == preflight_def.name
            else change_def
            if name == change_def.name
            else None
        ),
    )
    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_registry",
        lambda: (preflight_def, change_def),
    )

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
                    text="Saya akan cek status CSF dulu.",
                    tool_calls=[
                        LLMToolCall(
                            name="whm_preflight_csf_entries",
                            arguments={
                                "server_ref": "web2",
                                "target": "103.103.11.123",
                            },
                        )
                    ],
                )
            return LLMTurnResponse(
                text=(
                    "Evidence: IP 103.103.11.123 saat ini allowlisted di server web2.\n\n"
                    "Proposed change: Remove 103.103.11.123 from the CSF allowlist on web2."
                ),
                tool_calls=[
                    LLMToolCall(
                        name="whm_csf_allowlist_remove",
                        arguments={
                            "server_ref": "web2",
                            "targets": ["103.103.11.123"],
                            "reason": "Ticket #232123",
                        },
                    )
                ],
            )

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=None,
    )

    result = await runner.run_turn(
        thread_messages=[
            {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Remove this IP 103.103.11.123 from whitelist whm web2, reason: Ticket #232123",
                    }
                ],
            }
        ],
        available_tool_names={preflight_def.name, change_def.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    # Raw CSF output should be appended to the follow-up narration before approval tool UI.
    narration_index = next(
        index
        for index, message in enumerate(result.messages)
        if message.role == "assistant"
        and any(
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and "Raw preflight output for 103.103.11.123" in part["text"]
            for part in message.parts
        )
    )
    request_approval_index = next(
        index
        for index, message in enumerate(result.messages)
        if any(
            isinstance(part, dict)
            and part.get("type") == "tool-call"
            and part.get("toolName") == "request_approval"
            for part in message.parts
        )
    )
    assert narration_index < request_approval_index


async def test_agent_runner_falls_back_for_generic_read_success_when_model_stays_empty(
    monkeypatch,
) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def backup_status_tool(*, path: str) -> dict[str, object]:
        assert path == "/var/backups/latest.tar.gz"
        return {
            "date": "2026-03-28",
            "verdict": "ready",
            "count": 2,
            "path": path,
        }

    tool = ToolDefinition(
        name="get_backup_status",
        description="Inspect backup status.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        execute=backup_status_tool,
    )

    monkeypatch.setattr(
        "noa_api.core.agent.runner.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _SilentAfterToolLLM:
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
                    text="I'll inspect the latest backup status.",
                    tool_calls=[
                        LLMToolCall(
                            name=tool.name,
                            arguments={"path": "/var/backups/latest.tar.gz"},
                        )
                    ],
                )
            return LLMTurnResponse(text="", tool_calls=[])

    runner = AgentRunner(
        llm_client=_SilentAfterToolLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Check the latest backup status",
                    }
                ],
            }
        ],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    assert result.messages[-1].role == "assistant"
    assert result.messages[-1].parts[0]["text"] == (
        "Read result: date: 2026-03-28; verdict: ready; count: 2; "
        "path: /var/backups/latest.tar.gz."
    )


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
    approval_part = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "request_approval"
    )
    assert approval_part.get("toolName") == "request_approval"


async def test_agent_runner_allows_change_proposal_after_reason_follow_up(
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
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "text",
                        "text": (
                            "To proceed with suspending the account, I need a brief "
                            "human-readable reason for the change. Could you provide the reason?"
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "Requested by customer in ticket #121233."}
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
    approval_part = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "request_approval"
    )
    assert approval_part.get("toolName") == "request_approval"


async def test_agent_runner_allows_change_proposal_when_server_id_matches(
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
    monkeypatch.setattr(
        "noa_api.core.agent.runner._resolve_requested_server_id",
        lambda **kwargs: _async_return("server-1"),
    )

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
        session=None,
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
                        "args": {"server_ref": "whm.example.com", "username": "alice"},
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
                        "result": {
                            "ok": True,
                            "server_id": "server-1",
                            "account": {"user": "alice"},
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

    assert len(repo.action_requests) == 1
    approval_part = next(
        part
        for message in result.messages
        for part in message.parts
        if isinstance(part, dict)
        and part.get("type") == "tool-call"
        and part.get("toolName") == "request_approval"
    )
    assert approval_part.get("toolName") == "request_approval"


async def test_agent_runner_rejects_change_proposal_when_server_id_differs(
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
    monkeypatch.setattr(
        "noa_api.core.agent.runner._resolve_requested_server_id",
        lambda **kwargs: _async_return("server-1"),
    )

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(
            self, *, messages, tools, on_text_delta=None
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
            return LLMTurnResponse(text="Wrong server identity.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=None,
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
                        "result": {
                            "ok": True,
                            "server_id": "server-2",
                            "account": {"user": "alice"},
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
    result_payload = cast(dict[str, object], part["result"])
    assert result_payload["error_code"] == "preflight_mismatch"


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
    change_reply = cast(dict[str, object], change_email_context["replyTemplate"])
    unblock_reply = cast(dict[str, object], unblock_context["replyTemplate"])
    assert change_reply["title"] == "Contact email approval requested"
    assert change_reply["outcome"] == "info"
    assert unblock_reply["title"] == "CSF change approval requested"
    assert unblock_reply["outcome"] == "info"


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
