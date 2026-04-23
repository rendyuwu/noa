from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.models import Message, Thread


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
