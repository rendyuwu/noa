from __future__ import annotations


async def test_update_workflow_todo_echoes_list() -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    todos = [
        {"content": "Preflight", "status": "in_progress", "priority": "high"},
        {"content": "Request approval", "status": "pending", "priority": "high"},
    ]
    result = await update_workflow_todo(todos=todos)
    assert result["ok"] is True
    assert result["todos"] == todos
