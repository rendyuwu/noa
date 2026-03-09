from __future__ import annotations

from noa_api.core.tools.registry import get_tool_names

MVP_TOOL_CATALOG: tuple[str, ...] = get_tool_names()


def get_tool_catalog() -> tuple[str, ...]:
    return MVP_TOOL_CATALOG
