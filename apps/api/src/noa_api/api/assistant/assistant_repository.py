from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.lifecycle import ActionRequestStatus
from noa_api.storage.postgres.models import (
    ActionRequest,
    AuditLog,
    Message,
    Thread,
    ToolRun,
)


class SQLAssistantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_thread(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> Thread | None:
        result = await self._session.execute(
            select(Thread).where(
                Thread.id == thread_id, Thread.owner_user_id == owner_user_id
            )
        )
        return result.scalar_one_or_none()

    async def list_messages(self, *, thread_id: UUID) -> list[Message]:
        result = await self._session.execute(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        return list(result.scalars().all())

    async def get_pending_action_requests(
        self, *, thread_id: UUID
    ) -> list[ActionRequest]:
        result = await self._session.execute(
            select(ActionRequest)
            .where(
                ActionRequest.thread_id == thread_id,
                ActionRequest.status == ActionRequestStatus.PENDING,
            )
            .order_by(ActionRequest.created_at.asc(), ActionRequest.id.asc())
        )
        return list(result.scalars().all())

    async def list_action_requests(self, *, thread_id: UUID) -> list[ActionRequest]:
        result = await self._session.execute(
            select(ActionRequest)
            .where(ActionRequest.thread_id == thread_id)
            .order_by(ActionRequest.created_at.asc(), ActionRequest.id.asc())
        )
        return list(result.scalars().all())

    async def list_action_tool_runs(self, *, thread_id: UUID) -> list[ToolRun]:
        result = await self._session.execute(
            select(ToolRun)
            .where(
                ToolRun.thread_id == thread_id,
                ToolRun.action_request_id.is_not(None),
            )
            .order_by(ToolRun.created_at.asc(), ToolRun.id.asc())
        )
        return list(result.scalars().all())

    async def create_message(
        self, *, thread_id: UUID, role: str, parts: list[dict[str, object]]
    ) -> Message:
        message = Message(
            thread_id=thread_id,
            role=role,
            content=parts,
            created_at=datetime.now(UTC),
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            AuditLog(
                event_type=event_type,
                user_email=actor_email,
                tool_name=tool_name,
                meta_data=metadata,
            )
        )
        await self._session.flush()
