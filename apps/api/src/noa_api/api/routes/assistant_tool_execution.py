from __future__ import annotations


def build_tool_result_part(
    *,
    tool_name: str,
    tool_call_id: str,
    result: dict[str, object],
    is_error: bool,
) -> dict[str, object]:
    return {
        "type": "tool-result",
        "toolName": tool_name,
        "toolCallId": tool_call_id,
        "result": result,
        "isError": is_error,
    }
