from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

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

    assert by_name["get_current_time"].parameters_schema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert by_name["get_current_date"].parameters_schema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert by_name["set_demo_flag"].parameters_schema == {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {},
        },
        "required": ["key", "value"],
        "additionalProperties": False,
    }


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
