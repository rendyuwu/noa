from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from noa_api.storage.postgres.workflow_todos import (
    WorkflowTodoItem,
    WorkflowTodoService,
)


@dataclass
class _FakeWorkflowTodoRepository:
    workflows: dict[UUID, list[WorkflowTodoItem]] = field(default_factory=dict)
    cleared_thread_ids: list[UUID] = field(default_factory=list)

    async def replace_workflow(
        self, *, thread_id: UUID, todos: list[WorkflowTodoItem]
    ) -> None:
        self.workflows[thread_id] = list(todos)

    async def list_workflow(self, *, thread_id: UUID) -> list[WorkflowTodoItem]:
        return list(self.workflows.get(thread_id, []))

    async def clear_workflow(self, *, thread_id: UUID) -> None:
        self.cleared_thread_ids.append(thread_id)
        self.workflows.pop(thread_id, None)


async def test_workflow_todo_service_replace_workflow_replaces_existing_items() -> None:
    thread_id = uuid4()
    repository = _FakeWorkflowTodoRepository(
        workflows={
            thread_id: [
                {"content": "Old step", "status": "waiting_on_user", "priority": "high"}
            ]
        }
    )
    service = WorkflowTodoService(repository=repository)

    replaced = await service.replace_workflow(
        thread_id=thread_id,
        todos=[
            {
                "content": "Request approval",
                "status": "waiting_on_approval",
                "priority": "high",
            },
            {"content": "Apply change", "status": "pending", "priority": "medium"},
        ],
    )

    assert replaced == [
        {
            "content": "Request approval",
            "status": "waiting_on_approval",
            "priority": "high",
        },
        {"content": "Apply change", "status": "pending", "priority": "medium"},
    ]
    assert repository.workflows[thread_id] == replaced


async def test_workflow_todo_service_replace_workflow_accepts_empty_list_to_clear_state() -> (
    None
):
    thread_id = uuid4()
    repository = _FakeWorkflowTodoRepository(
        workflows={
            thread_id: [
                {"content": "Apply change", "status": "in_progress", "priority": "high"}
            ]
        }
    )
    service = WorkflowTodoService(repository=repository)

    replaced = await service.replace_workflow(thread_id=thread_id, todos=[])

    assert replaced == []
    assert repository.workflows[thread_id] == []


async def test_workflow_todo_service_clear_workflow_removes_persisted_items() -> None:
    thread_id = uuid4()
    repository = _FakeWorkflowTodoRepository(
        workflows={
            thread_id: [{"content": "Done", "status": "completed", "priority": "high"}]
        }
    )
    service = WorkflowTodoService(repository=repository)

    await service.clear_workflow(thread_id=thread_id)

    assert repository.cleared_thread_ids == [thread_id]
    assert thread_id not in repository.workflows
