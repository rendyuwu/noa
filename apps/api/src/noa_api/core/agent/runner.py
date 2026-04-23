from __future__ import annotations

import logging
from inspect import signature
from typing import Awaitable, Callable, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.json_safety import json_safe
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.core.tool_error_sanitizer import SanitizedToolError, sanitize_tool_error
from noa_api.core.tools.argument_validation import validate_tool_arguments
from noa_api.core.tools.registry import (
    ToolDefinition,
    get_tool_definition,
    get_tool_registry,
)
from noa_api.core.workflows.registry import (
    build_workflow_reply_template,
    build_workflow_todos,
    collect_recent_preflight_evidence,
    persist_workflow_todos,
)
from noa_api.core.workflows.types import (
    assistant_is_requesting_reason,
    render_workflow_reply_text,
)
from noa_api.core.workflows.preflight_validation import (
    resolve_requested_server_id as _resolve_requested_server_id,
    validate_matching_preflight as _require_matching_preflight,
)
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ToolRisk

# Re-exports for backward compatibility
from noa_api.core.agent.llm_client import (  # noqa: F401
    LLMClientProtocol,
    LLMToolCall,
    LLMTurnResponse,
    OpenAICompatibleLLMClient,
    create_default_llm_client,
    _split_text_deltas,
)
from noa_api.core.agent.message_codec import (  # noqa: F401
    AgentMessage,
    AgentRunnerResult,
    ProcessedToolCall,
    _as_object_dict,
    _assistant_message_parts,
    _append_assistant_text_to_working_messages,
    _append_assistant_text_to_output_messages,
    _should_persist_assistant_text_this_round,
    _should_suppress_provisional_assistant_text_this_round,
    _message_visible_text,
    _render_workflow_milestone_text,
    _finalize_turn_messages,
    _prompt_replay_parts,
    _to_openai_chat_messages,
    _safe_json_object,
    _extract_reasoning_summary,
)
from noa_api.core.agent.tool_schemas import (  # noqa: F401
    _to_openai_tool_schema,
    _llm_tool_description,
    _tool_risk_note,
    _build_approval_context,
)
from noa_api.core.agent.guidance import (  # noqa: F401
    _tool_error_messages,
    _assistant_guidance_for_change_validation_error,
    _internal_tool_guidance,
    _should_stop_after_internal_tool_guidance,
    _post_tool_followup_guidance,
    _preflight_retry_guidance,
    _preflight_user_retry_reply,
    _extract_firewall_preflight_raw_outputs,
    _render_firewall_preflight_raw_output,
    _append_firewall_preflight_raw_output,
)
from noa_api.core.agent.fallbacks import (  # noqa: F401
    _latest_tool_result_part,
    _tool_call_args_for_id,
    _canonical_tool_args,
    _working_messages_after_part,
    _has_fresh_matching_preflight_after_failed_tool_result,
    _latest_matching_failed_tool_result_part,
    _fallback_assistant_reply_from_recent_tool_result,
    _assistant_reply_from_tool_result_part,
    _generic_read_success_fallback,
    _generic_read_result_count,
    _infer_waiting_on_user_workflow_from_messages,
)
from noa_api.core.agent.change_validation import (  # noqa: F401
    _normalized_text,
    _reason_provenance_tokens,
    _reason_tokens_are_explicit_in_latest_user_turn,
    _is_reason_provenance_error,
    _latest_user_message_text,
    _validate_change_reason_provenance,
    _canonicalize_reason_follow_up_args,
    _matches_reason_follow_up_workflow_action,
    _tool_args_without_reason,
    _message_has_text,
)


logger = logging.getLogger(__name__)


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
