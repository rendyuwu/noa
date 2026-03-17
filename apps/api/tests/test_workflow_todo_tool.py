from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


async def test_update_workflow_todo_echoes_list() -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    todos = [
        {"content": "Preflight", "status": "in_progress", "priority": "high"},
        {"content": "Request approval", "status": "pending", "priority": "high"},
    ]
    result = await update_workflow_todo(todos=todos)
    assert result["ok"] is True
    assert result["todos"] == todos


async def test_update_workflow_todo_accepts_blocked_statuses() -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    todos = [
        {
            "content": "Ask user to confirm target",
            "status": "waiting_on_user",
            "priority": "high",
        },
        {
            "content": "Request approval",
            "status": "waiting_on_approval",
            "priority": "high",
        },
    ]

    result = await update_workflow_todo(todos=todos)

    assert result == {"ok": True, "todos": todos}


async def test_update_workflow_todo_rejects_multiple_in_progress_items() -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    result = await update_workflow_todo(
        todos=[
            {"content": "Preflight", "status": "in_progress", "priority": "high"},
            {
                "content": "Request approval",
                "status": "in_progress",
                "priority": "high",
            },
        ]
    )

    assert result == {
        "ok": False,
        "error_code": "multiple_in_progress_todos",
        "message": "Only one workflow TODO item can be in_progress at a time",
    }


async def test_update_workflow_todo_rejects_invalid_status() -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    result = await update_workflow_todo(
        todos=[
            {"content": "Preflight", "status": "running", "priority": "high"},
        ]
    )

    assert result["ok"] is False
    assert result["error_code"] == "invalid_todo_status"
    assert "waiting_on_user" in result["message"]
    assert "waiting_on_approval" in result["message"]


async def test_update_workflow_todo_persists_workflow_when_thread_context_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    todos = [
        {"content": "Preflight", "status": "completed", "priority": "high"},
        {
            "content": "Request approval",
            "status": "in_progress",
            "priority": "high",
        },
    ]
    thread_id = uuid4()

    result = await update_workflow_todo(
        todos=todos,
        session=cast(AsyncSession, object()),
        thread_id=thread_id,
    )

    assert result == {"ok": True, "todos": todos}
    assert captured == {"thread_id": thread_id, "todos": todos}


async def test_update_workflow_todo_persists_empty_workflow_to_clear_thread_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    captured: dict[str, object] = {}

    async def _record_replace(self, *, thread_id, todos):
        captured["thread_id"] = thread_id
        captured["todos"] = todos

    monkeypatch.setattr(
        "noa_api.storage.postgres.workflow_todos.WorkflowTodoService.replace_workflow",
        _record_replace,
    )

    thread_id = uuid4()

    result = await update_workflow_todo(
        todos=[],
        session=cast(AsyncSession, object()),
        thread_id=thread_id,
    )

    assert result == {"ok": True, "todos": []}
    assert captured == {"thread_id": thread_id, "todos": []}
