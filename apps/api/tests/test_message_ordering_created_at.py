from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from noa_api.api.routes.assistant_repository import SQLAssistantRepository


@dataclass
class _FakeSession:
    added: list[object] = field(default_factory=list)

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        return None


async def test_sql_assistant_repository_sets_message_created_at() -> None:
    session = _FakeSession()
    repo = SQLAssistantRepository(session)  # type: ignore[arg-type]

    created = await repo.create_message(
        thread_id=uuid4(),
        role="user",
        parts=[{"type": "text", "text": "Hi"}],
    )

    assert isinstance(created.created_at, datetime)
    assert created.created_at.tzinfo is not None
    assert created.created_at.tzinfo.utcoffset(created.created_at) == UTC.utcoffset(
        created.created_at
    )
