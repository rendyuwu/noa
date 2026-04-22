from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from inspect import signature
from typing import Any, Awaitable, Callable, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.config import Settings, get_required_llm_api_key
from noa_api.core.json_safety import json_safe
from noa_api.core.prompts.loader import load_system_prompt
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.core.tool_error_sanitizer import SanitizedToolError, sanitize_tool_error
from noa_api.core.tools.argument_validation import validate_tool_arguments
from noa_api.core.tools.registry import (
    ToolDefinition,
    get_tool_definition,
    get_tool_registry,
)
from noa_api.core.workflows.registry import (
    build_approval_context,
    build_workflow_reply_template,
    build_workflow_todos,
    collect_recent_preflight_evidence,
    describe_workflow_activity,
    infer_waiting_on_user_workflow_from_messages,
    persist_workflow_todos,
)
from noa_api.core.workflows.types import (
    assistant_is_requesting_reason,
    messages_before_latest_user_if_reason_follow_up,
    render_workflow_reply_text,
)
from noa_api.core.workflows.preflight_validation import (
    resolve_requested_server_id as _resolve_requested_server_id,
    validate_matching_preflight as _require_matching_preflight,
)
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ToolRisk


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class LLMTurnResponse:
    text: str
    tool_calls: list[LLMToolCall]
    reasoning: str = ""


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


def _workflow_todo_tool_messages(
    *, todos: list[dict[str, object]]
) -> list[AgentMessage]:
    tool_call_id = f"workflow-todo-{uuid4()}"
    return [
        AgentMessage(
            role="assistant",
            parts=[
                {
                    "type": "tool-call",
                    "toolName": "update_workflow_todo",
                    "toolCallId": tool_call_id,
                    "args": {"todos": todos},
                }
            ],
        ),
        AgentMessage(
            role="tool",
            parts=[
                {
                    "type": "tool-result",
                    "toolName": "update_workflow_todo",
                    "toolCallId": tool_call_id,
                    "result": {"ok": True, "todos": todos},
                    "isError": False,
                }
            ],
        ),
    ]


def _as_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _assistant_message_parts(*, reasoning: str, text: str) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    reasoning_text = _normalized_text(reasoning)
    if reasoning_text is not None:
        parts.append({"type": "reasoning", "summary": reasoning_text})

    visible_text = text.strip()
    if visible_text:
        parts.append({"type": "text", "text": visible_text})

    return parts


def _append_assistant_text_to_working_messages(
    working_messages: list[dict[str, object]], *, text: str
) -> None:
    assistant_parts = _assistant_message_parts(reasoning="", text=text)
    if not assistant_parts:
        return

    working_parts = _prompt_replay_parts(assistant_parts)
    if not working_parts:
        return

    working_messages.append(
        {
            "role": "assistant",
            "parts": working_parts,
        }
    )


def _append_assistant_text_to_output_messages(
    output_messages: list[AgentMessage], *, text: str
) -> None:
    assistant_parts = _assistant_message_parts(reasoning="", text=text)
    if not assistant_parts:
        return

    output_messages.append(
        AgentMessage(
            role="assistant",
            parts=assistant_parts,
        )
    )


def _should_persist_assistant_text_this_round(
    *, text: str, tool_calls: list[LLMToolCall]
) -> bool:
    if not text:
        return False

    if assistant_is_requesting_reason(text):
        return True

    return not tool_calls


def _should_suppress_provisional_assistant_text_this_round(
    *, text: str, tool_calls: list[LLMToolCall]
) -> bool:
    if not text or assistant_is_requesting_reason(text):
        return False

    for tool_call in tool_calls:
        if tool_call.name in {"request_approval", "update_workflow_todo"}:
            return True
        tool = get_tool_definition(tool_call.name)
        if tool is not None and tool.risk == ToolRisk.CHANGE:
            return True

    return False


def _message_visible_text(parts: list[dict[str, object]]) -> str | None:
    visible_parts: list[str] = []
    for part in parts:
        part_dict = _as_object_dict(part)
        if part_dict is None or part_dict.get("type") != "text":
            continue
        text = part_dict.get("text")
        if isinstance(text, str) and text:
            visible_parts.append(text)

    if not visible_parts:
        return None
    return "".join(visible_parts)


def _render_workflow_milestone_text(title: str, summary: str) -> str:
    normalized_title = title.strip()
    normalized_summary = summary.strip()
    if not normalized_title:
        return normalized_summary
    if not normalized_summary:
        return normalized_title
    separator = "" if normalized_title.endswith((".", "!", "?")) else "."
    return f"{normalized_title}{separator} {normalized_summary}"


def _finalize_turn_messages(
    *,
    messages: list[AgentMessage],
    reasoning: str,
) -> list[AgentMessage]:
    reasoning_text = _normalized_text(reasoning)
    if reasoning_text is None:
        return messages

    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "assistant":
            continue
        visible_text = _message_visible_text(message.parts)
        if visible_text is None:
            continue
        updated_message = AgentMessage(
            role="assistant",
            parts=_assistant_message_parts(
                reasoning=reasoning_text,
                text=visible_text,
            ),
        )
        return [*messages[:index], updated_message, *messages[index + 1 :]]

    return [
        *messages,
        AgentMessage(
            role="assistant",
            parts=[{"type": "reasoning", "summary": reasoning_text}],
        ),
    ]


