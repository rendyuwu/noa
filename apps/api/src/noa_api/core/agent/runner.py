from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from inspect import signature
from typing import Any, Awaitable, Callable, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.config import settings
from noa_api.core.json_safety import json_safe
from noa_api.core.prompts.loader import load_system_prompt
from noa_api.core.tool_error_sanitizer import SanitizedToolError, sanitize_tool_error
from noa_api.core.tools.argument_validation import validate_tool_arguments
from noa_api.core.tools.registry import (
    ToolDefinition,
    get_tool_definition,
    get_tool_registry,
)
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ToolRisk
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.server_ref import resolve_whm_server_ref


logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class ProcessedToolCall:
    messages: list[AgentMessage]
    should_stop: bool = False


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
        # Expose the full tool catalog to the LLM so it can propose tool calls
        # even when the current user is not allowlisted for them.
        #
        # Execution is still gated by `available_tool_names` below; denied calls
        # emit an explicit user-facing message.
        llm_tools = [_to_openai_tool_schema(tool) for tool in get_tool_registry()]
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

            saw_denied_tool_call = False
            saw_allowed_tool_call = False
            for tool_call in llm_response.tool_calls:
                if tool_calls_processed >= max_tool_calls:
                    hit_safety_limit = True
                    break

                tool_calls_processed += 1
                internal_tool_guidance = _internal_tool_guidance(tool_call.name)
                if internal_tool_guidance is not None:
                    working_messages.append(
                        {
                            "role": "assistant",
                            "parts": [
                                {
                                    "type": "text",
                                    "text": internal_tool_guidance,
                                }
                            ],
                        }
                    )
                    continue

                tool = get_tool_definition(tool_call.name)
                if tool is None or tool.name not in available_tool_names:
                    saw_denied_tool_call = True
                    unavailable_part: dict[str, object] = {
                        "type": "text",
                        "text": (
                            f"You don't have permission to use tool '{tool_call.name}'. "
                            "Please ask SimondayCE Team to enable tool access for your account."
                        ),
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

                saw_allowed_tool_call = True
                processed = await self._process_tool_call(
                    tool=tool,
                    args=tool_call.arguments,
                    working_messages=working_messages,
                    thread_id=thread_id,
                    requested_by_user_id=requested_by_user_id,
                )
                tool_messages = processed.messages
                output_messages.extend(tool_messages)
                for message in tool_messages:
                    working_messages.append(
                        {
                            "role": message.role,
                            "parts": message.parts,
                        }
                    )

                if processed.should_stop:
                    return AgentRunnerResult(
                        messages=output_messages, text_deltas=text_deltas
                    )

            # If the LLM proposed only tools the user cannot access, stop the turn
            # after emitting explicit permission guidance instead of looping again.
            if (
                saw_denied_tool_call
                and not saw_allowed_tool_call
                and not hit_safety_limit
            ):
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
        working_messages: list[dict[str, object]],
        thread_id: UUID,
        requested_by_user_id: UUID,
    ) -> ProcessedToolCall:
        if tool.risk == ToolRisk.CHANGE:
            validation_error = await self._validate_tool_call(
                tool=tool,
                args=args,
                working_messages=working_messages,
            )
            if validation_error is not None:
                tool_call_id = f"invalid-{uuid4()}"
                return ProcessedToolCall(
                    messages=_tool_error_messages(
                        tool=tool,
                        args=args,
                        tool_call_id=tool_call_id,
                        error=validation_error,
                    )
                )

            action_request = await self._action_tool_run_service.create_action_request(
                thread_id=thread_id,
                tool_name=tool.name,
                args=args,
                risk=tool.risk,
                requested_by_user_id=requested_by_user_id,
            )
            return ProcessedToolCall(
                messages=[
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
                                    **_build_approval_context(
                                        tool_name=tool.name,
                                        args=args,
                                        working_messages=working_messages,
                                    ),
                                },
                            }
                        ],
                    ),
                ],
                should_stop=True,
            )

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
            validate_tool_arguments(tool=tool, args=args)
            result = await self._execute_tool(
                tool=tool,
                args=args,
                thread_id=thread_id,
                requested_by_user_id=requested_by_user_id,
            )
            safe_result = json_safe(result)
            result_payload = (
                safe_result if isinstance(safe_result, dict) else {"value": safe_result}
            )
            _ = await self._action_tool_run_service.complete_tool_run(
                tool_run_id=started.id, result=result_payload
            )
            result_message = AgentMessage(
                role="tool",
                parts=[
                    {
                        "type": "tool-result",
                        "toolName": tool.name,
                        "toolCallId": tool_call_id,
                        "result": result_payload,
                        "isError": False,
                    }
                ],
            )
            return ProcessedToolCall(messages=[call_message, result_message])
        except Exception as exc:
            sanitized_error = sanitize_tool_error(exc)
            logger.exception(
                "Agent tool execution failed (tool_name=%s thread_id=%s tool_run_id=%s requested_by_user_id=%s error_code=%s)",
                tool.name,
                thread_id,
                started.id,
                requested_by_user_id,
                sanitized_error.error_code,
            )
            _ = await self._action_tool_run_service.fail_tool_run(
                tool_run_id=started.id,
                error=sanitized_error.error_code,
            )
            error_message = AgentMessage(
                role="tool",
                parts=[
                    {
                        "type": "tool-result",
                        "toolName": tool.name,
                        "toolCallId": tool_call_id,
                        "result": sanitized_error.as_result(),
                        "isError": True,
                    }
                ],
            )
            return ProcessedToolCall(messages=[call_message, error_message])

    async def _validate_tool_call(
        self,
        *,
        tool: ToolDefinition,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
    ) -> SanitizedToolError | None:
        try:
            validate_tool_arguments(tool=tool, args=args)
        except Exception as exc:
            return sanitize_tool_error(exc)
        if tool.risk == ToolRisk.CHANGE:
            requested_server_id = await _resolve_requested_server_id(
                args=args,
                session=self._session,
            )
            preflight_error = _require_matching_preflight(
                tool_name=tool.name,
                args=args,
                working_messages=working_messages,
                requested_server_id=requested_server_id,
            )
            if preflight_error is not None:
                return preflight_error
        return None

    async def _execute_tool(
        self,
        *,
        tool: ToolDefinition,
        args: dict[str, object],
        thread_id: UUID,
        requested_by_user_id: UUID,
    ) -> dict[str, object]:
        execute_parameters = signature(tool.execute).parameters
        execute_kwargs: dict[str, object] = dict(args)
        if self._session is not None and "session" in execute_parameters:
            execute_kwargs["session"] = self._session
        if "thread_id" in execute_parameters:
            execute_kwargs["thread_id"] = thread_id
        if "requested_by_user_id" in execute_parameters:
            execute_kwargs["requested_by_user_id"] = requested_by_user_id

        if execute_kwargs is not args:
            return await tool.execute(**execute_kwargs)
        return await tool.execute(**args)


