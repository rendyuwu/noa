from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.models import AuditLog


async def get_current_time() -> dict[str, str]:
    return {"time": datetime.now().astimezone().isoformat(timespec="seconds")}


async def get_current_date() -> dict[str, str]:
    return {"date": datetime.now().date().isoformat()}


async def set_demo_flag(
    *, session: AsyncSession, key: str, value: Any
) -> dict[str, Any]:
    marker_payload = {"key": key, "value": value}
    session.add(
        AuditLog(
            event_type="demo_flag_set",
            user_email=None,
            tool_name="set_demo_flag",
            meta_data=marker_payload,
        )
    )
    await session.flush()
    return {
        "ok": True,
        "flag": marker_payload,
    }
