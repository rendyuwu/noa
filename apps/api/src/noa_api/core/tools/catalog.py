from __future__ import annotations

from noa_api.core.tools.registry import get_tool_names

def get_tool_catalog() -> tuple[str, ...]:
    return get_tool_names()
