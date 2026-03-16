from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from noa_api.core.agent.runner import _to_openai_tool_schema
from noa_api.core.tools import catalog
from noa_api.core.tools.catalog import get_tool_catalog
from noa_api.core.tools.demo_tools import (
    get_current_date,
    get_current_time,
    set_demo_flag,
)
from noa_api.core.tools.registry import get_tool_definition, get_tool_registry
from noa_api.storage.postgres.models import AuditLog


@dataclass
class _FakeSession:
    added: list[object]
    flushed: bool = False

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flushed = True


async def test_tool_registry_contains_demo_tools_with_expected_risk() -> None:
    registry = get_tool_registry()
    names = tuple(tool.name for tool in registry)
    risks = {tool.name: tool.risk for tool in registry}

    assert names == get_tool_catalog()
    assert risks["get_current_time"] == "READ"
    assert risks["get_current_date"] == "READ"
    assert risks["set_demo_flag"] == "CHANGE"
    assert get_tool_definition("set_demo_flag") is not None
    assert get_tool_definition("unknown_tool") is None


async def test_tool_registry_exposes_machine_readable_parameter_schemas() -> None:
    by_name = {tool.name: tool for tool in get_tool_registry()}

    assert by_name["get_current_time"].parameters_schema["properties"] == {}
    assert by_name["get_current_date"].parameters_schema["properties"] == {}

    set_demo_flag_schema = by_name["set_demo_flag"].parameters_schema
    assert set_demo_flag_schema["required"] == ["key", "value"]
    assert (
        set_demo_flag_schema["properties"]["key"]["description"]
        == "Demo flag name to persist in the audit log, such as a feature or scenario identifier."
    )
    assert (
        set_demo_flag_schema["properties"]["value"]["description"]
        == "JSON-serializable flag value to persist for the demo marker."
    )

    change_email_schema = by_name["whm_change_contact_email"].parameters_schema
    assert change_email_schema["properties"]["new_email"]["format"] == "email"

    search_schema = by_name["whm_search_accounts"].parameters_schema
    assert search_schema["properties"]["limit"]["default"] == 20
    assert search_schema["properties"]["limit"]["minimum"] == 1


async def test_openai_tool_schema_includes_risk_notes_and_guidance() -> None:
    suspend_tool = get_tool_definition("whm_suspend_account")
    todo_tool = get_tool_definition("update_workflow_todo")

    assert suspend_tool is not None
    assert todo_tool is not None

    suspend_schema = _to_openai_tool_schema(suspend_tool)
    todo_schema = _to_openai_tool_schema(todo_tool)

    suspend_description = suspend_schema["function"]["description"]
    assert (
        "Risk: CHANGE. Requires persisted approval before execution."
        in suspend_description
    )
    assert "Run `whm_preflight_account` first" in suspend_description
    assert "status` `no-op`" in suspend_description

    todo_description = todo_schema["function"]["description"]
    assert (
        "Risk: READ. Evidence-gathering only; it does not change system state."
        in todo_description
    )
    assert "Keep exactly one item in_progress at a time" in todo_description


async def test_tools_catalog_is_sourced_live_from_registry(monkeypatch) -> None:
    monkeypatch.setattr(catalog, "get_tool_names", lambda: ("dynamic_tool",))

    assert catalog.get_tool_catalog() == ("dynamic_tool",)


async def test_read_demo_tools_return_time_and_date_payloads() -> None:
    current_time = await get_current_time()
    current_date = await get_current_date()

    assert "time" in current_time
    assert "date" in current_date
    datetime.fromisoformat(current_time["time"])
    date.fromisoformat(current_date["date"])


async def test_set_demo_flag_writes_db_backed_marker() -> None:
    session = _FakeSession(added=[])

    result = await set_demo_flag(session=session, key="feature_x", value=True)

    assert session.flushed is True
    assert len(session.added) == 1
    marker = cast(AuditLog, session.added[0])
    assert isinstance(marker, AuditLog)
    assert marker.event_type == "demo_flag_set"
    assert marker.tool_name == "set_demo_flag"
    assert marker.meta_data == {"key": "feature_x", "value": True}
    assert result == {"ok": True, "flag": {"key": "feature_x", "value": True}}
