from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.lifecycle import ActionRequestStatus, AssistantRunStatus
from noa_api.storage.postgres.models import (
    ActionRequest,
    AssistantRun,
    AuditLog,
    Message,
    Thread,
    ToolRun,
)


ACTIVE_RUN_STATUSES = (
    AssistantRunStatus.STARTING,
    AssistantRunStatus.RUNNING,
    AssistantRunStatus.WAITING_APPROVAL,
)

RUNNABLE_RUN_STATUSES = (
    AssistantRunStatus.STARTING,
    AssistantRunStatus.WAITING_APPROVAL,
)


async def _execute_run_update(
    session: AsyncSession,
    *,
    run_id: UUID,
    conditions: tuple[ColumnElement[bool], ...],
    values: dict[str, object],
) -> AssistantRun | None:
    result = await session.execute(
        update(AssistantRun)
        .where(AssistantRun.id == run_id, *conditions)
        .values(**values)
        .returning(AssistantRun)
    )
    await session.flush()
    return result.scalar_one_or_none()


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

    async def create_assistant_run(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        owner_instance_id: str,
    ) -> AssistantRun:
        run = AssistantRun(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            status=AssistantRunStatus.STARTING,
            owner_instance_id=owner_instance_id,
            sequence=0,
            live_snapshot={},
            blocking_action_request_id=None,
            last_error_reason=None,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_assistant_run(self, *, run_id: UUID) -> AssistantRun | None:
        result = await self._session.execute(
            select(AssistantRun).where(AssistantRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def get_active_run(self, *, thread_id: UUID) -> AssistantRun | None:
        result = await self._session.execute(
            select(AssistantRun)
            .where(
                AssistantRun.thread_id == thread_id,
                AssistantRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .order_by(AssistantRun.created_at.desc(), AssistantRun.id.desc())
        )
        return result.scalar_one_or_none()

    async def mark_run_running(self, *, run_id: UUID) -> AssistantRun | None:
        return await _execute_run_update(
            self._session,
            run_id=run_id,
            conditions=(AssistantRun.status.in_(RUNNABLE_RUN_STATUSES),),
            values={
                "status": AssistantRunStatus.RUNNING,
                "blocking_action_request_id": None,
                "last_error_reason": None,
            },
        )

    async def mark_run_waiting_approval(
        self, *, run_id: UUID, action_request_id: UUID
    ) -> AssistantRun | None:
        return await _execute_run_update(
            self._session,
            run_id=run_id,
            conditions=(AssistantRun.status.in_(ACTIVE_RUN_STATUSES),),
            values={
                "status": AssistantRunStatus.WAITING_APPROVAL,
                "blocking_action_request_id": action_request_id,
                "last_error_reason": None,
            },
        )

    async def append_run_snapshot(
        self, *, run_id: UUID, snapshot: Mapping[str, object]
    ) -> AssistantRun | None:
        return await _execute_run_update(
            self._session,
            run_id=run_id,
            conditions=(AssistantRun.status.in_(ACTIVE_RUN_STATUSES),),
            values={
                "sequence": AssistantRun.sequence + 1,
                "live_snapshot": dict(snapshot),
            },
        )

    async def mark_run_completed(self, *, run_id: UUID) -> AssistantRun | None:
        return await _execute_run_update(
            self._session,
            run_id=run_id,
            conditions=(AssistantRun.status.in_(ACTIVE_RUN_STATUSES),),
            values={
                "status": AssistantRunStatus.COMPLETED,
                "blocking_action_request_id": None,
            },
        )

    async def mark_run_failed(
        self, *, run_id: UUID, reason: str
    ) -> AssistantRun | None:
        return await _execute_run_update(
            self._session,
            run_id=run_id,
            conditions=(AssistantRun.status.in_(ACTIVE_RUN_STATUSES),),
            values={
                "status": AssistantRunStatus.FAILED,
                "last_error_reason": reason,
                "blocking_action_request_id": None,
            },
        )

    async def fail_run_if_owner_matches(
        self,
        *,
        run_id: UUID,
        owner_instance_id: str,
        reason: str,
    ) -> AssistantRun | None:
        result = await self._session.execute(
            update(AssistantRun)
            .where(
                AssistantRun.id == run_id,
                AssistantRun.owner_instance_id == owner_instance_id,
                AssistantRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .values(
                status=AssistantRunStatus.FAILED,
                last_error_reason=reason,
                blocking_action_request_id=None,
            )
            .returning(AssistantRun)
        )
        await self._session.flush()
        return result.scalar_one_or_none()

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
