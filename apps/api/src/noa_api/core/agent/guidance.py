from __future__ import annotations

from noa_api.core.agent.message_codec import (
    AgentMessage,
    _as_object_dict,
)
from noa_api.core.agent.change_validation import _normalized_text
from noa_api.core.agent.fallbacks import (
    _latest_tool_result_part,
    _tool_call_args_for_id,
)
from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.tools.registry import ToolDefinition
from noa_api.core.workflows.registry import describe_workflow_activity


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
