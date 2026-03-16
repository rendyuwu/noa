from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.workflow_todos import (
    SQLWorkflowTodoRepository,
    WorkflowTodoItem,
    WorkflowTodoService,
)


_VALID_TODO_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_VALID_TODO_PRIORITIES = {"high", "medium", "low"}


async def _validate_workflow_todos(*, todos: list[dict[str, Any]]) -> dict[str, Any]:
    validated_todos: list[WorkflowTodoItem] = []
    in_progress_count = 0
    for index, todo in enumerate(todos, start=1):
        content = todo.get("content")
        if not isinstance(content, str) or not content.strip():
            return {
                "ok": False,
                "error_code": "todo_content_required",
                "message": f"Todo item {index} must include non-empty content",
            }

        status = todo.get("status")
        if status not in _VALID_TODO_STATUSES:
            return {
                "ok": False,
                "error_code": "invalid_todo_status",
                "message": (
                    f"Todo item {index} has invalid status '{status}'. "
                    "Use pending, in_progress, completed, or cancelled"
                ),
            }
        if status == "in_progress":
            in_progress_count += 1

        priority = todo.get("priority")
        if priority not in _VALID_TODO_PRIORITIES:
            return {
                "ok": False,
                "error_code": "invalid_todo_priority",
                "message": (
                    f"Todo item {index} has invalid priority '{priority}'. "
                    "Use high, medium, or low"
                ),
            }

        validated_todos.append(
            {
                "content": content,
                "status": status,
                "priority": priority,
            }
        )

    if in_progress_count > 1:
        return {
            "ok": False,
            "error_code": "multiple_in_progress_todos",
            "message": "Only one workflow TODO item can be in_progress at a time",
        }

    return {"ok": True, "todos": validated_todos}


async def update_workflow_todo(
    *,
    todos: list[dict[str, Any]],
    session: AsyncSession | None = None,
    thread_id: UUID | None = None,
) -> dict[str, Any]:
    result = await _validate_workflow_todos(todos=todos)
    if not result.get("ok"):
        return result

    if session is not None and thread_id is not None:
        workflow_todo_service = WorkflowTodoService(
            repository=SQLWorkflowTodoRepository(session)
        )
        await workflow_todo_service.replace_workflow(
            thread_id=thread_id,
            todos=result["todos"],
        )

    return result
