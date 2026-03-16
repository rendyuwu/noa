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