def create_default_llm_client() -> LLMClientProtocol:
    api_key = (
        settings.llm_api_key.get_secret_value()
        if settings.llm_api_key is not None
        else ""
    )
    if api_key:
        prompt = load_system_prompt(settings)
        return OpenAICompatibleLLMClient(
            model=settings.llm_model,
            api_key=api_key,
            base_url=settings.llm_base_url,
            system_prompt=prompt.text,
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


def _humanize_tool_name(tool_name: str) -> str:
    return (
        " ".join(part.capitalize() for part in tool_name.split("_") if part.strip())
        or "Tool"
    )


def _coerce_part_record(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def _messages_since_last_user(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    last_user_index = -1
    for index, message in enumerate(working_messages):
        if message.get("role") == "user":
            last_user_index = index
    return working_messages[last_user_index + 1 :]


def _collect_recent_preflight_evidence(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    tool_calls_by_id: dict[str, dict[str, object]] = {}
    evidence: list[dict[str, object]] = []

    for message in _messages_since_last_user(working_messages):
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for raw_part in parts:
            part = _coerce_part_record(raw_part)
            if part is None:
                continue

            part_type = part.get("type")
            tool_name = part.get("toolName")
            if not isinstance(tool_name, str) or not tool_name.startswith(
                "whm_preflight_"
            ):
                continue

            tool_call_id = part.get("toolCallId")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue

            if part_type == "tool-call":
                args = part.get("args")
                args_obj = args if isinstance(args, dict) else {}
                tool_calls_by_id[tool_call_id] = {
                    "toolName": tool_name,
                    "args": json_safe(args_obj),
                }
                continue

            if part_type != "tool-result" or part.get("isError") is True:
                continue

            result = part.get("result")
            if not isinstance(result, dict):
                continue

            call = tool_calls_by_id.get(tool_call_id, {})
            entry: dict[str, object] = {
                "toolName": tool_name,
                "result": json_safe(result),
            }
            call_args = call.get("args")
            if isinstance(call_args, dict):
                entry["args"] = call_args
            evidence.append(entry)

    return evidence


def _collect_recent_preflight_results(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            "toolName": item["toolName"],
            "result": item["result"],
        }
        for item in _collect_recent_preflight_evidence(working_messages)
    ]


def _require_matching_preflight(
    *,
    tool_name: str,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None = None,
) -> SanitizedToolError | None:
    if tool_name in {
        "whm_suspend_account",
        "whm_unsuspend_account",
        "whm_change_contact_email",
    }:
        return _require_account_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )

    if tool_name in {
        "whm_csf_unblock",
        "whm_csf_allowlist_remove",
        "whm_csf_allowlist_add_ttl",
        "whm_csf_denylist_add_ttl",
    }:
        return _require_csf_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )

    return None


def _require_account_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = _normalized_text(args.get("server_ref"))
    requested_username = _normalized_text(args.get("username"))
    if requested_server_ref is None or requested_username is None:
        return None

    evidence = [
        item
        for item in _collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_account"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required WHM preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_account with the same server_ref and username before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if _normalized_text(account.get("user")) == requested_username:
            return None

    return SanitizedToolError(
        error="Required WHM preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful whm_preflight_account was found for server_ref '{requested_server_ref}' and username '{requested_username}' in the current turn.",
        ),
    )


def _require_csf_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = _normalized_text(args.get("server_ref"))
    requested_targets = _normalized_string_list(args.get("targets"))
    if requested_server_ref is None or not requested_targets:
        return None

    evidence = [
        item
        for item in _collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_csf_entries"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required WHM preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_csf_entries for each target with the same server_ref before requesting this change.",
            ),
        )

    matched_targets: set[str] = set()
    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        target = _normalized_text(result.get("target"))
        if target is not None:
            matched_targets.add(target)

    missing_targets = [
        target for target in requested_targets if target not in matched_targets
    ]
    if not missing_targets:
        return None

    return SanitizedToolError(
        error="Required WHM preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            "Missing successful whm_preflight_csf_entries results for target(s): "
            + ", ".join(f"'{target}'" for target in missing_targets),
        ),
    )


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalized_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = _normalized_text(item)
        if text is not None:
            normalized.append(text)
    return normalized