def _prompt_replay_parts(parts: list[dict[str, object]]) -> list[dict[str, object]]:
    replay_parts: list[dict[str, object]] = []
    for part in parts:
        part_dict = _as_object_dict(part)
        if part_dict is None:
            continue
        if part_dict.get("type") == "reasoning":
            continue
        replay_parts.append(part_dict)
    return replay_parts


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
            reasoning = _extract_reasoning_summary(getattr(choice, "reasoning", None))
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

            return LLMTurnResponse(
                text=text, tool_calls=tool_calls, reasoning=reasoning
            )

        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "messages": cast(Any, llm_messages),
            "stream": True,
        }
        if tools:
            request_kwargs["tools"] = cast(Any, tools)
            request_kwargs["tool_choice"] = "auto"

        stream: Any = await self._client.chat.completions.create(
            **cast(Any, request_kwargs)
        )

        text_chunks: list[str] = []
        reasoning_chunks: list[str] = []
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

            reasoning = _extract_reasoning_summary(
                getattr(delta, "reasoning", None),
                preserve_whitespace=True,
            )
            if reasoning:
                reasoning_chunks.append(reasoning)

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
        reasoning = "".join(reasoning_chunks).strip()
        tool_calls: list[LLMToolCall] = []
        for _, value in sorted(tool_call_acc.items()):
            name = value.get("name")
            if not name:
                continue
            args = _safe_json_object(value.get("arguments"))
            tool_calls.append(LLMToolCall(name=name, arguments=args))

        return LLMTurnResponse(text=text, tool_calls=tool_calls, reasoning=reasoning)


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
        llm_tools = [
            _to_openai_tool_schema(tool)
            for tool in get_tool_registry()
            if tool.name != "update_workflow_todo"
        ]
        max_rounds = 6
        max_tool_calls = 8

        working_messages = list(thread_messages)
        output_messages: list[AgentMessage] = []
        text_deltas: list[str] = []
        pending_firewall_preflight_raw: list[tuple[str, str]] = []
        rounds = 0
        tool_calls_processed = 0
        hit_safety_limit = False
        internal_guidance_counts: dict[str, int] = {}
        preflight_guidance_counts: dict[str, int] = {}
        reasoning_chunks: list[str] = []
        while rounds < max_rounds and tool_calls_processed < max_tool_calls:
            rounds += 1
            round_text_chunks: list[str] = []

            async def _collect_round_text_delta(delta: str) -> None:
                round_text_chunks.append(delta)

            if on_text_delta is None:
                llm_response = await self._llm_client.run_turn(
                    messages=working_messages,
                    tools=llm_tools,
                )
            else:
                llm_response = await self._llm_client.run_turn(
                    messages=working_messages,
                    tools=llm_tools,
                    on_text_delta=_collect_round_text_delta,
                )

            text = llm_response.text.strip()
            reasoning = _normalized_text(llm_response.reasoning)
            if reasoning:
                reasoning_chunks.append(reasoning)
            if text and pending_firewall_preflight_raw:
                text = _append_firewall_preflight_raw_output(
                    text, pending_firewall_preflight_raw
                )
                pending_firewall_preflight_raw.clear()

            if text:
                suppress_provisional_text = (
                    _should_suppress_provisional_assistant_text_this_round(
                        text=text,
                        tool_calls=llm_response.tool_calls,
                    )
                )
                if not suppress_provisional_text:
                    _append_assistant_text_to_working_messages(
                        working_messages,
                        text=text,
                    )
                    text_deltas.extend(_split_text_deltas(text))
                    if on_text_delta is not None:
                        for chunk in round_text_chunks:
                            await on_text_delta(chunk)

                if _should_persist_assistant_text_this_round(
                    text=text,
                    tool_calls=llm_response.tool_calls,
                ):
                    _append_assistant_text_to_output_messages(
                        output_messages,
                        text=text,
                    )

                if assistant_is_requesting_reason(text):
                    workflow_messages = await self._maybe_persist_waiting_on_user_workflow_from_text_turn(
                        assistant_text=text,
                        working_messages=working_messages,
                        thread_id=thread_id,
                    )
                    if workflow_messages:
                        output_messages.extend(workflow_messages)
                        for message in workflow_messages:
                            working_messages.append(
                                {
                                    "role": message.role,
                                    "parts": message.parts,
                                }
                            )

            if not llm_response.tool_calls:
                if not text:
                    aggregated_reasoning = "\n\n".join(reasoning_chunks).strip()
                    if aggregated_reasoning:
                        return AgentRunnerResult(
                            messages=_finalize_turn_messages(
                                messages=output_messages,
                                reasoning=aggregated_reasoning,
                            ),
                            text_deltas=text_deltas,
                        )
                    followup_guidance = _post_tool_followup_guidance(working_messages)
                    if followup_guidance is not None and not any(
                        _message_has_text(message, followup_guidance)
                        for message in working_messages
                    ):
                        working_messages.append(
                            {
                                "role": "assistant",
                                "parts": [
                                    {
                                        "type": "text",
                                        "text": followup_guidance,
                                    }
                                ],
                            }
                        )
                        continue

                    fallback_text = _fallback_assistant_reply_from_recent_tool_result(
                        working_messages
                    )
                    if fallback_text:
                        fallback_parts: list[dict[str, object]] = [
                            {
                                "type": "text",
                                "text": fallback_text,
                            }
                        ]
                        output_messages.append(
                            AgentMessage(role="assistant", parts=fallback_parts)
                        )
                        working_messages.append(
                            {
                                "role": "assistant",
                                "parts": fallback_parts,
                            }
                        )
                        text_deltas.extend(_split_text_deltas(fallback_text))
                        if on_text_delta is not None:
                            for chunk in _split_text_deltas(fallback_text):
                                await on_text_delta(chunk)

                return AgentRunnerResult(
                    messages=_finalize_turn_messages(
                        messages=output_messages,
                        reasoning="\n\n".join(reasoning_chunks),
                    ),
                    text_deltas=text_deltas,
                )

            saw_denied_tool_call = False
            saw_allowed_tool_call = False
            saw_internal_guidance_stop = False
            for tool_call in llm_response.tool_calls:
                tool = get_tool_definition(tool_call.name)
                tool_call_args = tool_call.arguments
                if tool is not None and tool.risk == ToolRisk.CHANGE:
                    tool_call_args = _canonicalize_reason_follow_up_args(
                        tool=tool,
                        args=tool_call_args,
                        working_messages=working_messages,
                    )

                duplicate_failed_result = _latest_matching_failed_tool_result_part(
                    working_messages=working_messages,
                    tool_name=tool_call.name,
                    args=tool_call_args,
                )
                if duplicate_failed_result is not None:
                    guidance_text = _preflight_retry_guidance(duplicate_failed_result)
                    if guidance_text is not None and tool is not None:
                        requested_server_id = None
                        if tool.risk == ToolRisk.CHANGE:
                            requested_server_id = await _resolve_requested_server_id(
                                args=tool_call_args,
                                session=self._session,
                            )
                        if _has_fresh_matching_preflight_after_failed_tool_result(
                            tool_name=tool_call.name,
                            args=tool_call_args,
                            working_messages=working_messages,
                            failed_tool_result_part=duplicate_failed_result,
                            requested_server_id=requested_server_id,
                        ):
                            duplicate_failed_result = None
                if duplicate_failed_result is not None:
                    guidance_text = _preflight_retry_guidance(duplicate_failed_result)
                    if guidance_text is not None:
                        preflight_guidance_key = (
                            f"{tool_call.name}:{_canonical_tool_args(tool_call_args)}"
                        )
                        preflight_guidance_counts[preflight_guidance_key] = (
                            preflight_guidance_counts.get(preflight_guidance_key, 0) + 1
                        )
                        if preflight_guidance_counts[preflight_guidance_key] >= 2:
                            fallback_text = _assistant_reply_from_tool_result_part(
                                working_messages=working_messages,
                                tool_result_part=duplicate_failed_result,
                            )
                            if fallback_text is None:
                                fallback_text = _preflight_user_retry_reply(
                                    working_messages=working_messages,
                                    tool_result_part=duplicate_failed_result,
                                )
                            fallback_parts: list[dict[str, object]] = [
                                {"type": "text", "text": fallback_text}
                            ]
                            output_messages.append(
                                AgentMessage(role="assistant", parts=fallback_parts)
                            )
                            working_messages.append(
                                {
                                    "role": "assistant",
                                    "parts": fallback_parts,
                                }
                            )
                            text_deltas.extend(_split_text_deltas(fallback_text))
                            if on_text_delta is not None:
                                for chunk in _split_text_deltas(fallback_text):
                                    await on_text_delta(chunk)
                            saw_internal_guidance_stop = True
                            break
                        working_messages.append(
                            {
                                "role": "assistant",
                                "parts": [{"type": "text", "text": guidance_text}],
                            }
                        )
                        continue

                    fallback_text = _assistant_reply_from_tool_result_part(
                        working_messages=working_messages,
                        tool_result_part=duplicate_failed_result,
                    )
                    if fallback_text is None:
                        fallback_text = (
                            f"The tool '{tool_call.name}' already failed with these exact arguments. "
                            "Please review the existing error before retrying."
                        )
                    fallback_parts: list[dict[str, object]] = [
                        {"type": "text", "text": fallback_text}
                    ]
                    output_messages.append(
                        AgentMessage(role="assistant", parts=fallback_parts)
                    )
                    working_messages.append(
                        {
                            "role": "assistant",
                            "parts": fallback_parts,
                        }
                    )
                    text_deltas.extend(_split_text_deltas(fallback_text))
                    if on_text_delta is not None:
                        for chunk in _split_text_deltas(fallback_text):
                            await on_text_delta(chunk)
                    saw_internal_guidance_stop = True
                    break

                if tool_calls_processed >= max_tool_calls:
                    hit_safety_limit = True
                    break

                tool_calls_processed += 1
                internal_tool_guidance = _internal_tool_guidance(tool_call.name)
                if internal_tool_guidance is not None:
                    internal_guidance_counts[tool_call.name] = (
                        internal_guidance_counts.get(tool_call.name, 0) + 1
                    )
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
                    if _should_stop_after_internal_tool_guidance(
                        tool_call.name,
                        internal_guidance_counts.get(tool_call.name, 0),
                    ):
                        output_messages.append(
                            AgentMessage(
                                role="assistant",
                                parts=[
                                    {
                                        "type": "text",
                                        "text": internal_tool_guidance,
                                    }
                                ],
                            )
                        )
                        saw_internal_guidance_stop = True
                        break
                    continue

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

                if tool.risk == ToolRisk.CHANGE and pending_firewall_preflight_raw:
                    raw_text = _render_firewall_preflight_raw_output(
                        pending_firewall_preflight_raw
                    )
                    pending_firewall_preflight_raw.clear()
                    raw_parts: list[dict[str, object]] = [
                        {"type": "text", "text": raw_text}
                    ]
                    output_messages.append(
                        AgentMessage(role="assistant", parts=raw_parts)
                    )
                    working_messages.append(
                        {
                            "role": "assistant",
                            "parts": raw_parts,
                        }
                    )
                    text_deltas.extend(_split_text_deltas(raw_text))
                    if on_text_delta is not None:
                        for chunk in _split_text_deltas(raw_text):
                            await on_text_delta(chunk)

                processed = await self._process_tool_call(
                    tool=tool,
                    args=tool_call_args,
                    working_messages=working_messages,
                    thread_id=thread_id,
                    requested_by_user_id=requested_by_user_id,
                )
                tool_messages = processed.messages
                output_messages.extend(tool_messages)

                pending_firewall_preflight_raw.extend(
                    _extract_firewall_preflight_raw_outputs(tool_messages)
                )
                for message in tool_messages:
                    working_messages.append(
                        {
                            "role": message.role,
                            "parts": message.parts,
                        }
                    )

                if processed.should_stop:
                    return AgentRunnerResult(
                        messages=_finalize_turn_messages(
                            messages=output_messages,
                            reasoning="\n\n".join(reasoning_chunks),
                        ),
                        text_deltas=text_deltas,
                    )

            if saw_internal_guidance_stop:
                return AgentRunnerResult(
                    messages=_finalize_turn_messages(
                        messages=output_messages,
                        reasoning="\n\n".join(reasoning_chunks),
                    ),
                    text_deltas=text_deltas,
                )

            # If the LLM proposed only tools the user cannot access, stop the turn
            # after emitting explicit permission guidance instead of looping again.
            if (
                saw_denied_tool_call
                and not saw_allowed_tool_call
                and not hit_safety_limit
            ):
                return AgentRunnerResult(
                    messages=_finalize_turn_messages(
                        messages=output_messages,
                        reasoning="\n\n".join(reasoning_chunks),
                    ),
                    text_deltas=text_deltas,
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
        final_messages = _finalize_turn_messages(
            messages=output_messages,
            reasoning="\n\n".join(reasoning_chunks),
        )

        if hit_safety_limit:
            final_messages = [
                *final_messages,
                AgentMessage(
                    role="assistant",
                    parts=safety_parts,
                ),
            ]

        return AgentRunnerResult(
            messages=final_messages,
            text_deltas=text_deltas,
        )

    async def _process_tool_call(
        self,
        *,
        tool: ToolDefinition,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        thread_id: UUID,
        requested_by_user_id: UUID,
    ) -> ProcessedToolCall:
        args = _canonicalize_reason_follow_up_args(
            tool=tool,
            args=args,
            working_messages=working_messages,
        )
        if tool.risk == ToolRisk.CHANGE:
            validation_error = await self._validate_tool_call(
                tool=tool,
                args=args,
                working_messages=working_messages,
            )
            if validation_error is None:
                validation_error = _validate_change_reason_provenance(
                    tool=tool,
                    args=args,
                    working_messages=working_messages,
                )
            if validation_error is not None:
                tool_call_id = f"invalid-{uuid4()}"
                workflow_messages = (
                    await self._persist_waiting_on_user_workflow_if_reason_missing(
                        tool=tool,
                        args=args,
                        working_messages=working_messages,
                        thread_id=thread_id,
                        validation_error=validation_error,
                    )
                )
                message_args = dict(args)
                if _is_reason_provenance_error(validation_error):
                    message_args.pop("reason", None)
                messages = _tool_error_messages(
                    tool=tool,
                    args=message_args,
                    tool_call_id=tool_call_id,
                    error=validation_error,
                )
                if workflow_messages:
                    messages = [*workflow_messages, *messages]
                assistant_guidance = _assistant_guidance_for_change_validation_error(
                    tool_name=tool.name,
                    args=args,
                    error=validation_error,
                )
                if assistant_guidance is not None:
                    messages.append(
                        AgentMessage(
                            role="assistant",
                            parts=[{"type": "text", "text": assistant_guidance}],
                        )
                    )
                return ProcessedToolCall(
                    messages=messages,
                    should_stop=assistant_guidance is not None,
                )

            action_request = await self._action_tool_run_service.create_action_request(
                thread_id=thread_id,
                tool_name=tool.name,
                args=args,
                risk=tool.risk,
                requested_by_user_id=requested_by_user_id,
            )
            preflight_evidence = collect_recent_preflight_evidence(working_messages)
            workflow_todos = build_workflow_todos(
                tool_name=tool.name,
                workflow_family=tool.workflow_family,
                args=args,
                phase="waiting_on_approval",
                preflight_evidence=preflight_evidence,
            )
            await persist_workflow_todos(
                session=self._session,
                thread_id=thread_id,
                todos=workflow_todos,
            )
            workflow_todo_messages = (
                _workflow_todo_tool_messages(
                    todos=cast(list[dict[str, object]], workflow_todos)
                )
                if isinstance(workflow_todos, list)
                else []
            )
            redacted_args = redact_sensitive_data(args)
            safe_message_args = (
                redacted_args
                if isinstance(redacted_args, dict)
                else {"value": redacted_args}
            )
            approval_context = _build_approval_context(
                tool_name=tool.name,
                args=args,
                working_messages=working_messages,
            )
            reply_template = build_workflow_reply_template(
                tool_name=tool.name,
                workflow_family=tool.workflow_family,
                args=args,
                phase="waiting_on_approval",
                preflight_evidence=preflight_evidence,
            )
            messages: list[AgentMessage] = []
            if reply_template is not None:
                reply_text = render_workflow_reply_text(reply_template).strip()
                if reply_text:
                    messages.append(
                        AgentMessage(
                            role="assistant",
                            parts=[{"type": "text", "text": reply_text}],
                        )
                    )
            return ProcessedToolCall(
                messages=[
                    *messages,
                    AgentMessage(
                        role="assistant",
                        parts=[
                            {
                                "type": "tool-call",
                                "toolName": tool.name,
                                "toolCallId": f"proposal-{action_request.id}",
                                "args": safe_message_args,
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
                                    "arguments": safe_message_args,
                                    **approval_context,
                                },
                            }
                        ],
                    ),
                    *workflow_todo_messages,
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
        redacted_args = redact_sensitive_data(args)
        safe_message_args = (
            redacted_args
            if isinstance(redacted_args, dict)
            else {"value": redacted_args}
        )
        call_message = AgentMessage(
            role="assistant",
            parts=[
                {
                    "type": "tool-call",
                    "toolName": tool.name,
                    "toolCallId": tool_call_id,
                    "args": safe_message_args,
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

    async def _persist_waiting_on_user_workflow_if_reason_missing(
        self,
        *,
        tool: ToolDefinition,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        thread_id: UUID,
        validation_error: SanitizedToolError | None = None,
    ) -> list[AgentMessage]:
        if tool.risk != ToolRisk.CHANGE or tool.workflow_family is None:
            return []
        reason = _normalized_text(args.get("reason"))
        if reason is not None and not _is_reason_provenance_error(validation_error):
            return []

        workflow_args = dict(args)
        if _is_reason_provenance_error(validation_error):
            workflow_args.pop("reason", None)

        workflow_todos = build_workflow_todos(
            tool_name=tool.name,
            workflow_family=tool.workflow_family,
            args=workflow_args,
            phase="waiting_on_user",
            preflight_evidence=collect_recent_preflight_evidence(working_messages),
        )
        await persist_workflow_todos(
            session=self._session,
            thread_id=thread_id,
            todos=workflow_todos,
        )
        if not isinstance(workflow_todos, list):
            return []
        return _workflow_todo_tool_messages(
            todos=cast(list[dict[str, object]], workflow_todos)
        )

    async def _maybe_persist_waiting_on_user_workflow_from_text_turn(
        self,
        *,
        assistant_text: str,
        working_messages: list[dict[str, object]],
        thread_id: UUID,
    ) -> list[AgentMessage]:
        if not assistant_is_requesting_reason(assistant_text):
            return []

        inferred = _infer_waiting_on_user_workflow_from_messages(
            working_messages,
            assistant_text=assistant_text,
        )
        if inferred is None:
            return []

        tool_name = cast(str, inferred["tool_name"])
        args = cast(dict[str, object], inferred["args"])
        tool = get_tool_definition(tool_name)
        if tool is None or tool.workflow_family is None:
            return []

        workflow_todos = build_workflow_todos(
            tool_name=tool.name,
            workflow_family=tool.workflow_family,
            args=args,
            phase="waiting_on_user",
            preflight_evidence=collect_recent_preflight_evidence(working_messages),
        )
        await persist_workflow_todos(
            session=self._session,
            thread_id=thread_id,
            todos=workflow_todos,
        )
        if not isinstance(workflow_todos, list):
            return []
        return _workflow_todo_tool_messages(
            todos=cast(list[dict[str, object]], workflow_todos)
        )

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


def create_default_llm_client(app_settings: Settings) -> LLMClientProtocol:
    api_key = get_required_llm_api_key(app_settings)

    prompt = load_system_prompt(app_settings)
    return OpenAICompatibleLLMClient(
        model=app_settings.llm_model,
        api_key=api_key,
        base_url=app_settings.llm_base_url,
        system_prompt=prompt.text,
    )


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
            part_dict = _as_object_dict(part)
            if part_dict is None:
                continue
            part_type = part_dict.get("type")
            if part_type == "text":
                text = part_dict.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text)
                continue

            if part_type == "reasoning":
                continue

            if part_type == "tool-call":
                tool_name = part_dict.get("toolName")
                if not isinstance(tool_name, str) or not tool_name:
                    continue
                tool_call_id = part_dict.get("toolCallId")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    continue
                args = part_dict.get("args")
                args_obj = _as_object_dict(args) or {}
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
                tool_call_id = part_dict.get("toolCallId")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    continue
                result = part_dict.get("result")
                rendered_result = _as_object_dict(result) or {"value": result}
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
    parsed_dict = _as_object_dict(parsed)
    if parsed_dict is not None:
        return parsed_dict
    return {}


def _extract_reasoning_summary(
    value: object, *, preserve_whitespace: bool = False
) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value if preserve_whitespace else value.strip()
    if isinstance(value, (list, tuple)):
        summary_parts = [
            _extract_reasoning_summary(
                item,
                preserve_whitespace=preserve_whitespace,
            )
            for item in value
        ]
        if preserve_whitespace:
            return "".join(part for part in summary_parts if part)
        return " ".join(part for part in summary_parts if part).strip()

    value_dict = _as_object_dict(value)
    if value_dict is not None:
        summary_value = value_dict.get("summary")
        if summary_value is None:
            return ""
        return _extract_reasoning_summary(
            summary_value,
            preserve_whitespace=preserve_whitespace,
        )

    summary_value = getattr(value, "summary", None)

    if summary_value is None:
        return ""
    return _extract_reasoning_summary(
        summary_value,
        preserve_whitespace=preserve_whitespace,
    )


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _reason_provenance_tokens(value: object) -> list[str]:
    normalized = _normalized_text(value)
    if normalized is None:
        return []
    folded = unicodedata.normalize("NFKC", normalized).casefold()
    tokens: list[str] = []
    current: list[str] = []

    for char in folded:
        if char.isalnum():
            current.append(char)
            continue
        if char == "-" and current:
            current.append(char)
            continue
        if current:
            token = "".join(current).strip("-")
            if token:
                tokens.append(token)
            current = []

    if current:
        token = "".join(current).strip("-")
        if token:
            tokens.append(token)

    return tokens


def _reason_tokens_are_explicit_in_latest_user_turn(
    *,
    reason_tokens: list[str],
    latest_user_tokens: list[str],
) -> bool:
    reason_length = len(reason_tokens)
    if reason_length == 0 or reason_length > len(latest_user_tokens):
        return False

    for start in range(len(latest_user_tokens) - reason_length + 1):
        if latest_user_tokens[start : start + reason_length] == reason_tokens:
            return True

    return False


def _is_reason_provenance_error(error: SanitizedToolError | None) -> bool:
    if error is None or error.error_code != "invalid_tool_arguments":
        return False
    return any(
        detail == "Reason must be explicit in the latest user turn."
        for detail in (error.details or ())
    )


def _latest_user_message_text(
    working_messages: list[dict[str, object]],
) -> str | None:
    for message in reversed(working_messages):
        if message.get("role") != "user":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        text_parts: list[str] = []
        for part in parts:
            part_dict = _as_object_dict(part)
            if part_dict is None or part_dict.get("type") != "text":
                continue
            text = _normalized_text(part_dict.get("text"))
            if text is not None:
                text_parts.append(text)
        if text_parts:
            return " ".join(text_parts)
    return None


def _validate_change_reason_provenance(
    *,
    tool: ToolDefinition,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
) -> SanitizedToolError | None:
    if tool.risk != ToolRisk.CHANGE:
        return None

    reason = _normalized_text(args.get("reason"))
    if reason is None:
        return None

    latest_user_text = _latest_user_message_text(working_messages)
    if latest_user_text is None:
        return SanitizedToolError(
            error="Tool arguments are invalid",
            error_code="invalid_tool_arguments",
            details=("Reason must be explicit in the latest user turn.",),
        )

    reason_tokens = _reason_provenance_tokens(reason)
    latest_user_tokens = _reason_provenance_tokens(latest_user_text)
    if not reason_tokens or not latest_user_tokens:
        return SanitizedToolError(
            error="Tool arguments are invalid",
            error_code="invalid_tool_arguments",
            details=("Reason must be explicit in the latest user turn.",),
        )

    if _reason_tokens_are_explicit_in_latest_user_turn(
        reason_tokens=reason_tokens,
        latest_user_tokens=latest_user_tokens,
    ):
        return None

    return SanitizedToolError(
        error="Tool arguments are invalid",
        error_code="invalid_tool_arguments",
        details=("Reason must be explicit in the latest user turn.",),
    )


def _canonicalize_reason_follow_up_args(
    *,
    tool: ToolDefinition,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
) -> dict[str, object]:
    if tool.risk != ToolRisk.CHANGE:
        return args

    latest_user_text = _latest_user_message_text(working_messages)
    if latest_user_text is None:
        return args

    follow_up_context = messages_before_latest_user_if_reason_follow_up(
        working_messages
    )
    if follow_up_context is None:
        return args

    if not _matches_reason_follow_up_workflow_action(
        tool=tool,
        args=args,
        follow_up_context=follow_up_context,
    ):
        return args

    candidate_args = dict(args)
    candidate_args["reason"] = latest_user_text
    if (
        _validate_change_reason_provenance(
            tool=tool,
            args=candidate_args,
            working_messages=working_messages,
        )
        is not None
    ):
        return args

    return candidate_args


def _matches_reason_follow_up_workflow_action(
    *,
    tool: ToolDefinition,
    args: dict[str, object],
    follow_up_context: list[dict[str, object]],
) -> bool:
    inferred = _infer_waiting_on_user_workflow_from_messages(
        assistant_text="",
        working_messages=follow_up_context,
    )
    if inferred is None:
        return False

    inferred_tool_name = inferred.get("tool_name")
    inferred_args = inferred.get("args")
    if inferred_tool_name != tool.name or not isinstance(inferred_args, dict):
        return False

    inferred_args_dict = {
        str(key): value for key, value in inferred_args.items() if isinstance(key, str)
    }

    return _canonical_tool_args(
        _tool_args_without_reason(args)
    ) == _canonical_tool_args(_tool_args_without_reason(inferred_args_dict))


def _tool_args_without_reason(args: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in args.items() if key != "reason"}


def _message_has_text(message: dict[str, object], expected_text: str) -> bool:
    parts = message.get("parts")
    if not isinstance(parts, list):
        return False
    for part in parts:
        part_dict = _as_object_dict(part)
        if part_dict is None or part_dict.get("type") != "text":
            continue
        if part_dict.get("text") == expected_text:
            return True
    return False


def _build_approval_context(
    *,
    tool_name: str,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
) -> dict[str, object]:
    return build_approval_context(
        tool_name=tool_name,
        args=args,
        working_messages=working_messages,
    )


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


def _assistant_guidance_for_change_validation_error(
    *,
    tool_name: str,
    args: dict[str, object],
    error: SanitizedToolError,
) -> str | None:
    if error.error_code != "invalid_tool_arguments":
        return None

    details = tuple(detail.lower() for detail in (error.details or ()))
    if not any("reason" in detail for detail in details):
        return None

    activity = describe_workflow_activity(tool_name=tool_name, args=args).lower()
    return (
        f"I need a short, human-readable reason before I can continue {activity}. "
        "Please provide an osTicket/reference number or a brief description for the reason you want recorded for this change."
    )


def _internal_tool_guidance(tool_name: str) -> str | None:
    if tool_name == "request_approval":
        return (
            "Approval requests are created automatically after you call the "
            "underlying CHANGE tool. Do not call request_approval directly."
        )
    if tool_name == "update_workflow_todo":
        return (
            "Workflow TODO state is backend-managed for operational workflows. "
            "For simple READ requests, answer directly after using tools instead of calling update_workflow_todo."
        )
    return None


def _should_stop_after_internal_tool_guidance(tool_name: str, count: int) -> bool:
    return tool_name in {"request_approval", "update_workflow_todo"} and count >= 2


def _latest_tool_result_part(
    working_messages: list[dict[str, object]],
) -> dict[str, object] | None:
    for message in reversed(working_messages):
        if message.get("role") != "tool":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in reversed(parts):
            part_dict = _as_object_dict(part)
            if part_dict is not None and part_dict.get("type") == "tool-result":
                return part_dict
    return None


def _tool_call_args_for_id(
    working_messages: list[dict[str, object]], tool_call_id: str
) -> dict[str, object] | None:
    for message in reversed(working_messages):
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            part_dict = _as_object_dict(part)
            if part_dict is None or part_dict.get("type") != "tool-call":
                continue
            if part_dict.get("toolCallId") != tool_call_id:
                continue
            args = part_dict.get("args")
            args_dict = _as_object_dict(args)
            if args_dict is not None:
                return args_dict
            return {}
    return None


def _canonical_tool_args(args: dict[str, object]) -> str:
    return json.dumps(json_safe(args), sort_keys=True, separators=(",", ":"))


def _working_messages_after_part(
    working_messages: list[dict[str, object]],
    *,
    part_to_match: dict[str, object],
) -> list[dict[str, object]]:
    for message_index, message in enumerate(working_messages):
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part_index, raw_part in enumerate(parts):
            part_dict = _as_object_dict(raw_part)
            if part_dict is not part_to_match:
                continue
            trailing_parts = parts[part_index + 1 :]
            trailing_messages: list[dict[str, object]] = []
            if trailing_parts:
                trailing_messages.append(
                    {
                        "role": message.get("role"),
                        "parts": trailing_parts,
                    }
                )
            trailing_messages.extend(working_messages[message_index + 1 :])
            return trailing_messages
    return []


def _has_fresh_matching_preflight_after_failed_tool_result(
    *,
    tool_name: str,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    failed_tool_result_part: dict[str, object],
    requested_server_id: str | None,
) -> bool:
    result = _as_object_dict(failed_tool_result_part.get("result"))
    if result is None:
        return False
    error_code = _normalized_text(result.get("error_code"))
    if error_code not in {"preflight_required", "preflight_mismatch"}:
        return False

    trailing_messages = _working_messages_after_part(
        working_messages,
        part_to_match=failed_tool_result_part,
    )
    if not trailing_messages:
        return False

    return (
        _require_matching_preflight(
            tool_name=tool_name,
            args=args,
            working_messages=trailing_messages,
            requested_server_id=requested_server_id,
        )
        is None
    )


def _latest_matching_failed_tool_result_part(
    *,
    working_messages: list[dict[str, object]],
    tool_name: str,
    args: dict[str, object],
) -> dict[str, object] | None:
    expected_args = _canonical_tool_args(args)
    for message in reversed(working_messages):
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in reversed(parts):
            part_dict = _as_object_dict(part)
            if part_dict is None or part_dict.get("type") != "tool-result":
                continue
            if part_dict.get("toolName") != tool_name:
                continue
            result = _as_object_dict(part_dict.get("result"))
            if result is None:
                continue
            if part_dict.get("isError") is not True and result.get("ok") is not False:
                continue
            tool_call_id = part_dict.get("toolCallId")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue
            call_args = _tool_call_args_for_id(working_messages, tool_call_id)
            if not isinstance(call_args, dict):
                continue
            if _canonical_tool_args(call_args) != expected_args:
                continue
            return part_dict
    return None


def _post_tool_followup_guidance(
    working_messages: list[dict[str, object]],
) -> str | None:
    latest_tool_result = _latest_tool_result_part(working_messages)
    if latest_tool_result is None:
        return None
    preflight_guidance = _preflight_retry_guidance(latest_tool_result)
    if preflight_guidance is not None:
        return preflight_guidance
    return (
        "Using the latest tool result you already have, answer the user directly now. "
        "If the tool succeeded, give the direct answer first and then short supporting evidence. "
        "If the tool failed, explain the error code, likely cause, and next safe step. "
        "If a workflow receipt is already present, keep the narration to 1-2 lines and refer to it. "
        "Do not call another tool unless another fact is strictly required. Reply in the user's language."
    )


def _preflight_retry_guidance(tool_result_part: dict[str, object]) -> str | None:
    result = _as_object_dict(tool_result_part.get("result"))
    if result is None:
        return None
    error_code = _normalized_text(result.get("error_code"))
    if error_code not in {"preflight_required", "preflight_mismatch"}:
        return None
    tool_name = (
        _normalized_text(tool_result_part.get("toolName")) or "the requested change"
    )
    return (
        f"The previous attempt to run {tool_name} is blocked by stale or missing preflight evidence. "
        "Run a fresh matching preflight now, review the current state, then decide whether to retry the change. "
        "Do not present the raw preflight error to the user unless the fresh preflight also fails."
    )


def _preflight_user_retry_reply(
    *,
    working_messages: list[dict[str, object]],
    tool_result_part: dict[str, object],
) -> str:
    tool_name = _normalized_text(tool_result_part.get("toolName")) or "that change"
    tool_call_id = _normalized_text(tool_result_part.get("toolCallId"))
    args = (
        _tool_call_args_for_id(working_messages, tool_call_id)
        if tool_call_id is not None
        else None
    )
    activity = describe_workflow_activity(tool_name=tool_name, args=args or {})
    normalized_activity = (
        activity[:1].lower() + activity[1:] if activity else "that change"
    )
    return (
        "I need to run a fresh matching preflight before I can continue with "
        f"{normalized_activity}. I need to re-check the current state first, then decide whether to retry the change."
    )


def _extract_firewall_preflight_raw_outputs(
    messages: list[AgentMessage],
) -> list[tuple[str, str]]:
    outputs: list[tuple[str, str]] = []
    for message in messages:
        for part in message.parts:
            part_dict = _as_object_dict(part)
            if part_dict is None or part_dict.get("type") != "tool-result":
                continue
            if part_dict.get("toolName") != "whm_preflight_firewall_entries":
                continue
            result = _as_object_dict(part_dict.get("result"))
            if result is None or result.get("ok") is not True:
                continue
            target = _normalized_text(result.get("target")) or "the target"
            csf_result = _as_object_dict(result.get("csf"))
            if csf_result is None:
                continue
            verdict = _normalized_text(csf_result.get("verdict"))
            if verdict not in {"blocked", "allowlisted"}:
                continue
            raw_output = csf_result.get("raw_output")
            raw_text = (
                raw_output
                if isinstance(raw_output, str) and raw_output.strip()
                else None
            )
            if raw_text is None:
                continue
            outputs.append((f"{target} (CSF)", raw_text))
    return outputs


def _render_firewall_preflight_raw_output(raw_outputs: list[tuple[str, str]]) -> str:
    blocks: list[str] = []
    for target, raw_output in raw_outputs:
        blocks.append(f"Raw preflight output for {target}:\n```\n{raw_output}\n```")
    return "\n\n".join(blocks).strip()


def _append_firewall_preflight_raw_output(
    text: str, raw_outputs: list[tuple[str, str]]
) -> str:
    appendix = _render_firewall_preflight_raw_output(raw_outputs)
    if not appendix:
        return text
    if not text.strip():
        return appendix
    return f"{text}\n\n{appendix}"


def _fallback_assistant_reply_from_recent_tool_result(
    working_messages: list[dict[str, object]],
) -> str | None:
    latest_tool_result = _latest_tool_result_part(working_messages)
    if latest_tool_result is None:
        return None

    return _assistant_reply_from_tool_result_part(
        working_messages=working_messages,
        tool_result_part=latest_tool_result,
    )


def _assistant_reply_from_tool_result_part(
    *,
    working_messages: list[dict[str, object]],
    tool_result_part: dict[str, object],
) -> str | None:
    latest_tool_result = tool_result_part

    tool_name = latest_tool_result.get("toolName")
    tool_call_id = latest_tool_result.get("toolCallId")
    result = latest_tool_result.get("result")
    is_error = latest_tool_result.get("isError") is True

    if not isinstance(tool_name, str):
        return None

    result_dict = _as_object_dict(result)

    if tool_name == "whm_preflight_firewall_entries" and result_dict is not None:
        target = _normalized_text(result_dict.get("target")) or "the target"
        verdict = _normalized_text(result_dict.get("combined_verdict")) or "unknown"
        csf_result = _as_object_dict(result_dict.get("csf"))
        raw_output = csf_result.get("raw_output") if csf_result is not None else None
        raw_output_text = (
            raw_output if isinstance(raw_output, str) and raw_output.strip() else None
        )
        matches = result_dict.get("matches")
        match_count = (
            len([item for item in matches if isinstance(item, str) and item.strip()])
            if isinstance(matches, list)
            else 0
        )
        args = (
            _tool_call_args_for_id(working_messages, tool_call_id)
            if isinstance(tool_call_id, str)
            else None
        )
        server_ref = _normalized_text(args.get("server_ref")) if args else None
        location = f" on server {server_ref}" if server_ref else ""
        available_tools = _as_object_dict(result_dict.get("available_tools"))
        available_labels: list[str] = []
        if available_tools is not None:
            if available_tools.get("csf") is True:
                available_labels.append("CSF")
            if available_tools.get("imunify") is True:
                available_labels.append("Imunify")
        available_text = (
            f" Available tools: {', '.join(available_labels)}."
            if available_labels
            else ""
        )

        if verdict == "blocked":
            answer = f"Firewall result: {target}{location} is blocked."
        elif verdict == "allowlisted":
            answer = f"Firewall result: {target}{location} is allowlisted."
        elif verdict == "not_found":
            answer = f"Firewall result: {target}{location} was not found in the available firewall tools."
        else:
            answer = f"Firewall result: {target}{location} returned an inconclusive verdict ({verdict})."

        if verdict == "not_found":
            evidence = "No matching firewall entries were found."
        elif match_count > 0:
            evidence = f"Found {match_count} matching firewall entr{'y' if match_count == 1 else 'ies'}."
        else:
            evidence = "The tool returned no matching evidence lines."

        reply = f"{answer}\n\nEvidence: {evidence}{available_text}"
        if verdict in {"blocked", "allowlisted"} and raw_output_text is not None:
            reply = (
                f"{reply}\n\nRaw preflight output (CSF):\n```\n{raw_output_text}\n```"
            )
        return reply

    if result_dict is not None:
        error_code = _normalized_text(result_dict.get("error_code"))
        message = _normalized_text(result_dict.get("message"))
        if is_error or result_dict.get("ok") is False:
            if error_code in {"preflight_required", "preflight_mismatch"}:
                return _preflight_user_retry_reply(
                    working_messages=working_messages,
                    tool_result_part=latest_tool_result,
                )
            if error_code and message:
                return f"The tool failed with {error_code}: {message}"
            if message:
                return message
            if error_code:
                return f"The tool failed with {error_code}."
        if message and message != "ok":
            return message

    tool = get_tool_definition(tool_name)
    if tool is None or tool.risk != ToolRisk.READ or is_error:
        return None

    return _generic_read_success_fallback(result)

    return None


def _generic_read_success_fallback(result: object) -> str:
    result_dict = _as_object_dict(result)
    if result_dict is None:
        return "The check completed successfully."

    summary_parts: list[str] = []

    for key in ("datetime", "date", "time", "timestamp", "verdict"):
        value = _normalized_text(result_dict.get(key))
        if value is not None:
            summary_parts.append(f"{key}: {value}")

    found = result_dict.get("found")
    if isinstance(found, bool):
        summary_parts.append(f"found: {'yes' if found else 'no'}")

    count = _generic_read_result_count(result_dict)
    if count is not None:
        summary_parts.append(f"count: {count}")

    for key in ("path", "file", "filepath"):
        value = _normalized_text(result_dict.get(key))
        if value is not None:
            summary_parts.append(f"{key}: {value}")
            break

    message = _normalized_text(result_dict.get("message"))
    if message is not None and message != "ok":
        summary_parts.append(f"message: {message}")

    if summary_parts:
        return f"Read result: {'; '.join(summary_parts[:5])}."

    return "The check completed successfully."


def _generic_read_result_count(result: dict[str, object]) -> int | None:
    for key in ("count", "total", "total_count", "match_count", "matches_count"):
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value

    for key in ("matches", "items", "results", "entries", "paths"):
        value = result.get(key)
        if isinstance(value, list):
            return len(value)

    return None


def _infer_waiting_on_user_workflow_from_messages(
    working_messages: list[dict[str, object]], assistant_text: str | None = None
) -> dict[str, object] | None:
    inferred = infer_waiting_on_user_workflow_from_messages(
        assistant_text=assistant_text or "",
        working_messages=working_messages,
    )
    if inferred is None:
        return None
    return {
        "tool_name": inferred.tool_name,
        "args": inferred.args,
    }


def _split_text_deltas(text: str, *, chunk_size: int = 24) -> list[str]:
    if not text:
        return []
    return [
        text[index : index + chunk_size] for index in range(0, len(text), chunk_size)
    ]
