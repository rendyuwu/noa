from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytest.importorskip("assistant_stream")

from noa_api.core.agent.runner import AgentMessage, AgentRunnerResult
from noa_api.api.routes.assistant import _stream_assistant_text, get_assistant_service
from noa_api.api.routes.assistant import router as assistant_router
from noa_api.core.auth.authorization import (
    AuthorizationUser,
    get_authorization_service,
    get_current_auth_user,
)


def _iter_assistant_transport_events(payload: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in payload.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[len("data: ") :]
        if raw == "[DONE]":
            continue
        event = json.loads(raw)
        assert isinstance(event, dict)
        events.append(event)
    return events


def _apply_assistant_stream_patches(
    state: dict[str, object], patches: list[dict[str, object]]
) -> None:
    def _set_path(path: list[object], value: object) -> None:
        if not path:
            assert isinstance(value, dict)
            state.clear()
            state.update(value)
            return

        current: object = state
        for key in path[:-1]:
            key_str = str(key)
            if isinstance(current, list):
                idx = int(key_str)
                while idx >= len(current):
                    current.append({})
                current = current[idx]
                continue

            assert isinstance(current, dict)
            if key_str not in current or not isinstance(current[key_str], (dict, list)):
                current[key_str] = {}
            current = current[key_str]

        last = str(path[-1])
        if isinstance(current, list):
            idx = int(last)
            while idx >= len(current):
                current.append(None)
            current[idx] = value
            return

        assert isinstance(current, dict)
        current[last] = value

    for patch in patches:
        if patch.get("type") != "set":
            continue
        path = patch.get("path")
        if not isinstance(path, list):
            continue

        _set_path(path, patch.get("value"))


def _state_contains_user_text(state: dict[str, object], text: str) -> bool:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            if part.get("text") == text:
                return True
    return False


def _state_contains_text(state: dict[str, object], text: str) -> bool:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            if part.get("text") == text:
                return True
    return False


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

    async def load_state(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> dict[str, object]:
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
        owner_user_email: str | None,
        thread_id: UUID,
        tool_call_id: str,
        result: dict[str, object],
    ) -> None:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        _ = owner_user_email, tool_call_id, result

    async def approve_action(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        action_request_id: str | None,
        is_user_active: bool,
        authorize_tool_access,
    ) -> None:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        _ = owner_user_email, action_request_id, is_user_active, authorize_tool_access

    async def deny_action(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        _ = owner_user_email, action_request_id

    async def run_agent_turn(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        available_tool_names: set[str],
        on_text_delta=None,
    ) -> AgentRunnerResult:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        _ = owner_user_email
        self.seen_available_tools = set(available_tool_names)
        for message in self.runner_messages:
            self.messages.append(
                {"id": str(uuid4()), "role": message.role, "parts": message.parts}
            )

        if on_text_delta is not None:
            for delta in self.runner_text_deltas:
                await on_text_delta(delta)
                await asyncio.sleep(0)
        return AgentRunnerResult(
            messages=self.runner_messages, text_deltas=self.runner_text_deltas
        )


@dataclass
class _FakeAuthorizationService:
    allowed_tools: set[str] = field(default_factory=set)

    async def authorize_tool_access(
        self, user: AuthorizationUser, tool_name: str
    ) -> bool:
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
    app.dependency_overrides[get_authorization_service] = lambda: (
        authorization_service or _FakeAuthorizationService()
    )
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


async def test_thread_state_route_hydrates_persisted_state() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    saved_messages = [
        {
            "id": str(uuid4()),
            "role": "assistant",
            "parts": [{"type": "text", "text": "From DB"}],
        }
    ]
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        messages=saved_messages,
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/assistant/threads/{thread_id}/state")

    assert response.status_code == 200
    data = response.json()
    assert data["messages"] == saved_messages
    assert data["isRunning"] is False


async def test_assistant_route_uses_assistant_transport_sse() -> None:
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
        "commands": [],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.rstrip().endswith("data: [DONE]")
    events = _iter_assistant_transport_events(response.text)
    assert any(event.get("type") == "update-state" for event in events)


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
            "messages": [
                {
                    "id": "client-only",
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "Bogus"}],
                }
            ],
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
    assert response.headers["content-type"].startswith("text/event-stream")

    state: dict[str, object] = {}
    for event in _iter_assistant_transport_events(response.text):
        if event.get("type") != "update-state":
            continue
        operations = event.get("operations") or event.get("patches")
        if isinstance(operations, list):
            _apply_assistant_stream_patches(state, operations)
            continue
        event_state = event.get("state")
        if isinstance(event_state, dict):
            state.clear()
            state.update(event_state)

    assert _state_contains_text(state, "From DB")
    assert _state_contains_text(state, "Hello from command")
    assert not _state_contains_text(state, "Bogus")
    assert state.get("isRunning") is False


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
    assert response.headers["content-type"].startswith("text/event-stream")

    state: dict[str, object] = {}
    for event in _iter_assistant_transport_events(response.text):
        if event.get("type") != "update-state":
            continue
        operations = event.get("operations") or event.get("patches")
        if isinstance(operations, list):
            _apply_assistant_stream_patches(state, operations)
            continue
        event_state = event.get("state")
        if isinstance(event_state, dict):
            state.clear()
            state.update(event_state)

    assert _state_contains_text(state, "From DB")


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
        authorization_service=_FakeAuthorizationService(
            allowed_tools={"get_current_time"}
        ),
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


