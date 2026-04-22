from __future__ import annotations

from uuid import UUID

from noa_api.api.threads.repository import SQLThreadRepository
from noa_api.storage.postgres.models import Thread


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
