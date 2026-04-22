from __future__ import annotations

from noa_api.core.tools.registry import ToolDefinition
from noa_api.core.workflows.registry import build_approval_context
from noa_api.storage.postgres.lifecycle import ToolRisk


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
