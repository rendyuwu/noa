from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytest.importorskip("assistant_stream")

from noa_api.api.routes.assistant import get_assistant_service
from noa_api.api.routes.assistant import router as assistant_router
from noa_api.core.auth.authorization import AuthorizationUser, get_current_auth_user


@dataclass
class _FakeAssistantService:
    owner_user_id: UUID
    thread_id: UUID
    messages: list[dict[str, object]] = field(default_factory=list)

    async def thread_exists(self, *, owner_user_id: UUID, thread_id: UUID) -> bool:
        return owner_user_id == self.owner_user_id and thread_id == self.thread_id

    async def load_state(self, *, owner_user_id: UUID, thread_id: UUID) -> dict[str, object]:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        return {
            "messages": list(self.messages),
            "isRunning": False,
        }

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, object]],
    ) -> None:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        self.messages.append(
            {
                "id": str(uuid4()),
                "role": role,
                "parts": parts,
            }
        )

    async def approve_action(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id

    async def deny_action(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id


def _build_app(service: _FakeAssistantService, current_user: AuthorizationUser) -> FastAPI:
    app = FastAPI()
    app.include_router(assistant_router)
    app.dependency_overrides[get_assistant_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: current_user
    return app


async def test_assistant_route_rejects_missing_thread_id() -> None:
    owner_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=uuid4())
    app = _build_app(
        service,
        AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json={"state": {}, "commands": []})

    assert response.status_code == 422


async def test_assistant_route_streams_canonical_state_and_applies_commands() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        messages=[
            {
                "id": str(uuid4()),
                "role": "assistant",
                "parts": [{"type": "text", "text": "From DB"}],
            }
        ],
    )
    app = _build_app(
        service,
        AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
    )

    payload = {
        "state": {
            "messages": [{"id": "client-only", "role": "assistant", "parts": [{"type": "text", "text": "Bogus"}]}],
            "isRunning": False,
        },
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello from command"}],
                },
            },
            {"type": "approve-action", "actionRequestId": "ar-1"},
            {"type": "deny-action", "actionRequestId": "ar-2"},
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert "aui-state:" in response.text
    assert "From DB" in response.text
    assert "Hello from command" in response.text
    assert "Bogus" not in response.text
    assert '"isRunning":false' in response.text
