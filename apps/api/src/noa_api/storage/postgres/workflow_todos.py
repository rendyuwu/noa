from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, TypedDict
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.models import WorkflowTodo

WorkflowTodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]
WorkflowTodoPriority = Literal["high", "medium", "low"]


class WorkflowTodoItem(TypedDict):
    content: str
    status: WorkflowTodoStatus
    priority: WorkflowTodoPriority


class WorkflowTodoRepositoryProtocol(Protocol):
    async def replace_workflow(
        self, *, thread_id: UUID, todos: Sequence[WorkflowTodoItem]
    ) -> None: ...

    async def list_workflow(self, *, thread_id: UUID) -> list[WorkflowTodo]: ...

    async def clear_workflow(self, *, thread_id: UUID) -> None: ...


class SQLWorkflowTodoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_workflow(
        self, *, thread_id: UUID, todos: Sequence[WorkflowTodoItem]
    ) -> None:
        await self.clear_workflow(thread_id=thread_id)
        for position, todo in enumerate(todos):
            self._session.add(
                WorkflowTodo(
                    thread_id=thread_id,
                    position=position,
                    content=todo["content"],
                    status=todo["status"],
                    priority=todo["priority"],
                )
            )
        await self._session.flush()

    async def list_workflow(self, *, thread_id: UUID) -> list[WorkflowTodo]:
        result = await self._session.execute(
            select(WorkflowTodo)
            .where(WorkflowTodo.thread_id == thread_id)
            .order_by(WorkflowTodo.position.asc())
        )
        return list(result.scalars().all())

    async def clear_workflow(self, *, thread_id: UUID) -> None:
        await self._session.execute(
            delete(WorkflowTodo).where(WorkflowTodo.thread_id == thread_id)
        )
        await self._session.flush()


class WorkflowTodoService:
    def __init__(self, *, repository: WorkflowTodoRepositoryProtocol) -> None:
        self._repository = repository

    async def replace_workflow(
        self, *, thread_id: UUID, todos: Sequence[WorkflowTodoItem]
    ) -> list[WorkflowTodoItem]:
        normalized: list[WorkflowTodoItem] = [
            {
                "content": todo["content"],
                "status": todo["status"],
                "priority": todo["priority"],
            }
            for todo in todos
        ]
        await self._repository.replace_workflow(thread_id=thread_id, todos=normalized)
        return normalized

    async def list_workflow(self, *, thread_id: UUID) -> list[WorkflowTodoItem]:
        return [
            {
                "content": todo.content,
                "status": todo.status,
                "priority": todo.priority,
            }
            for todo in await self._repository.list_workflow(thread_id=thread_id)
        ]

    async def clear_workflow(self, *, thread_id: UUID) -> None:
        await self._repository.clear_workflow(thread_id=thread_id)
