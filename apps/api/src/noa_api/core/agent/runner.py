from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from inspect import signature
from typing import Any, Awaitable, Callable, Protocol, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.config import settings
from noa_api.core.tools.registry import (
    ToolDefinition,
    get_tool_definition,
    get_tool_registry,
)
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
    async def run_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMTurnResponse: ...


@dataclass(frozen=True, slots=True)
class AgentMessage:
    role: str
    parts: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class AgentRunnerResult:
    messages: list[AgentMessage]
    text_deltas: list[str]


class RuleBasedLLMClient:
    async def run_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMTurnResponse:
        _ = tools

        async def _emit_text_turn(text: str) -> LLMTurnResponse:
            turn = LLMTurnResponse(text=text, tool_calls=[])
            if on_text_delta is not None and turn.text:
                for chunk in _split_text_deltas(turn.text):
                    await on_text_delta(chunk)
                    await asyncio.sleep(0)
            return turn

        tool_result_templates: dict[str, tuple[str, str]] = {
            "get_current_date": ("date", "Today's date is {value}."),
            "get_current_time": ("time", "The current time is {value}."),
        }
        relevant_tool_results = set(tool_result_templates) | {"set_demo_flag"}

        last_user_index = -1
        for index, message in enumerate(messages):
            if message.get("role") == "user":
                last_user_index = index

        latest_relevant_tool_result: dict[str, object] | None = None
        for message in reversed(messages[last_user_index + 1 :]):
            if message.get("role") != "tool":
                continue
            parts = message.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict) or part.get("type") != "tool-result":
                    continue
                tool_name = part.get("toolName")
                if not isinstance(tool_name, str):
                    continue
                if tool_name in relevant_tool_results:
                    latest_relevant_tool_result = part
                    break
            if latest_relevant_tool_result is not None:
                break

        if latest_relevant_tool_result is not None:
            if latest_relevant_tool_result.get("isError") is not True:
                tool_name = latest_relevant_tool_result.get("toolName")
                result = latest_relevant_tool_result.get("result")
                if isinstance(tool_name, str) and isinstance(result, dict):
                    if tool_name == "set_demo_flag":
                        if result.get("ok") is True:
                            return await _emit_text_turn("The demo flag was updated.")
                        return await _emit_text_turn(
                            "I could not confirm the demo flag update."
                        )
                    value_key, template = tool_result_templates[tool_name]
                    raw_value = result.get(value_key)
                    if isinstance(raw_value, str) and raw_value:
                        return await _emit_text_turn(template.format(value=raw_value))

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
            turn = LLMTurnResponse(
                text="I'll check the current server time.",
                tool_calls=[LLMToolCall(name="get_current_time", arguments={})],
            )
        elif "date" in lowered:
            turn = LLMTurnResponse(
                text="I'll check today's server date.",
                tool_calls=[LLMToolCall(name="get_current_date", arguments={})],
            )
        elif "demo flag" in lowered:
            key, value = _extract_demo_flag_args(last_user_text)
            turn = LLMTurnResponse(
                text="I can set that demo flag after your approval.",
                tool_calls=[
                    LLMToolCall(
                        name="set_demo_flag", arguments={"key": key, "value": value}
                    )
                ],
            )
        else:
            turn = LLMTurnResponse(
                text="I can help with date/time checks and demo flag requests in this MVP.",
                tool_calls=[],
            )

        if on_text_delta is not None and turn.text:
            for chunk in _split_text_deltas(turn.text):
                await on_text_delta(chunk)
                await asyncio.sleep(0)
        return turn


