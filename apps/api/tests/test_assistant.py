from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytest.importorskip("assistant_stream")

from noa_api.core.agent.runner import AgentMessage, AgentRunnerResult
from noa_api.api.routes.assistant import get_assistant_service
from noa_api.api.routes.assistant import router as assistant_router
from noa_api.core.auth.authorization import (
    AuthorizationUser,
    get_authorization_service,
    get_current_auth_user,
)


@dataclass
class _FakeAssistantService:
    owner_user_id: UUID
    thread_id: UUID
    messages: list[dict[str, object]] = field(default_factory=list)
    runner_messages: list[AgentMessage] = field(default_factory=list)
    runner_text_deltas: list[str] = field(default_factory=list)
    seen_available_tools: set[str] = field(default_factory=set)

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

    async def add_tool_result(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        tool_call_id: str,
        result: dict[str, object],
    ) -> None:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        _ = tool_call_id, result

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

    async def run_agent_turn(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        available_tool_names: set[str],
    ) -> AgentRunnerResult:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        self.seen_available_tools = set(available_tool_names)
        for message in self.runner_messages:
            self.messages.append({"id": str(uuid4()), "role": message.role, "parts": message.parts})
        return AgentRunnerResult(messages=self.runner_messages, text_deltas=self.runner_text_deltas)


@dataclass
class _FakeAuthorizationService:
    allowed_tools: set[str] = field(default_factory=set)

    async def authorize_tool_access(self, user: AuthorizationUser, tool_name: str) -> bool:
        _ = user
        return tool_name in self.allowed_tools


def _build_app(
    service: _FakeAssistantService,
    current_user: AuthorizationUser,
    authorization_service: _FakeAuthorizationService | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(assistant_router)
    app.dependency_overrides[get_assistant_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: current_user
    app.dependency_overrides[get_authorization_service] = lambda: authorization_service or _FakeAuthorizationService()
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


async def test_assistant_route_rejects_edit_style_add_message() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
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
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "parentId": "parent-1",
                "sourceId": "source-1",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Edited"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Editing existing messages is not supported yet"


async def test_assistant_route_accepts_add_tool_result_command() -> None:
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
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-tool-result",
                "toolCallId": "tool-call-1",
                "result": {"ok": True},
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert "aui-state:" in response.text
    assert "From DB" in response.text


async def test_assistant_route_rejects_unknown_command_type() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
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
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "unknown-command",
                "value": "x",
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 422
    detail = response.json().get("detail")
    assert isinstance(detail, list)
    assert detail[0]["loc"][:2] == ["body", "commands"]


async def test_assistant_route_runs_agent_with_rbac_filtered_tools() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        runner_messages=[
            AgentMessage(
                role="assistant",
                parts=[{"type": "text", "text": "I'll check that for you."}],
            )
        ],
        runner_text_deltas=["I'll check", " that for you."],
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
        authorization_service=_FakeAuthorizationService(allowed_tools={"get_current_time"}),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "What time is it?"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert "I'll check that for you." in response.text
    assert service.seen_available_tools == {"get_current_time"}
