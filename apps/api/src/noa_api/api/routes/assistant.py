from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated, Any, Literal
from uuid import UUID

from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.auth.authorization import AuthorizationUser, get_current_auth_user
from noa_api.storage.postgres.client import create_engine, create_session_factory
from noa_api.storage.postgres.models import Message, Thread

router = APIRouter(tags=["assistant"])
_engine = create_engine()
_session_factory = create_session_factory(_engine)


class AssistantMessage(BaseModel):
    role: str
    parts: list[dict[str, Any]]


class AddMessageCommand(BaseModel):
    type: Literal["add-message"]
    message: AssistantMessage
    parent_id: str | None = Field(default=None, alias="parentId")
    source_id: str | None = Field(default=None, alias="sourceId")

    model_config = {"populate_by_name": True}


class ApproveActionCommand(BaseModel):
    type: Literal["approve-action"]
    action_request_id: str | None = Field(default=None, alias="actionRequestId")

    model_config = {"populate_by_name": True}


class DenyActionCommand(BaseModel):
    type: Literal["deny-action"]
    action_request_id: str | None = Field(default=None, alias="actionRequestId")

    model_config = {"populate_by_name": True}


AssistantCommand = Annotated[
    AddMessageCommand | ApproveActionCommand | DenyActionCommand,
    Field(discriminator="type"),
]


class AssistantRequest(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)
    commands: list[AssistantCommand] = Field(default_factory=list)
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    thread_id: UUID = Field(alias="threadId")

    model_config = {"populate_by_name": True}


class SQLAssistantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_thread(self, *, owner_user_id: UUID, thread_id: UUID) -> Thread | None:
        result = await self._session.execute(
            select(Thread).where(Thread.id == thread_id, Thread.owner_user_id == owner_user_id)
        )
        return result.scalar_one_or_none()

    async def list_messages(self, *, thread_id: UUID) -> list[Message]:
        result = await self._session.execute(
            select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at.asc(), Message.id.asc())
        )
        return list(result.scalars().all())

    async def create_message(self, *, thread_id: UUID, role: str, parts: list[dict[str, Any]]) -> Message:
        message = Message(thread_id=thread_id, role=role, content=parts)
        self._session.add(message)
        await self._session.flush()
        return message


class AssistantService:
    def __init__(self, repository: SQLAssistantRepository) -> None:
        self._repository = repository

    async def thread_exists(self, *, owner_user_id: UUID, thread_id: UUID) -> bool:
        return await self._repository.get_thread(owner_user_id=owner_user_id, thread_id=thread_id) is not None

    async def load_state(self, *, owner_user_id: UUID, thread_id: UUID) -> dict[str, object]:
        thread = await self._repository.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if thread is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

        messages = await self._repository.list_messages(thread_id=thread_id)
        return {
            "messages": [
                {
                    "id": str(message.id),
                    "role": message.role,
                    "parts": message.content,
                }
                for message in messages
            ],
            "isRunning": False,
        }

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, Any]],
    ) -> None:
        thread = await self._repository.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if thread is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
        await self._repository.create_message(thread_id=thread_id, role=role, parts=parts)

    async def approve_action(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        _ = owner_user_id, thread_id, action_request_id

    async def deny_action(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        _ = owner_user_id, thread_id, action_request_id


async def get_assistant_service() -> AsyncGenerator[AssistantService, None]:
    async with _session_factory() as session:
        service = AssistantService(SQLAssistantRepository(session))
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _require_active_user(current_user: AuthorizationUser = Depends(get_current_auth_user)) -> AuthorizationUser:
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User pending approval")
    return current_user


@router.post("/assistant")
async def assistant_transport(
    payload: AssistantRequest,
    current_user: AuthorizationUser = Depends(_require_active_user),
    assistant_service: AssistantService = Depends(get_assistant_service),
) -> DataStreamResponse:
    if not await assistant_service.thread_exists(owner_user_id=current_user.user_id, thread_id=payload.thread_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    async def run_callback(controller: RunController) -> None:
        if controller.state is None:
            controller.state = {}

        canonical_state = await assistant_service.load_state(
            owner_user_id=current_user.user_id,
            thread_id=payload.thread_id,
        )
        controller.state["messages"] = canonical_state["messages"]
        controller.state["isRunning"] = True

        for command in payload.commands:
            if isinstance(command, AddMessageCommand):
                await assistant_service.add_message(
                    owner_user_id=current_user.user_id,
                    thread_id=payload.thread_id,
                    role=command.message.role,
                    parts=command.message.parts,
                )
            elif isinstance(command, ApproveActionCommand):
                await assistant_service.approve_action(
                    owner_user_id=current_user.user_id,
                    thread_id=payload.thread_id,
                    action_request_id=command.action_request_id,
                )
            elif isinstance(command, DenyActionCommand):
                await assistant_service.deny_action(
                    owner_user_id=current_user.user_id,
                    thread_id=payload.thread_id,
                    action_request_id=command.action_request_id,
                )

        updated_state = await assistant_service.load_state(
            owner_user_id=current_user.user_id,
            thread_id=payload.thread_id,
        )
        controller.state["messages"] = updated_state["messages"]
        controller.state["isRunning"] = False

    stream = create_run(run_callback, state=payload.state)
    return DataStreamResponse(stream)