class OpenAICompatibleLLMClient:
    def __init__(
        self, *, model: str, api_key: str, base_url: str | None, system_prompt: str
    ) -> None:
        from openai import AsyncOpenAI

        self._model = model
        self._system_prompt = system_prompt
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def run_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMTurnResponse:
        llm_messages = _to_openai_chat_messages(
            messages=messages,
            system_prompt=self._system_prompt,
        )

        if on_text_delta is None:
            request_kwargs: dict[str, Any] = {
                "model": self._model,
                "temperature": 0,
                "messages": cast(Any, llm_messages),
            }
            if tools:
                request_kwargs["tools"] = cast(Any, tools)
                request_kwargs["tool_choice"] = "auto"

            response: Any = await self._client.chat.completions.create(**request_kwargs)

            choice: Any = response.choices[0].message
            text = getattr(choice, "content", "") or ""
            tool_calls: list[LLMToolCall] = []
            for call in getattr(choice, "tool_calls", None) or []:
                function = getattr(call, "function", None)
                if function is None:
                    continue
                name = getattr(function, "name", None)
                if not isinstance(name, str) or not name:
                    continue
                args_raw = getattr(function, "arguments", None)
                args = _safe_json_object(
                    args_raw if isinstance(args_raw, str) else None
                )
                tool_calls.append(LLMToolCall(name=name, arguments=args))

            return LLMTurnResponse(text=text, tool_calls=tool_calls)

        request_kwargs = {
            "model": self._model,
            "temperature": 0,
            "messages": cast(Any, llm_messages),
            "stream": True,
        }
        if tools:
            request_kwargs["tools"] = cast(Any, tools)
            request_kwargs["tool_choice"] = "auto"

        stream: Any = await self._client.chat.completions.create(**request_kwargs)

        text_chunks: list[str] = []
        tool_call_acc: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                text_chunks.append(content)
                await on_text_delta(content)

            delta_tool_calls = getattr(delta, "tool_calls", None)
            if not isinstance(delta_tool_calls, list) or not delta_tool_calls:
                continue

            for call in delta_tool_calls:
                index = getattr(call, "index", None)
                if index is None:
                    continue
                try:
                    idx = int(index)
                except (TypeError, ValueError):
                    continue

                acc = tool_call_acc.setdefault(idx, {"name": "", "arguments": ""})
                function = getattr(call, "function", None)
                if function is None:
                    continue

                name = getattr(function, "name", None)
                if isinstance(name, str) and name:
                    acc["name"] = name
                arguments = getattr(function, "arguments", None)
                if isinstance(arguments, str) and arguments:
                    acc["arguments"] += arguments

        text = "".join(text_chunks)
        tool_calls: list[LLMToolCall] = []
        for _, value in sorted(tool_call_acc.items()):
            name = value.get("name")
            if not name:
                continue
            args = _safe_json_object(value.get("arguments"))
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
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunnerResult:
        allowed_tools = [
            tool for tool in get_tool_registry() if tool.name in available_tool_names
        ]
        llm_tools = [_to_openai_tool_schema(tool) for tool in allowed_tools]
        max_rounds = 4
        max_tool_calls = 8

        working_messages = list(thread_messages)
        output_messages: list[AgentMessage] = []
        text_deltas: list[str] = []
        rounds = 0
        tool_calls_processed = 0
        hit_safety_limit = False

        while rounds < max_rounds and tool_calls_processed < max_tool_calls:
            rounds += 1
            if on_text_delta is None:
                llm_response = await self._llm_client.run_turn(
                    messages=working_messages,
                    tools=llm_tools,
                )
            else:
                llm_response = await self._llm_client.run_turn(
                    messages=working_messages,
                    tools=llm_tools,
                    on_text_delta=on_text_delta,
                )

            text = llm_response.text.strip()
            if text:
                assistant_text_part: dict[str, object] = {
                    "type": "text",
                    "text": text,
                }
                assistant_parts: list[dict[str, object]] = [assistant_text_part]
                output_messages.append(
                    AgentMessage(
                        role="assistant",
                        parts=assistant_parts,
                    )
                )
                working_messages.append(
                    {
                        "role": "assistant",
                        "parts": assistant_parts,
                    }
                )
                text_deltas.extend(_split_text_deltas(text))

            if not llm_response.tool_calls:
                return AgentRunnerResult(
                    messages=output_messages, text_deltas=text_deltas
                )

            for tool_call in llm_response.tool_calls:
                if tool_calls_processed >= max_tool_calls:
                    hit_safety_limit = True
                    break

                tool_calls_processed += 1
                tool = get_tool_definition(tool_call.name)
                if tool is None or tool.name not in available_tool_names:
                    unavailable_part: dict[str, object] = {
                        "type": "text",
                        "text": f"Tool '{tool_call.name}' is not available for this user.",
                    }
                    unavailable_parts: list[dict[str, object]] = [unavailable_part]
                    output_messages.append(
                        AgentMessage(
                            role="assistant",
                            parts=unavailable_parts,
                        )
                    )
                    working_messages.append(
                        {
                            "role": "assistant",
                            "parts": unavailable_parts,
                        }
                    )
                    continue

                tool_messages = await self._process_tool_call(
                    tool=tool,
                    args=tool_call.arguments,
                    thread_id=thread_id,
                    requested_by_user_id=requested_by_user_id,
                )
                output_messages.extend(tool_messages)
                for message in tool_messages:
                    working_messages.append(
                        {
                            "role": message.role,
                            "parts": message.parts,
                        }
                    )

                if tool.risk == ToolRisk.CHANGE:
                    return AgentRunnerResult(
                        messages=output_messages, text_deltas=text_deltas
                    )

        if not hit_safety_limit and (
            rounds >= max_rounds or tool_calls_processed >= max_tool_calls
        ):
            hit_safety_limit = True

        if hit_safety_limit:
            safety_parts: list[dict[str, object]] = [
                {
                    "type": "text",
                    "text": "Tool loop exceeded safety limits.",
                }
            ]
            output_messages.append(
                AgentMessage(
                    role="assistant",
                    parts=safety_parts,
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
            _ = await self._action_tool_run_service.complete_tool_run(
                tool_run_id=started.id, result=result
            )
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
            _ = await self._action_tool_run_service.fail_tool_run(
                tool_run_id=started.id, error=str(exc)
            )
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

    async def _execute_tool(
        self, *, tool: ToolDefinition, args: dict[str, object]
    ) -> dict[str, object]:
        if (
            self._session is not None
            and "session" in signature(tool.execute).parameters
        ):
            return await tool.execute(session=self._session, **args)
        return await tool.execute(**args)


def create_default_llm_client() -> LLMClientProtocol:
    api_key = (
        settings.llm_api_key.get_secret_value()
        if settings.llm_api_key is not None
        else ""
    )
    if api_key:
        return OpenAICompatibleLLMClient(
            model=settings.llm_model,
            api_key=api_key,
            base_url=settings.llm_base_url,
            system_prompt=settings.llm_system_prompt,
        )
    return RuleBasedLLMClient()


def _to_openai_chat_messages(
    *, messages: list[dict[str, object]], system_prompt: str
) -> list[dict[str, Any]]:
    llm_messages: list[dict[str, Any]] = []
    if system_prompt.strip():
        llm_messages.append({"role": "system", "content": system_prompt})

    for message in messages:
        role = message.get("role")
        if not isinstance(role, str):
            continue
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"

        parts = message.get("parts")
        if not isinstance(parts, list):
            continue

        text_parts: list[str] = []
        tool_calls_out: list[dict[str, Any]] = []
        tool_results_out: list[dict[str, Any]] = []

        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text)
                continue

            if part_type == "tool-call":
                tool_name = part.get("toolName")
                if not isinstance(tool_name, str) or not tool_name:
                    continue
                tool_call_id = part.get("toolCallId")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    continue
                args = part.get("args")
                args_obj = args if isinstance(args, dict) else {}
                tool_calls_out.append(
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(args_obj),
                        },
                    }
                )
                continue

            if part_type == "tool-result":
                tool_call_id = part.get("toolCallId")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    continue
                result = part.get("result")
                rendered_result = (
                    result if isinstance(result, dict) else {"value": result}
                )
                tool_results_out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(rendered_result),
                    }
                )

        if role == "tool":
            llm_messages.extend(tool_results_out)
            continue

        content_text = "\n".join(text_parts)
        if not content_text and not tool_calls_out:
            continue

        message_payload: dict[str, Any] = {
            "role": role,
            "content": content_text,
        }
        if tool_calls_out:
            message_payload["tool_calls"] = tool_calls_out
        llm_messages.append(message_payload)

    return llm_messages


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
    return [
        text[index : index + chunk_size] for index in range(0, len(text), chunk_size)
    ]


def _extract_demo_flag_args(text: str) -> tuple[str, object]:
    match = re.search(
        r"demo\s+flag\s+([a-zA-Z0-9_.-]+)\s*=\s*(.+)$", text, flags=re.IGNORECASE
    )
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
