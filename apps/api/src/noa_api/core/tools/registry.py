from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from noa_api.core.tools.demo_tools import get_current_date, get_current_time, set_demo_flag

ToolRisk = Literal["READ", "CHANGE"]
ToolExecutor = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    risk: ToolRisk
    execute: ToolExecutor


_MVP_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_current_time",
        description="Get the server current time.",
        risk="READ",
        execute=get_current_time,
    ),
    ToolDefinition(
        name="get_current_date",
        description="Get the server current date.",
        risk="READ",
        execute=get_current_date,
    ),
    ToolDefinition(
        name="set_demo_flag",
        description="Set a demo marker flag in persistence.",
        risk="CHANGE",
        execute=set_demo_flag,
    ),
)
_MVP_TOOL_INDEX = {tool.name: tool for tool in _MVP_TOOLS}


def get_tool_registry() -> tuple[ToolDefinition, ...]:
    return _MVP_TOOLS


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return _MVP_TOOL_INDEX.get(tool_name)


def get_tool_names() -> tuple[str, ...]:
    return tuple(tool.name for tool in _MVP_TOOLS)