async def _resolve_requested_server_id(
    *, args: dict[str, object], session: AsyncSession | None
) -> str | None:
    requested_server_ref = _normalized_text(args.get("server_ref"))
    if requested_server_ref is None or session is None:
        return None
    if not hasattr(session, "execute"):
        return None

    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(requested_server_ref, repo=repo)
    if not resolution.ok or resolution.server_id is None:
        return None
    return str(resolution.server_id)


def _server_identity_matches(
    *,
    item_args: dict[str, object],
    result: dict[str, object],
    requested_server_ref: str,
    requested_server_id: str | None,
) -> bool:
    result_server_id = _normalized_text(result.get("server_id"))
    if requested_server_id is not None and result_server_id is not None:
        return result_server_id == requested_server_id
    return _normalized_text(item_args.get("server_ref")) == requested_server_ref


def _format_argument_value(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if value is None:
        return "none"
    if isinstance(value, list):
        return ", ".join(_format_argument_value(item) for item in value[:5])
    return json.dumps(json_safe(value))


def _summarize_arguments(args: dict[str, object]) -> list[dict[str, str]]:
    hidden_keys = {"api_token", "password", "secret", "token"}
    items: list[dict[str, str]] = []
    for key, value in args.items():
        if key in hidden_keys:
            continue
        items.append(
            {
                "label": key.replace("_", " ").capitalize(),
                "value": _format_argument_value(value),
            }
        )
    return items


def _describe_activity(tool_name: str, args: dict[str, object]) -> str:
    if tool_name == "whm_unsuspend_account":
        return f"Unsuspend account '{args.get('username', 'unknown')}'"
    if tool_name == "whm_suspend_account":
        return f"Suspend account '{args.get('username', 'unknown')}'"
    if tool_name == "whm_change_contact_email":
        return (
            f"Change contact email for '{args.get('username', 'unknown')}' "
            f"to '{args.get('new_email', 'unknown')}'"
        )
    if tool_name == "whm_csf_unblock":
        return f"Remove CSF block for '{_format_argument_value(args.get('targets'))}'"
    if tool_name == "whm_csf_allowlist_add_ttl":
        return (
            f"Add '{_format_argument_value(args.get('targets'))}' to the CSF allowlist"
        )
    if tool_name == "whm_csf_allowlist_remove":
        return f"Remove '{_format_argument_value(args.get('targets'))}' from the CSF allowlist"
    if tool_name == "whm_csf_denylist_add_ttl":
        return (
            f"Add '{_format_argument_value(args.get('targets'))}' to the CSF denylist"
        )
    return _humanize_tool_name(tool_name)


def _extract_before_state(
    preflight_results: list[dict[str, object]],
) -> list[dict[str, str]]:
    before_state: list[dict[str, str]] = []
    for item in preflight_results:
        tool_name = item.get("toolName")
        result = item.get("result")
        if not isinstance(tool_name, str) or not isinstance(result, dict):
            continue
        if tool_name == "whm_preflight_account":
            account = result.get("account")
            if isinstance(account, dict):
                for key, label in (
                    ("user", "Username"),
                    ("domain", "Domain"),
                    ("contactemail", "Contact email"),
                    ("suspended", "Suspended"),
                    ("suspendreason", "Suspend reason"),
                    ("plan", "Plan"),
                ):
                    value = account.get(key)
                    if value in (None, ""):
                        continue
                    before_state.append(
                        {"label": label, "value": _format_argument_value(value)}
                    )
        if tool_name == "whm_preflight_csf_entries":
            verdict = result.get("verdict")
            target = result.get("target")
            if target not in (None, ""):
                before_state.append(
                    {"label": "Target", "value": _format_argument_value(target)}
                )
            if verdict not in (None, ""):
                before_state.append(
                    {
                        "label": "Current CSF state",
                        "value": _format_argument_value(verdict),
                    }
                )
            matches = result.get("matches")
            if isinstance(matches, list) and matches:
                before_state.append(
                    {
                        "label": "Matched entries",
                        "value": "; ".join(
                            _format_argument_value(match) for match in matches[:3]
                        ),
                    }
                )
    return before_state


def _build_approval_context(
    *,
    tool_name: str,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
) -> dict[str, object]:
    preflight_results = _collect_recent_preflight_results(working_messages)
    return {
        "activity": _describe_activity(tool_name, args),
        "argumentSummary": _summarize_arguments(args),
        "beforeState": _extract_before_state(preflight_results),
        "preflightResults": preflight_results,
    }


def _to_openai_tool_schema(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": _llm_tool_description(tool),
            "parameters": tool.parameters_schema,
        },
    }


