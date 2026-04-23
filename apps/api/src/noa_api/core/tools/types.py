from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from noa_api.storage.postgres.lifecycle import ToolRisk

ToolExecutor = Callable[..., Awaitable[dict[str, Any]]]
ToolParametersSchema = dict[str, Any]
ToolResultSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    risk: ToolRisk
    parameters_schema: ToolParametersSchema
    execute: ToolExecutor
    prompt_hints: tuple[str, ...] = ()
    result_schema: ToolResultSchema | None = None
    workflow_family: str | None = None
