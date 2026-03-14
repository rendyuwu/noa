from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from noa_api.api.error_handling import install_error_handling
from noa_api.api.routes.threads import get_thread_service, router as threads_router
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


class _FakeThreadService:
    def __init__(self) -> None:
        self._threads: dict[UUID, _ThreadRecord] = {}
        self._thread_by_external_id: dict[tuple[UUID, str], UUID] = {}

    async def list_threads(self, *, owner_user_id: UUID) -> list[_ThreadRecord]:
        return sorted(
            [
                record
                for record in self._threads.values()
                if record.owner_user_id == owner_user_id
            ],
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
        normalized_external_id = (
            external_id.strip() if external_id is not None else None
        )
        if normalized_external_id:
            existing_id = self._thread_by_external_id.get(
                (owner_user_id, normalized_external_id)
            )
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
            self._thread_by_external_id[(owner_user_id, normalized_external_id)] = (
                record.id
            )
        return record, True

    async def get_thread(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> _ThreadRecord | None:
        record = self._threads.get(thread_id)
        if record is None or record.owner_user_id != owner_user_id:
            return None
        return record

    async def update_thread_title(
        self, *, owner_user_id: UUID, thread_id: UUID, title: str | None
    ) -> _ThreadRecord | None:
        record = await self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if record is None:
            return None
        record.title = title
        record.updated_at = datetime.now(UTC)
        return record

    async def set_thread_title_if_missing(
        self, *, owner_user_id: UUID, thread_id: UUID, title: str
    ) -> bool:
        record = await self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if record is None or record.title is not None:
            return False
        record.title = title
        record.updated_at = datetime.now(UTC)
        return True

    async def set_archived(
        self, *, owner_user_id: UUID, thread_id: UUID, is_archived: bool
    ) -> _ThreadRecord | None:
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


def _create_threads_app() -> FastAPI:
    app = FastAPI()
    install_error_handling(app)
    app.include_router(threads_router)
    return app


async def test_threads_routes_enforce_owner_scoping() -> None:
    app = _create_threads_app()

    service = _FakeThreadService()
    owner_id = uuid4()
    other_id = uuid4()
    owner_thread, _ = await service.create_thread(
        owner_user_id=owner_id, title="Owner thread"
    )
    other_thread, _ = await service.create_thread(
        owner_user_id=other_id, title="Other thread"
    )

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
        get_other_response = await client.get(
            f"/threads/{other_thread.id}",
            headers={"x-request-id": "threads-missing-thread"},
        )
        patch_other_response = await client.patch(
            f"/threads/{other_thread.id}", json={"title": "Nope"}
        )
        archive_other_response = await client.post(
            f"/threads/{other_thread.id}/archive"
        )
        delete_other_response = await client.delete(f"/threads/{other_thread.id}")

    assert list_response.status_code == 200
    threads = list_response.json()["threads"]
    assert [item["id"] for item in threads] == [str(owner_thread.id)]

    assert get_other_response.status_code == 404
    get_other_body = get_other_response.json()
    assert get_other_body["detail"] == "Thread not found"
    assert get_other_body["error_code"] == "thread_not_found"
    assert get_other_body["request_id"] == "threads-missing-thread"
    assert get_other_response.headers["x-request-id"] == get_other_body["request_id"]
    assert patch_other_response.status_code == 404
    assert archive_other_response.status_code == 404
    assert delete_other_response.status_code == 404


async def test_threads_routes_archive_unarchive_and_delete() -> None:
    app = _create_threads_app()

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
    app = _create_threads_app()

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
        list_response = await client.get(
            "/threads", headers={"x-request-id": "threads-pending-approval"}
        )
        create_response = await client.post("/threads", json={"title": "Blocked"})

    assert list_response.status_code == 403
    list_body = list_response.json()
    assert list_body["detail"] == "User pending approval"
    assert list_body["error_code"] == "user_pending_approval"
    assert list_body["request_id"] == "threads-pending-approval"
    assert list_response.headers["x-request-id"] == list_body["request_id"]
    assert create_response.status_code == 403


async def test_threads_routes_initialize_is_idempotent_per_user_local_id() -> None:
    app = _create_threads_app()

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
        first_response = await client.post(
            "/threads", json={"localId": "local-123", "title": "First"}
        )
        second_response = await client.post(
            "/threads", json={"localId": "local-123", "title": "Second"}
        )
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
    app = _create_threads_app()

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
    app = _create_threads_app()

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


async def test_threads_title_endpoint_persists_generated_title_for_later_list_fetch() -> (
    None
):
    app = _create_threads_app()

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
    threads_by_id = {item["id"]: item for item in threads}
    assert str(thread.id) in threads_by_id
    assert threads_by_id[str(thread.id)]["title"] == expected_title


async def test_threads_title_endpoint_does_not_overwrite_title_set_via_patch() -> None:
    app = _create_threads_app()

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
    renamed_title = "User renamed title"
    generated_title = "Need a deployment rollback checklist"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        patch_response = await client.patch(
            f"/threads/{thread.id}", json={"title": renamed_title}
        )
        title_response = await client.post(
            f"/threads/{thread.id}/title",
            json={"messages": [{"role": "user", "content": generated_title}]},
        )
        list_response = await client.get("/threads")

    assert patch_response.status_code == 200
    assert patch_response.json()["title"] == renamed_title

    assert title_response.status_code == 200
    assert title_response.json()["title"] == renamed_title

    assert list_response.status_code == 200
    threads = list_response.json()["threads"]
    threads_by_id = {item["id"]: item for item in threads}
    assert str(thread.id) in threads_by_id
    assert threads_by_id[str(thread.id)]["title"] == renamed_title


async def test_threads_title_endpoint_returns_stored_title_when_set_during_generation() -> (
    None
):
    app = _create_threads_app()

    class _RaceyThreadService(_FakeThreadService):
        def __init__(self) -> None:
            super().__init__()
            self._pending_rename: dict[UUID, str] = {}

        def rename_before_next_write(self, *, thread_id: UUID, title: str) -> None:
            self._pending_rename[thread_id] = title

        async def update_thread_title(
            self, *, owner_user_id: UUID, thread_id: UUID, title: str | None
        ) -> _ThreadRecord | None:
            pending = self._pending_rename.pop(thread_id, None)
            if pending is not None:
                record = await self.get_thread(
                    owner_user_id=owner_user_id, thread_id=thread_id
                )
                if record is not None:
                    record.title = pending
                    record.updated_at = datetime.now(UTC)
            return await super().update_thread_title(
                owner_user_id=owner_user_id, thread_id=thread_id, title=title
            )

        async def set_thread_title_if_missing(
            self, *, owner_user_id: UUID, thread_id: UUID, title: str
        ) -> bool:
            pending = self._pending_rename.pop(thread_id, None)
            if pending is not None:
                record = await self.get_thread(
                    owner_user_id=owner_user_id, thread_id=thread_id
                )
                if record is not None:
                    record.title = pending
                    record.updated_at = datetime.now(UTC)
            return await super().set_thread_title_if_missing(
                owner_user_id=owner_user_id, thread_id=thread_id, title=title
            )

    service = _RaceyThreadService()
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
    stored_title = "Renamed in-flight"
    generated_title = "Create an incident response runbook"
    service.rename_before_next_write(thread_id=thread.id, title=stored_title)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_response = await client.post(
            f"/threads/{thread.id}/title",
            json={"messages": [{"role": "user", "content": generated_title}]},
        )
        list_response = await client.get("/threads")

    assert title_response.status_code == 200
    assert title_response.json()["title"] == stored_title

    assert list_response.status_code == 200
    threads = list_response.json()["threads"]
    threads_by_id = {item["id"]: item for item in threads}
    assert str(thread.id) in threads_by_id
    assert threads_by_id[str(thread.id)]["title"] == stored_title


async def test_threads_title_endpoint_returns_404_for_missing_thread() -> None:
    app = _create_threads_app()

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
            json={
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Hi"}]}
                ]
            },
        )

    assert response.status_code == 404


async def test_threads_title_endpoint_supports_parts_message_shape() -> None:
    app = _create_threads_app()

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
    app = _create_threads_app()

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
                    {"role": "user", "content": "Need a deployment rollback checklist"}
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Need a deployment rollback checklist"
