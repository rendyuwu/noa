from __future__ import annotations

from datetime import datetime


async def get_current_time() -> dict[str, str]:
    return {"time": datetime.now().astimezone().isoformat(timespec="seconds")}


async def get_current_date() -> dict[str, str]:
    return {"date": datetime.now().date().isoformat()}