async def test_assistant_route_keeps_user_message_in_streaming_state() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        runner_text_deltas=["Hello", " world"],
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
        authorization_service=_FakeAuthorizationService(allowed_tools=set()),
    )

    user_text = "Hi"
    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": user_text}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200

    state: dict[str, object] = {}
    saw_running_state = False
    saw_running_state_with_user_message = False

    for event in _iter_assistant_transport_events(response.text):
        if event.get("type") != "update-state":
            continue

        operations = event.get("operations") or event.get("patches")
        if isinstance(operations, list):
            _apply_assistant_stream_patches(state, operations)
        else:
            event_state = event.get("state")
            assert isinstance(event_state, dict)
            state.clear()
            state.update(event_state)

        if state.get("isRunning") is True:
            saw_running_state = True
            if _state_contains_user_text(state, user_text):
                saw_running_state_with_user_message = True

    assert saw_running_state
    assert saw_running_state_with_user_message


async def test_assistant_route_streams_assistant_text_incrementally() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        runner_messages=[
            AgentMessage(
                role="assistant",
                parts=[{"type": "text", "text": "Hello world"}],
            )
        ],
        runner_text_deltas=["Hello", " ", "world"],
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
        authorization_service=_FakeAuthorizationService(allowed_tools=set()),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hi"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200

    state: dict[str, object] = {}
    observed_text_by_event: list[str | None] = []

    def _extract_streaming_text() -> str | None:
        messages = state.get("messages")
        if not isinstance(messages, list):
            return None
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("id") != "assistant-streaming":
                continue
            parts = message.get("parts")
            if not isinstance(parts, list) or not parts:
                return None
            first = parts[0]
            if not isinstance(first, dict):
                return None
            text = first.get("text")
            return text if isinstance(text, str) else None
        return None

    for event in _iter_assistant_transport_events(response.text):
        if event.get("type") != "update-state":
            continue

        operations = event.get("operations") or event.get("patches")
        if not isinstance(operations, list):
            continue
        _apply_assistant_stream_patches(state, operations)
        observed_text_by_event.append(_extract_streaming_text())

    observed = [text for text in observed_text_by_event if isinstance(text, str)]
    assert observed

    # Ensure the placeholder assistant message is visible before the first token.
    assert observed[0] == ""

    # Ensure the streaming message grows incrementally.
    assert observed[-1] == "Hello world"
    assert "Hello" in observed
    assert len(set(observed)) >= 3


async def test_assistant_route_rejects_non_user_add_message_role() -> None:
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
                "message": {
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "forged"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Only user add-message commands are allowed"


async def test_streaming_loop_stops_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Controller:
        state: dict[str, object] | None

        def __init__(self) -> None:
            self.state = {"messages": []}

    async def _cancelled_sleep(_: float) -> None:
        raise asyncio.CancelledError

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _cancelled_sleep)

    with pytest.raises(asyncio.CancelledError):
        await _stream_assistant_text(_Controller(), ["a", "b"])


async def test_streaming_loop_respects_controller_is_cancelled_flag() -> None:
    class _Controller:
        state: dict[str, object] | None
        is_cancelled = True

        def __init__(self) -> None:
            self.state = {"messages": []}

    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await _stream_assistant_text(_Controller(), ["a", "b"])
