from __future__ import annotations

import json

from noa_api.core.agent.change_validation import _normalized_text
from noa_api.core.agent.message_codec import _as_object_dict
from noa_api.core.json_safety import json_safe
from noa_api.core.workflows.preflight_validation import (
    validate_matching_preflight as _require_matching_preflight,
)
from noa_api.core.workflows.registry import (
    infer_waiting_on_user_workflow_from_messages,
)
from noa_api.storage.postgres.lifecycle import ToolRisk


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
    from noa_api.core.agent.guidance import (
        _preflight_user_retry_reply,
    )
    import noa_api.core.agent.runner as _runner_mod

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

    tool = _runner_mod.get_tool_definition(tool_name)
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