def _llm_tool_description(tool: ToolDefinition) -> str:
    parts = [tool.description, _tool_risk_note(tool)]
    parts.extend(tool.prompt_hints)
    return " ".join(part.rstrip(".") + "." for part in parts if part)


def _tool_risk_note(tool: ToolDefinition) -> str:
    if tool.risk == ToolRisk.CHANGE:
        return "Risk: CHANGE. Requires persisted approval before execution"
    return "Risk: READ. Evidence-gathering only; it does not change system state"


def _tool_error_messages(
    *,
    tool: ToolDefinition,
    args: dict[str, object],
    tool_call_id: str,
    error: SanitizedToolError,
) -> list[AgentMessage]:
    return [
        AgentMessage(
            role="assistant",
            parts=[
                {
                    "type": "tool-call",
                    "toolName": tool.name,
                    "toolCallId": tool_call_id,
                    "args": args,
                }
            ],
        ),
        AgentMessage(
            role="tool",
            parts=[
                {
                    "type": "tool-result",
                    "toolName": tool.name,
                    "toolCallId": tool_call_id,
                    "result": error.as_result(),
                    "isError": True,
                }
            ],
        ),
    ]


def _internal_tool_guidance(tool_name: str) -> str | None:
    if tool_name == "request_approval":
        return (
            "Approval requests are created automatically after you call the "
            "underlying CHANGE tool. Do not call request_approval directly."
        )
    return None


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
