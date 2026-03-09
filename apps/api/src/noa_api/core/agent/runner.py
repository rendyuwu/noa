from __future__ import annotations

import json
import re
from dataclasses import dataclass
from inspect import signature
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.config import settings
from noa_api.core.tools.registry import ToolDefinition, get_tool_definition, get_tool_registry
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ToolRisk


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class LLMTurnResponse:
    text: str
    tool_calls: list[LLMToolCall]


class LLMClientProtocol(Protocol):
    async def run_turn(self, *, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> LLMTurnResponse: ...


@dataclass(frozen=True, slots=True)
class AgentMessage:
    role: str
    parts: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class AgentRunnerResult:
    messages: list[AgentMessage]
    text_deltas: list[str]


class RuleBasedLLMClient:
    async def run_turn(self, *, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> LLMTurnResponse:
        _ = tools
        last_user_text = ""
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            parts = message.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text")
                    if isinstance(text, str):
                        last_user_text = text
                        break
            if last_user_text:
                break

        lowered = last_user_text.lower()
        if "time" in lowered:
            return LLMTurnResponse(
                text="I'll check the current server time.",
                tool_calls=[LLMToolCall(name="get_current_time", arguments={})],
            )
        if "date" in lowered:
            return LLMTurnResponse(
                text="I'll check today's server date.",
                tool_calls=[LLMToolCall(name="get_current_date", arguments={})],
            )
        if "demo flag" in lowered:
            key, value = _extract_demo_flag_args(last_user_text)
            return LLMTurnResponse(
                text="I can set that demo flag after your approval.",
                tool_calls=[LLMToolCall(name="set_demo_flag", arguments={"key": key, "value": value})],
            )

        return LLMTurnResponse(
            text="I can help with date/time checks and demo flag requests in this MVP.",
            tool_calls=[],
        )


class OpenAICompatibleLLMClient:
    def __init__(self, *, model: str, api_key: str, base_url: str | None, system_prompt: str) -> None:
        from openai import AsyncOpenAI

        self._model = model
        self._system_prompt = system_prompt
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def run_turn(self, *, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> LLMTurnResponse:
        llm_messages: list[dict[str, str]] = []
        if self._system_prompt.strip():
            llm_messages.append({"role": "system", "content": self._system_prompt})

        for message in messages:
            role = message.get("role")
            if not isinstance(role, str):
                continue
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"

            parts = message.get("parts")
            text_parts: list[str] = []
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text)
            if not text_parts:
                continue
            llm_messages.append({"role": role, "content": "\n".join(text_parts)})

        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=llm_messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
        )

        choice = response.choices[0].message
        text = choice.content or ""
        tool_calls: list[LLMToolCall] = []
        for call in choice.tool_calls or []:
            name = call.function.name
            args_raw = call.function.arguments
            args = _safe_json_object(args_raw)
            tool_calls.append(LLMToolCall(name=name, arguments=args))

        return LLMTurnResponse(text=text, tool_calls=tool_calls)


class AgentRunner:
    def __init__(
        self,
        *,
        llm_client: LLMClientProtocol,
        action_tool_run_service: ActionToolRunService,
        session: AsyncSession | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._action_tool_run_service = action_tool_run_service
        self._session = session

    async def run_turn(
        self,
        *,
        thread_messages: list[dict[str, object]],
        available_tool_names: set[str],
        thread_id: UUID,
        requested_by_user_id: UUID,
    ) -> AgentRunnerResult:
        allowed_tools = [tool for tool in get_tool_registry() if tool.name in available_tool_names]
        llm_tools = [_to_openai_tool_schema(tool) for tool in allowed_tools]
        llm_response = await self._llm_client.run_turn(messages=thread_messages, tools=llm_tools)

        output_messages: list[AgentMessage] = []
        text = llm_response.text.strip()
        text_deltas = _split_text_deltas(text)
        if text:
            output_messages.append(
                AgentMessage(
                    role="assistant",
                    parts=[{"type": "text", "text": text}],
                )
            )

        for tool_call in llm_response.tool_calls:
            tool = get_tool_definition(tool_call.name)
            if tool is None or tool.name not in available_tool_names:
                output_messages.append(
                    AgentMessage(
                        role="assistant",
                        parts=[
                            {
                                "type": "text",
                                "text": f"Tool '{tool_call.name}' is not available for this user.",
                            }
                        ],
                    )
                )
                continue

            output_messages.extend(
                await self._process_tool_call(
                    tool=tool,
                    args=tool_call.arguments,
                    thread_id=thread_id,
                    requested_by_user_id=requested_by_user_id,
                )
            )

        return AgentRunnerResult(messages=output_messages, text_deltas=text_deltas)

    async def _process_tool_call(
        self,
        *,
        tool: ToolDefinition,
        args: dict[str, object],
        thread_id: UUID,
        requested_by_user_id: UUID,
    ) -> list[AgentMessage]:
        if tool.risk == ToolRisk.CHANGE:
            action_request = await self._action_tool_run_service.create_action_request(
                thread_id=thread_id,
                tool_name=tool.name,
                args=args,
                risk=tool.risk,
                requested_by_user_id=requested_by_user_id,
            )
            return [
                AgentMessage(
                    role="assistant",
                    parts=[
                        {
                            "type": "tool-call",
                            "toolName": tool.name,
                            "toolCallId": f"proposal-{action_request.id}",
                            "args": args,
                        }
                    ],
                ),
                AgentMessage(
                    role="assistant",
                    parts=[
                        {
                            "type": "tool-call",
                            "toolName": "request_approval",
                            "toolCallId": f"request-approval-{action_request.id}",
                            "args": {
                                "actionRequestId": str(action_request.id),
                                "toolName": tool.name,
                                "risk": tool.risk.value,
                                "arguments": args,
                            },
                        }
                    ],
                ),
            ]

        started = await self._action_tool_run_service.start_tool_run(
            thread_id=thread_id,
            tool_name=tool.name,
            args=args,
            action_request_id=None,
            requested_by_user_id=requested_by_user_id,
        )
        tool_call_id = str(started.id)
        call_message = AgentMessage(
            role="assistant",
            parts=[
                {
                    "type": "tool-call",
                    "toolName": tool.name,
                    "toolCallId": tool_call_id,
                    "args": args,
                }
            ],
        )

        try:
            result = await self._execute_tool(tool=tool, args=args)
            _ = await self._action_tool_run_service.complete_tool_run(tool_run_id=started.id, result=result)
            result_message = AgentMessage(
                role="tool",
                parts=[
                    {
                        "type": "tool-result",
                        "toolName": tool.name,
                        "toolCallId": tool_call_id,
                        "result": result,
                        "isError": False,
                    }
                ],
            )
            return [call_message, result_message]
        except Exception as exc:
            _ = await self._action_tool_run_service.fail_tool_run(tool_run_id=started.id, error=str(exc))
            error_message = AgentMessage(
                role="tool",
                parts=[
                    {
                        "type": "tool-result",
                        "toolName": tool.name,
                        "toolCallId": tool_call_id,
                        "result": {"error": str(exc)},
                        "isError": True,
                    }
                ],
            )
            return [call_message, error_message]

    async def _execute_tool(self, *, tool: ToolDefinition, args: dict[str, object]) -> dict[str, object]:
        if self._session is not None and "session" in signature(tool.execute).parameters:
            return await tool.execute(session=self._session, **args)
        return await tool.execute(**args)


def create_default_llm_client() -> LLMClientProtocol:
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key is not None else ""
    if api_key:
        return OpenAICompatibleLLMClient(
            model=settings.llm_model,
            api_key=api_key,
            base_url=settings.llm_base_url,
            system_prompt=settings.llm_system_prompt,
        )
    return RuleBasedLLMClient()


def _safe_json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _to_openai_tool_schema(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        },
    }


def _split_text_deltas(text: str, *, chunk_size: int = 24) -> list[str]:
    if not text:
        return []
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def _extract_demo_flag_args(text: str) -> tuple[str, object]:
    match = re.search(r"demo\s+flag\s+([a-zA-Z0-9_.-]+)\s*=\s*(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return "demo_flag", True
    key = match.group(1)
    raw_value = match.group(2).strip()
    lowered = raw_value.lower()
    if lowered in {"true", "yes", "on"}:
        return key, True
    if lowered in {"false", "no", "off"}:
        return key, False
    if raw_value.isdigit():
        return key, int(raw_value)
    return key, raw_value
