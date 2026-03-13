from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from noa_api.core.tools.demo_tools import (
    get_current_date,
    get_current_time,
    set_demo_flag,
)
from noa_api.core.tools.workflow_todo import update_workflow_todo
from noa_api.storage.postgres.lifecycle import ToolRisk

ToolExecutor = Callable[..., Awaitable[dict[str, Any]]]
ToolParametersSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    risk: ToolRisk
    parameters_schema: ToolParametersSchema
    execute: ToolExecutor


_MVP_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_current_time",
        description="Get the server current time.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=get_current_time,
    ),
    ToolDefinition(
        name="get_current_date",
        description="Get the server current date.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=get_current_date,
    ),
    ToolDefinition(
        name="set_demo_flag",
        description="Set a demo marker flag in persistence.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {}},
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        execute=set_demo_flag,
    ),
    ToolDefinition(
        name="update_workflow_todo",
        description="Update the workflow TODO checklist shown in chat.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "cancelled",
                                ],
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": ["content", "status", "priority"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        },
        execute=update_workflow_todo,
    ),
)
_MVP_TOOL_INDEX = {tool.name: tool for tool in _MVP_TOOLS}


def get_tool_registry() -> tuple[ToolDefinition, ...]:
    return _MVP_TOOLS


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return _MVP_TOOL_INDEX.get(tool_name)


def get_tool_names() -> tuple[str, ...]:
    return tuple(tool.name for tool in _MVP_TOOLS)
