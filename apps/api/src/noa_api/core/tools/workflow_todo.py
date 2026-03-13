from __future__ import annotations

from typing import Any


async def update_workflow_todo(*, todos: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": True, "todos": todos}
