from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from noa_api.core.agent.change_validation import _normalized_text


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
    *, text: str, tool_calls: list[object]
) -> bool:
    from noa_api.core.workflows.types import assistant_is_requesting_reason

    if not text:
        return False

    if assistant_is_requesting_reason(text):
        return True

    return not tool_calls


def _should_suppress_provisional_assistant_text_this_round(
    *, text: str, tool_calls: list[object]
) -> bool:
    from noa_api.core.workflows.types import assistant_is_requesting_reason
    import noa_api.core.agent.runner as _runner_mod
    from noa_api.storage.postgres.lifecycle import ToolRisk

    if not text or assistant_is_requesting_reason(text):
        return False

    for tool_call in tool_calls:
        if getattr(tool_call, "name", None) in {"request_approval", "update_workflow_todo"}:
            return True
        tool = _runner_mod.get_tool_definition(getattr(tool_call, "name", ""))
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
