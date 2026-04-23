from __future__ import annotations

from noa_api.core.tools.definitions import ALL_TOOLS
from noa_api.core.tools.schemas.common import REASON_PARAM as _REASON_PARAM  # noqa: F401 — re-export
from noa_api.core.tools.types import ToolDefinition  # noqa: F401 — re-export

_MVP_TOOLS = ALL_TOOLS
_MVP_TOOL_INDEX: dict[str, ToolDefinition] = {t.name: t for t in _MVP_TOOLS}


def get_tool_registry() -> tuple[ToolDefinition, ...]:
    return _MVP_TOOLS


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return _MVP_TOOL_INDEX.get(tool_name)


def get_tool_names() -> tuple[str, ...]:
    return tuple(t.name for t in _MVP_TOOLS)
