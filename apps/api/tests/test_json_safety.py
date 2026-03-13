from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import uuid4

from noa_api.storage.postgres.lifecycle import ToolRisk


def test_json_safe_converts_datetime_date_uuid_enum_and_sets() -> None:
    from noa_api.core.json_safety import json_safe

    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    payload = {
        "now": now,
        "day": date(2026, 3, 13),
        "id": uuid4(),
        "risk": ToolRisk.READ,
        "tags": {"a", "b"},
    }

    safe = json_safe(payload)
    assert isinstance(safe, dict)
    assert safe["now"] == now.isoformat()
    assert safe["day"] == "2026-03-13"
    assert isinstance(safe["id"], str)
    assert safe["risk"] == "READ"
    assert sorted(safe["tags"]) == ["a", "b"]

    json.dumps(safe)
