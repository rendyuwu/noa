from __future__ import annotations

MVP_TOOL_CATALOG: tuple[str, ...] = (
    "get_current_time",
    "get_current_date",
    "set_demo_flag",
)


def get_tool_catalog() -> tuple[str, ...]:
    return MVP_TOOL_CATALOG
