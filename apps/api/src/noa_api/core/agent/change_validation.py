from __future__ import annotations

import unicodedata

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.tools.registry import ToolDefinition
from noa_api.core.workflows.types import messages_before_latest_user_if_reason_follow_up
from noa_api.storage.postgres.lifecycle import ToolRisk


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
    from noa_api.core.agent.message_codec import _as_object_dict

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
    from noa_api.core.agent.fallbacks import (
        _canonical_tool_args,
        _infer_waiting_on_user_workflow_from_messages,
    )

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
    from noa_api.core.agent.message_codec import _as_object_dict

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
