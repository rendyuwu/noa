from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from noa_api.api.routes.threads import ThreadService, router as threads_router
from noa_api.api.routes.threads import get_thread_service
from noa_api.core.auth.authorization import AuthorizationUser, get_current_auth_user


@dataclass
class _ThreadRecord:
    id: UUID
    owner_user_id: UUID
    external_id: str | None
    title: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class _FakeThreadService(ThreadService):
    def __init__(self) -> None:
        self._threads: dict[UUID, _ThreadRecord] = {}
        self._thread_by_external_id: dict[tuple[UUID, str], UUID] = {}

    async def list_threads(self, *, owner_user_id: UUID) -> list[_ThreadRecord]:
        return sorted(
            [record for record in self._threads.values() if record.owner_user_id == owner_user_id],
            key=lambda record: record.created_at,
            reverse=True,
        )

    async def create_thread(
        self,
        *,
        owner_user_id: UUID,
        title: str | None = None,
        external_id: str | None = None,
    ) -> tuple[_ThreadRecord, bool]:
        normalized_external_id = external_id.strip() if external_id is not None else None
        if normalized_external_id:
            existing_id = self._thread_by_external_id.get((owner_user_id, normalized_external_id))
            if existing_id is not None:
                return self._threads[existing_id], False

        now = datetime.now(UTC)
        record = _ThreadRecord(
            id=uuid4(),
            owner_user_id=owner_user_id,
            external_id=normalized_external_id,
            title=title,
            is_archived=False,
            created_at=now,
            updated_at=now,
        )
        self._threads[record.id] = record
        if normalized_external_id:
            self._thread_by_external_id[(owner_user_id, normalized_external_id)] = record.id
        return record, True

    async def get_thread(self, *, owner_user_id: UUID, thread_id: UUID) -> _ThreadRecord | None:
        record = self._threads.get(thread_id)
        if record is None or record.owner_user_id != owner_user_id:
            return None
        return record

    async def update_thread_title(self, *, owner_user_id: UUID, thread_id: UUID, title: str | None) -> _ThreadRecord | None:
        record = await self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if record is None:
            return None
        record.title = title
        record.updated_at = datetime.now(UTC)
        return record

    async def set_archived(self, *, owner_user_id: UUID, thread_id: UUID, is_archived: bool) -> _ThreadRecord | None:
        record = await self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if record is None:
            return None
        record.is_archived = is_archived
        record.updated_at = datetime.now(UTC)
        return record

    async def delete_thread(self, *, owner_user_id: UUID, thread_id: UUID) -> bool:
        record = await self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if record is None:
            return False
        if record.external_id is not None:
            self._thread_by_external_id.pop((owner_user_id, record.external_id), None)
        del self._threads[thread_id]
        return True


async def test_threads_routes_enforce_owner_scoping() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    other_id = uuid4()
    owner_thread, _ = await service.create_thread(owner_user_id=owner_id, title="Owner thread")
    other_thread, _ = await service.create_thread(owner_user_id=other_id, title="Other thread")

    current_user = AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/threads")
        get_other_response = await client.get(f"/threads/{other_thread.id}")
        patch_other_response = await client.patch(f"/threads/{other_thread.id}", json={"title": "Nope"})
        archive_other_response = await client.post(f"/threads/{other_thread.id}/archive")
        delete_other_response = await client.delete(f"/threads/{other_thread.id}")

    assert list_response.status_code == 200
    threads = list_response.json()["threads"]
    assert [item["id"] for item in threads] == [str(owner_thread.id)]

    assert get_other_response.status_code == 404
    assert patch_other_response.status_code == 404
    assert archive_other_response.status_code == 404
    assert delete_other_response.status_code == 404


async def test_threads_routes_archive_unarchive_and_delete() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    current_user = AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post("/threads", json={"title": "Initial title"})
        assert create_response.status_code == 201

        created = create_response.json()
        thread_id = created["id"]
        assert created["remoteId"] == thread_id
        assert created["status"] == "regular"
        assert created["title"] == "Initial title"
        assert created["is_archived"] is False

        archived_response = await client.post(f"/threads/{thread_id}/archive")
        assert archived_response.status_code == 200
        assert archived_response.json()["is_archived"] is True
        assert archived_response.json()["status"] == "archived"

        unarchived_response = await client.post(f"/threads/{thread_id}/unarchive")
        assert unarchived_response.status_code == 200
        assert unarchived_response.json()["is_archived"] is False
        assert unarchived_response.json()["status"] == "regular"

        delete_response = await client.delete(f"/threads/{thread_id}")
        assert delete_response.status_code == 204

        get_response = await client.get(f"/threads/{thread_id}")
        assert get_response.status_code == 404


async def test_threads_routes_deny_inactive_user() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="inactive@example.com",
        display_name="Inactive",
        is_active=False,
        roles=["member"],
        tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/threads")
        create_response = await client.post("/threads", json={"title": "Blocked"})

    assert list_response.status_code == 403
    assert create_response.status_code == 403


async def test_threads_routes_initialize_is_idempotent_per_user_local_id() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_response = await client.post("/threads", json={"localId": "local-123", "title": "First"})
        second_response = await client.post("/threads", json={"localId": "local-123", "title": "Second"})
        list_response = await client.get("/threads")

    assert first_response.status_code == 201
    assert second_response.status_code == 200

    first = first_response.json()
    second = second_response.json()
    assert second["id"] == first["id"]
    assert second["remoteId"] == first["remoteId"]
    assert second["externalId"] == "local-123"

    threads = list_response.json()["threads"]
    assert len(threads) == 1
    assert threads[0]["id"] == first["id"]
    assert threads[0]["externalId"] == "local-123"


async def test_threads_routes_reject_oversized_title() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/threads", json={"title": "x" * 256})

    assert response.status_code == 422


async def test_threads_title_endpoint_generates_title_for_owner_thread() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    thread, _ = await service.create_thread(owner_user_id=owner_id, title=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/threads/{thread.id}/title",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Summarize quarterly operations report and risks",
                            }
                        ],
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Summarize quarterly operations report and risks"


async def test_threads_title_endpoint_persists_generated_title_for_later_list_fetch() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    thread, _ = await service.create_thread(owner_user_id=owner_id, title=None)
    expected_title = "Create an incident response runbook"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_response = await client.post(
            f"/threads/{thread.id}/title",
            json={"messages": [{"role": "user", "content": expected_title}]},
        )
        list_response = await client.get("/threads")

    assert title_response.status_code == 200
    assert title_response.json()["title"] == expected_title

    assert list_response.status_code == 200
    threads = list_response.json()["threads"]
    assert len(threads) == 1
    assert threads[0]["id"] == str(thread.id)
    assert threads[0]["title"] == expected_title


async def test_threads_title_endpoint_returns_404_for_missing_thread() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/threads/{uuid4()}/title",
            json={"messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}]},
        )

    assert response.status_code == 404


async def test_threads_title_endpoint_supports_parts_message_shape() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    thread, _ = await service.create_thread(owner_user_id=owner_id, title=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/threads/{thread.id}/title",
            json={
                "messages": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "type": "text",
                                "text": "Plan migration timeline and owner communication",
                            }
                        ],
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Plan migration timeline and owner communication"


async def test_threads_title_endpoint_supports_string_content_shape() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    thread, _ = await service.create_thread(owner_user_id=owner_id, title=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/threads/{thread.id}/title",
            json={"messages": [{"role": "user", "content": "Need a deployment rollback checklist"}]},
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Need a deployment rollback checklist"
