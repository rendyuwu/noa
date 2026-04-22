from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
import logging
from typing import Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import THREAD_NOT_FOUND, USER_PENDING_APPROVAL
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.api.route_telemetry import record_route_outcome
from noa_api.core.logging_context import log_context
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.models import Message, Thread

router = APIRouter(tags=["threads"])

logger = logging.getLogger(__name__)
THREADS_OUTCOMES_TOTAL = "threads.outcomes.total"


class ThreadResponse(BaseModel):
    id: str
    remote_id: str = Field(alias="remoteId")
    external_id: str | None = Field(alias="externalId")
    status: Literal["regular", "archived"]
    title: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True}


class ThreadListResponse(BaseModel):
    threads: list[ThreadResponse]


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    local_id: str | None = Field(default=None, alias="localId", max_length=255)

    model_config = {"populate_by_name": True}

    @field_validator("title", "local_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized == "":
                return None
            return normalized
        return value


class UpdateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized == "":
                return None
            return normalized
        return value


class GenerateTitleRequest(BaseModel):
    messages: list[dict[str, object]] = Field(default_factory=list)


class GenerateTitleResponse(BaseModel):
    title: str


def _extract_text_chunks(value: object) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, dict):
        if value.get("type") == "text":
            text_value = value.get("text")
            if isinstance(text_value, str):
                normalized = text_value.strip()
                return [normalized] if normalized else []
        nested_content = value.get("content")
        if nested_content is not None:
            return _extract_text_chunks(nested_content)
        return []
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_extract_text_chunks(item))
        return chunks
    return []


def _message_text_chunks(message: dict[str, object]) -> list[str]:
    parts = message.get("parts")
    if parts is not None:
        chunks = _extract_text_chunks(parts)
        if chunks:
            return chunks

    content = message.get("content")
    return _extract_text_chunks(content)


class SQLThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_threads(self, *, owner_user_id: UUID) -> list[Thread]:
        result = await self._session.execute(
            select(Thread)
            .where(Thread.owner_user_id == owner_user_id)
            .order_by(Thread.updated_at.desc(), Thread.created_at.desc())
        )
        return list(result.scalars().all())

    async def create_thread(
        self,
        *,
        owner_user_id: UUID,
        title: str | None = None,
        external_id: str | None = None,
    ) -> tuple[Thread, bool]:
        if external_id is not None:
            existing = await self.get_thread_by_external_id(
                owner_user_id=owner_user_id, external_id=external_id
            )
            if existing is not None:
                return existing, False

        thread = Thread(
            owner_user_id=owner_user_id, title=title, external_id=external_id
        )
        self._session.add(thread)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            if external_id is None:
                raise
            existing = await self.get_thread_by_external_id(
                owner_user_id=owner_user_id, external_id=external_id
            )
            if existing is None:
                raise
            return existing, False
        return thread, True

    async def get_thread_by_external_id(
        self, *, owner_user_id: UUID, external_id: str
    ) -> Thread | None:
        result = await self._session.execute(
            select(Thread).where(
                Thread.owner_user_id == owner_user_id, Thread.external_id == external_id
            )
        )
        return result.scalar_one_or_none()

    async def get_thread(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> Thread | None:
        result = await self._session.execute(
            select(Thread).where(
                Thread.id == thread_id, Thread.owner_user_id == owner_user_id
            )
        )
        return result.scalar_one_or_none()

    async def list_messages(
        self, *, owner_user_id: UUID, thread_id: UUID, limit: int = 50
    ) -> list[Message]:
        result = await self._session.execute(
            select(Message)
            .join(Thread, Message.thread_id == Thread.id)
            .where(
                Message.thread_id == thread_id, Thread.owner_user_id == owner_user_id
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_thread_title(
        self, *, owner_user_id: UUID, thread_id: UUID, title: str | None
    ) -> Thread | None:
        thread = await self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if thread is None:
            return None
        thread.title = title
        await self._session.flush()
        return thread

    async def set_thread_title_if_missing(
        self, *, owner_user_id: UUID, thread_id: UUID, title: str
    ) -> bool:
        result = await self._session.execute(
            update(Thread)
            .where(
                Thread.id == thread_id,
                Thread.owner_user_id == owner_user_id,
                Thread.title.is_(None),
            )
            .values(title=title)
            .returning(Thread.id)
        )
        return result.scalar_one_or_none() is not None

    async def set_archived(
        self, *, owner_user_id: UUID, thread_id: UUID, is_archived: bool
    ) -> Thread | None:
        thread = await self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if thread is None:
            return None
        thread.is_archived = is_archived
        await self._session.flush()
        return thread

    async def delete_thread(self, *, owner_user_id: UUID, thread_id: UUID) -> bool:
        result = await self._session.execute(
            delete(Thread)
            .where(Thread.id == thread_id, Thread.owner_user_id == owner_user_id)
            .returning(Thread.id)
        )
        return result.scalar_one_or_none() is not None


class ThreadService:
    def __init__(self, repository: SQLThreadRepository) -> None:
        self._repository = repository

    async def list_threads(self, *, owner_user_id: UUID) -> list[Thread]:
        return await self._repository.list_threads(owner_user_id=owner_user_id)

    async def create_thread(
        self,
        *,
        owner_user_id: UUID,
        title: str | None = None,
        external_id: str | None = None,
    ) -> tuple[Thread, bool]:
        return await self._repository.create_thread(
            owner_user_id=owner_user_id, title=title, external_id=external_id
        )

    async def get_thread(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> Thread | None:
        return await self._repository.get_thread(
            owner_user_id=owner_user_id, thread_id=thread_id
        )

    async def list_thread_messages_for_title(
        self, *, owner_user_id: UUID, thread_id: UUID, limit: int = 50
    ) -> list[dict[str, object]]:
        messages = await self._repository.list_messages(
            owner_user_id=owner_user_id, thread_id=thread_id, limit=limit
        )
        return [
            {
                "role": message.role,
                "parts": message.content,
            }
            for message in messages
        ]

    async def update_thread_title(
        self, *, owner_user_id: UUID, thread_id: UUID, title: str | None
    ) -> Thread | None:
        return await self._repository.update_thread_title(
            owner_user_id=owner_user_id, thread_id=thread_id, title=title
        )

    async def set_thread_title_if_missing(
        self, *, owner_user_id: UUID, thread_id: UUID, title: str
    ) -> bool:
        return await self._repository.set_thread_title_if_missing(
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            title=title,
        )

    async def set_archived(
        self, *, owner_user_id: UUID, thread_id: UUID, is_archived: bool
    ) -> Thread | None:
        return await self._repository.set_archived(
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            is_archived=is_archived,
        )

    async def delete_thread(self, *, owner_user_id: UUID, thread_id: UUID) -> bool:
        return await self._repository.delete_thread(
            owner_user_id=owner_user_id, thread_id=thread_id
        )


def _to_thread_response(thread: Thread) -> ThreadResponse:
    return ThreadResponse(
        id=str(thread.id),
        remoteId=str(thread.id),
        externalId=thread.external_id,
        status="archived" if thread.is_archived else "regular",
        title=thread.title,
        is_archived=thread.is_archived,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _record_thread_outcome(
    request: Request,
    *,
    event_name: str,
    status_code: int,
    trace_attributes: dict[str, str | int | bool],
    error_code: str | None = None,
) -> None:
    record_route_outcome(
        request,
        metric_name=THREADS_OUTCOMES_TOTAL,
        event_name=event_name,
        status_code=status_code,
        trace_attributes=trace_attributes,
        error_code=error_code,
    )


def _raise_thread_not_found(
    request: Request, *, owner_user_id: UUID, thread_id: UUID
) -> NoReturn:
    with log_context(thread_id=str(thread_id), user_id=str(owner_user_id)):
        logger.info("thread_not_found")
    _record_thread_outcome(
        request,
        event_name="thread_not_found",
        status_code=status.HTTP_404_NOT_FOUND,
        trace_attributes={
            "thread_id": str(thread_id),
            "user_id": str(owner_user_id),
        },
        error_code=THREAD_NOT_FOUND,
    )
    raise ApiHTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Thread not found",
        error_code=THREAD_NOT_FOUND,
    )


async def get_thread_service() -> AsyncGenerator[ThreadService, None]:
    async with get_session_factory()() as session:
        service = ThreadService(SQLThreadRepository(session))
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _require_active_user(
    request: Request,
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active:
        with log_context(user_id=str(current_user.user_id)):
            logger.info("threads_access_denied_inactive_user")
        _record_thread_outcome(
            request,
            event_name="threads_access_denied_inactive_user",
            status_code=status.HTTP_403_FORBIDDEN,
            trace_attributes={"user_id": str(current_user.user_id)},
            error_code=USER_PENDING_APPROVAL,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User pending approval",
            error_code=USER_PENDING_APPROVAL,
        )
    return current_user


@router.get("/threads", response_model=ThreadListResponse)
async def list_threads(
    request: Request,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> ThreadListResponse:
    threads = await thread_service.list_threads(owner_user_id=current_user.user_id)
    with log_context(user_id=str(current_user.user_id)):
        logger.info("threads_list_succeeded", extra={"thread_count": len(threads)})
    _record_thread_outcome(
        request,
        event_name="threads_list_succeeded",
        status_code=status.HTTP_200_OK,
        trace_attributes={
            "thread_count": len(threads),
            "user_id": str(current_user.user_id),
        },
    )
    return ThreadListResponse(
        threads=[_to_thread_response(thread) for thread in threads]
    )


@router.post("/threads", response_model=ThreadResponse)
async def create_thread(
    request: Request,
    payload: CreateThreadRequest | None = None,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> ThreadResponse | Response:
    thread, created = await thread_service.create_thread(
        owner_user_id=current_user.user_id,
        title=None if payload is None else payload.title,
        external_id=None if payload is None else payload.local_id,
    )
    with log_context(user_id=str(current_user.user_id), thread_id=str(thread.id)):
        logger.info(
            "thread_created" if created else "thread_reused",
            extra={"external_id_present": thread.external_id is not None},
        )
    _record_thread_outcome(
        request,
        event_name="thread_created" if created else "thread_reused",
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        trace_attributes={
            "external_id_present": thread.external_id is not None,
            "thread_id": str(thread.id),
            "user_id": str(current_user.user_id),
        },
    )
    response = _to_thread_response(thread)
    if created:
        return JSONResponse(
            content=jsonable_encoder(response.model_dump(by_alias=True)),
            status_code=status.HTTP_201_CREATED,
        )
    return response


@router.get("/threads/{id}", response_model=ThreadResponse)
async def get_thread(
    request: Request,
    id: UUID,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> ThreadResponse:
    with log_context(user_id=str(current_user.user_id), thread_id=str(id)):
        thread = await thread_service.get_thread(
            owner_user_id=current_user.user_id, thread_id=id
        )
        if thread is None:
            _raise_thread_not_found(
                request,
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
        logger.info("thread_retrieved")
        _record_thread_outcome(
            request,
            event_name="thread_retrieved",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "thread_id": str(id),
                "user_id": str(current_user.user_id),
            },
        )
        return _to_thread_response(thread)


@router.patch("/threads/{id}", response_model=ThreadResponse)
async def patch_thread(
    request: Request,
    id: UUID,
    payload: UpdateThreadRequest,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> ThreadResponse:
    with log_context(user_id=str(current_user.user_id), thread_id=str(id)):
        thread = await thread_service.update_thread_title(
            owner_user_id=current_user.user_id, thread_id=id, title=payload.title
        )
        if thread is None:
            _raise_thread_not_found(
                request,
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
        logger.info("thread_title_updated")
        _record_thread_outcome(
            request,
            event_name="thread_title_updated",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "thread_id": str(id),
                "user_id": str(current_user.user_id),
            },
        )
        return _to_thread_response(thread)


@router.post("/threads/{id}/archive", response_model=ThreadResponse)
async def archive_thread(
    request: Request,
    id: UUID,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> ThreadResponse:
    with log_context(user_id=str(current_user.user_id), thread_id=str(id)):
        thread = await thread_service.set_archived(
            owner_user_id=current_user.user_id, thread_id=id, is_archived=True
        )
        if thread is None:
            _raise_thread_not_found(
                request,
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
        logger.info("thread_archived")
        _record_thread_outcome(
            request,
            event_name="thread_archived",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "thread_id": str(id),
                "user_id": str(current_user.user_id),
            },
        )
        return _to_thread_response(thread)


@router.post("/threads/{id}/unarchive", response_model=ThreadResponse)
async def unarchive_thread(
    request: Request,
    id: UUID,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> ThreadResponse:
    with log_context(user_id=str(current_user.user_id), thread_id=str(id)):
        thread = await thread_service.set_archived(
            owner_user_id=current_user.user_id, thread_id=id, is_archived=False
        )
        if thread is None:
            _raise_thread_not_found(
                request,
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
        logger.info("thread_unarchived")
        _record_thread_outcome(
            request,
            event_name="thread_unarchived",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "thread_id": str(id),
                "user_id": str(current_user.user_id),
            },
        )
        return _to_thread_response(thread)


@router.delete("/threads/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    request: Request,
    id: UUID,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> Response:
    with log_context(user_id=str(current_user.user_id), thread_id=str(id)):
        deleted = await thread_service.delete_thread(
            owner_user_id=current_user.user_id, thread_id=id
        )
        if not deleted:
            _raise_thread_not_found(
                request,
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
        logger.info("thread_deleted")
        _record_thread_outcome(
            request,
            event_name="thread_deleted",
            status_code=status.HTTP_204_NO_CONTENT,
            trace_attributes={
                "thread_id": str(id),
                "user_id": str(current_user.user_id),
            },
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/threads/{id}/title", response_model=GenerateTitleResponse)
async def generate_thread_title(
    request: Request,
    id: UUID,
    payload: GenerateTitleRequest,
    current_user: AuthorizationUser = Depends(_require_active_user),
    thread_service: ThreadService = Depends(get_thread_service),
) -> GenerateTitleResponse:
    with log_context(user_id=str(current_user.user_id), thread_id=str(id)):
        stored = await thread_service.get_thread(
            owner_user_id=current_user.user_id, thread_id=id
        )
        if stored is None:
            _raise_thread_not_found(
                request,
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
        if stored.title is not None:
            logger.info("thread_title_returned_existing")
            _record_thread_outcome(
                request,
                event_name="thread_title_returned_existing",
                status_code=status.HTTP_200_OK,
                trace_attributes={
                    "thread_id": str(id),
                    "user_id": str(current_user.user_id),
                },
            )
            return GenerateTitleResponse(title=stored.title)

        user_text_chunks: list[str] = []
        fallback_text_chunks: list[str] = []
        for message in payload.messages:
            text_chunks = _message_text_chunks(message)
            if not text_chunks:
                continue
            fallback_text_chunks.extend(text_chunks)
            if message.get("role") == "user":
                user_text_chunks.extend(text_chunks)

        generated_title_source = "request_messages"
        candidate = " ".join(user_text_chunks or fallback_text_chunks).strip()

        if not candidate:
            persisted_messages = await thread_service.list_thread_messages_for_title(
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
            for message in persisted_messages:
                text_chunks = _message_text_chunks(message)
                if not text_chunks:
                    continue
                fallback_text_chunks.extend(text_chunks)
                if message.get("role") == "user":
                    user_text_chunks.extend(text_chunks)
            candidate = " ".join(user_text_chunks or fallback_text_chunks).strip()
            generated_title_source = "persisted_messages"

        title = (candidate[:80] if candidate else "New Thread").strip()

        did_set = await thread_service.set_thread_title_if_missing(
            owner_user_id=current_user.user_id,
            thread_id=id,
            title=title,
        )
        if did_set:
            logger.info(
                "thread_title_generated",
                extra={"generated_title_source": generated_title_source},
            )
            _record_thread_outcome(
                request,
                event_name="thread_title_generated",
                status_code=status.HTTP_200_OK,
                trace_attributes={
                    "generated_title_source": generated_title_source,
                    "thread_id": str(id),
                    "user_id": str(current_user.user_id),
                },
            )
            return GenerateTitleResponse(title=title)

        refreshed = await thread_service.get_thread(
            owner_user_id=current_user.user_id, thread_id=id
        )
        if refreshed is None:
            _raise_thread_not_found(
                request,
                owner_user_id=current_user.user_id,
                thread_id=id,
            )
        logger.info("thread_title_returned_refreshed")
        _record_thread_outcome(
            request,
            event_name="thread_title_returned_refreshed",
            status_code=status.HTTP_200_OK,
            trace_attributes={
                "thread_id": str(id),
                "user_id": str(current_user.user_id),
            },
        )
        return GenerateTitleResponse(title=refreshed.title or title)
