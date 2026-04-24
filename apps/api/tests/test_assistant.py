from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

pytest.importorskip("assistant_stream")

from noa_api.api.auth_dependencies import get_active_current_auth_user
from noa_api.api.assistant.assistant_errors import assistant_domain_error
from noa_api.api.error_handling import install_error_handling
from noa_api.api.assistant.service import AssistantService
from noa_api.api.routes.assistant import (
    _stream_assistant_text,
    get_assistant_run_coordinator,
    get_assistant_service,
)
from noa_api.api.assistant.assistant_runs import AssistantRunCoordinator
from noa_api.core.agent.runner import AgentMessage, AgentRunnerResult
from noa_api.api.routes.assistant import router as assistant_router
from noa_api.core.auth.authorization import (
    AuthorizationUser,
    get_authorization_service,
)
from noa_api.core.logging import configure_logging
from noa_api.core.telemetry import TelemetryEvent
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import AssistantRunStatus


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
                current_list = cast(list[object], current)
                idx = int(key_str)
                while idx >= len(current_list):
                    current_list.append({})
                current = current_list[idx]
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


@contextmanager
def _capture_structured_logs() -> Iterator[io.StringIO]:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_formatters = {
        id(handler): handler.formatter for handler in original_handlers
    }
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    try:
        configure_logging()
        yield stream
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for existing_handler in original_handlers:
            existing_handler.setFormatter(original_formatters[id(existing_handler)])


def _load_log_payloads(stream: io.StringIO) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in stream.getvalue().splitlines()
        if line.strip()
    ]


class RecordingTelemetryRecorder:
    def __init__(self) -> None:
        self.trace_events: list[TelemetryEvent] = []
        self.metric_events: list[tuple[TelemetryEvent, int | float]] = []
        self.report_events: list[tuple[TelemetryEvent, str | None]] = []

    def trace(self, event: TelemetryEvent) -> None:
        self.trace_events.append(event)

    def metric(self, event: TelemetryEvent, *, value: int | float) -> None:
        self.metric_events.append((event, value))

    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None:
        self.report_events.append((event, detail))


@dataclass
class _FakeAssistantService:
    owner_user_id: UUID
    thread_id: UUID
    messages: list[dict[str, object]] = field(default_factory=list)
    workflow: list[dict[str, object]] = field(default_factory=list)
    pending_approvals: list[dict[str, object]] = field(default_factory=list)
    action_requests: list[dict[str, object]] = field(default_factory=list)
    runner_messages: list[AgentMessage] = field(default_factory=list)
    runner_text_deltas: list[str] = field(default_factory=list)
    seen_available_tools: set[str] = field(default_factory=set)
    active_runs: dict[UUID, SimpleNamespace] = field(default_factory=dict)

    def _get_active_run_for_thread(self, *, thread_id: UUID) -> SimpleNamespace | None:
        for run in self.active_runs.values():
            if run.thread_id != thread_id:
                continue
            if run.status not in {
                AssistantRunStatus.STARTING,
                AssistantRunStatus.RUNNING,
                AssistantRunStatus.WAITING_APPROVAL,
            }:
                continue
            return run
        return None

    async def thread_exists(self, *, owner_user_id: UUID, thread_id: UUID) -> bool:
        return owner_user_id == self.owner_user_id and thread_id == self.thread_id

    async def load_state(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> dict[str, object]:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        active_run = self._get_active_run_for_thread(thread_id=thread_id)
        active_run_status = active_run.status.value if active_run is not None else None
        return {
            "messages": list(self.messages),
            "workflow": list(self.workflow),
            "pendingApprovals": list(self.pending_approvals),
            "actionRequests": list(self.action_requests),
            "isRunning": active_run_status
            in {
                AssistantRunStatus.STARTING.value,
                AssistantRunStatus.RUNNING.value,
            },
            "runStatus": active_run_status,
            "activeRunId": str(active_run.id) if active_run is not None else None,
            "waitingForApproval": bool(
                active_run_status == AssistantRunStatus.WAITING_APPROVAL.value
            ),
            "lastErrorReason": (
                active_run.last_error_reason if active_run is not None else None
            ),
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
        if (
            role == "user"
            and self._get_active_run_for_thread(thread_id=thread_id) is not None
        ):
            raise assistant_domain_error(
                status_code=409,
                detail="Thread already has an active assistant run",
            )
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

    async def create_run(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        owner_instance_id: str,
    ) -> SimpleNamespace:
        assert owner_user_id == self.owner_user_id
        assert thread_id == self.thread_id
        run = SimpleNamespace(
            id=uuid4(),
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            owner_instance_id=owner_instance_id,
            status=AssistantRunStatus.STARTING,
            sequence=0,
            live_snapshot={},
            last_error_reason=None,
        )
        self.active_runs[run.id] = run
        return run

    async def get_run(self, *, run_id: UUID) -> SimpleNamespace | None:
        return self.active_runs.get(run_id)

    async def mark_run_running(self, *, run_id: UUID) -> SimpleNamespace | None:
        run = self.active_runs.get(run_id)
        if run is None:
            return None
        run.status = AssistantRunStatus.RUNNING
        run.last_error_reason = None
        return run

    async def mark_run_waiting_approval(
        self, *, run_id: UUID, action_request_id: UUID
    ) -> SimpleNamespace | None:
        _ = action_request_id
        run = self.active_runs.get(run_id)
        if run is None:
            return None
        run.status = AssistantRunStatus.WAITING_APPROVAL
        return run

    async def append_run_snapshot(
        self, *, run_id: UUID, snapshot: dict[str, object]
    ) -> SimpleNamespace | None:
        run = self.active_runs.get(run_id)
        if run is None:
            return None
        run.sequence += 1
        run.live_snapshot = dict(snapshot)
        return run

    async def mark_run_completed(self, *, run_id: UUID) -> SimpleNamespace | None:
        run = self.active_runs.get(run_id)
        if run is None:
            return None
        run.status = AssistantRunStatus.COMPLETED
        return run

    async def mark_run_failed(
        self, *, run_id: UUID, reason: str
    ) -> SimpleNamespace | None:
        run = self.active_runs.get(run_id)
        if run is None:
            return None
        run.status = AssistantRunStatus.FAILED
        run.last_error_reason = reason
        return run


@dataclass
class _FakeAuthorizationService:
    allowed_tools: set[str] = field(default_factory=set)

    async def authorize_tool_access(
        self, user: AuthorizationUser, tool_name: str
    ) -> bool:
        _ = user
        return tool_name in self.allowed_tools

    async def get_allowed_tool_names(self, user: AuthorizationUser) -> set[str]:
        _ = user
        return set(self.allowed_tools)


@dataclass
class _RouteRunner:
    async def run_turn(self, **kwargs):
        raise AssertionError(f"runner should not be called: {kwargs}")


@dataclass
class _RouteAssistantRepository:
    owner_user_id: UUID
    thread_id: UUID

    async def get_thread(self, *, owner_user_id: UUID, thread_id: UUID):
        if owner_user_id != self.owner_user_id or thread_id != self.thread_id:
            return None
        return SimpleNamespace(id=thread_id, owner_user_id=owner_user_id)

    async def list_messages(self, *, thread_id: UUID):
        _ = thread_id
        return []

    async def get_pending_action_requests(self, *, thread_id: UUID):
        _ = thread_id
        return []

    async def list_action_requests(self, *, thread_id: UUID):
        _ = thread_id
        return []

    async def list_action_tool_runs(self, *, thread_id: UUID):
        _ = thread_id
        return []

    async def get_active_run(self, *, thread_id: UUID):
        _ = thread_id
        return None

    async def create_assistant_run(
        self, *, thread_id: UUID, owner_user_id: UUID, owner_instance_id: str
    ):
        raise AssertionError("create_assistant_run should not be called")

    async def get_assistant_run(self, *, run_id: UUID):
        _ = run_id
        return None

    async def mark_run_running(self, *, run_id: UUID):
        _ = run_id
        return None

    async def mark_run_waiting_approval(self, *, run_id: UUID, action_request_id: UUID):
        _ = run_id, action_request_id
        return None

    async def append_run_snapshot(self, *, run_id: UUID, snapshot):
        _ = run_id, snapshot
        return None

    async def mark_run_completed(self, *, run_id: UUID):
        _ = run_id
        return None

    async def mark_run_failed(self, *, run_id: UUID, reason: str):
        _ = run_id, reason
        return None

    async def fail_run_if_owner_matches(
        self, *, run_id: UUID, owner_instance_id: str, reason: str
    ):
        _ = run_id, owner_instance_id, reason
        return None

    async def create_message(self, **kwargs):
        raise AssertionError(f"create_message should not be called: {kwargs}")

    async def create_audit_log(self, **kwargs) -> None:
        raise AssertionError(f"create_audit_log should not be called: {kwargs}")


@dataclass
class _RouteActionToolRunRepository:
    async def get_action_request(self, **kwargs):
        _ = kwargs
        return None

    async def create_action_request(self, **kwargs):
        raise AssertionError(f"create_action_request should not be called: {kwargs}")

    async def decide_action_request(self, **kwargs):
        raise AssertionError(f"decide_action_request should not be called: {kwargs}")

    async def start_tool_run(self, **kwargs):
        raise AssertionError(f"start_tool_run should not be called: {kwargs}")

    async def get_tool_run(self, **kwargs):
        _ = kwargs
        return None

    async def finish_tool_run(self, **kwargs):
        raise AssertionError(f"finish_tool_run should not be called: {kwargs}")


def _build_real_service_app(*, owner_id: UUID, thread_id: UUID) -> FastAPI:
    service = AssistantService(
        _RouteAssistantRepository(owner_user_id=owner_id, thread_id=thread_id),
        _RouteRunner(),
        action_tool_run_service=ActionToolRunService(
            repository=_RouteActionToolRunRepository()
        ),
        session=cast(Any, None),
    )
    return _build_app(
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


def _build_app(
    service: Any,
    current_user: AuthorizationUser,
    authorization_service: _FakeAuthorizationService | None = None,
    coordinator: AssistantRunCoordinator | None = None,
) -> FastAPI:
    app = FastAPI()
    resolved_coordinator = coordinator or AssistantRunCoordinator(
        instance_id="test-api"
    )
    install_error_handling(app)
    app.include_router(assistant_router)
    app.dependency_overrides[get_assistant_service] = lambda: service
    app.dependency_overrides[get_active_current_auth_user] = lambda: current_user
    app.dependency_overrides[get_authorization_service] = lambda: (
        authorization_service or _FakeAuthorizationService()
    )
    app.dependency_overrides[get_assistant_run_coordinator] = lambda: (
        resolved_coordinator
    )
    return app


def _assert_assistant_ack(
    response: Any,
    *,
    thread_id: UUID,
    run_status: str | None,
    has_active_run_id: bool,
) -> dict[str, object]:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["threadId"] == str(thread_id)
    assert payload["runStatus"] == run_status
    if has_active_run_id:
        UUID(payload["activeRunId"])
    else:
        assert payload["activeRunId"] is None
    return cast(dict[str, object], payload)


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
    expected_text = "From DB"
    saved_messages = [
        {
            "id": str(uuid4()),
            "role": "assistant",
            "parts": [{"type": "text", "text": expected_text}],
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
    assert _state_contains_text(data, expected_text)
    assert data["isRunning"] is False
    assert data["waitingForApproval"] is False


async def test_thread_state_route_includes_workflow_and_pending_approvals() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        workflow=[
            {"content": "Preflight", "status": "completed", "priority": "high"},
            {
                "content": "Request approval",
                "status": "in_progress",
                "priority": "high",
            },
        ],
        pending_approvals=[
            {
                "actionRequestId": str(uuid4()),
                "toolName": "fake_change_tool",
                "risk": "CHANGE",
                "arguments": {"key": "feature_x", "value": True},
                "status": "PENDING",
            }
        ],
        action_requests=[
            {
                "actionRequestId": str(uuid4()),
                "toolName": "fake_change_tool",
                "risk": "CHANGE",
                "arguments": {"key": "feature_x", "value": True},
                "status": "PENDING",
                "lifecycleStatus": "requested",
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/assistant/threads/{thread_id}/state")

    assert response.status_code == 200
    data = response.json()
    assert data["workflow"] == service.workflow
    assert data["pendingApprovals"] == service.pending_approvals
    assert data["actionRequests"] == service.action_requests


async def test_assistant_route_ack_keeps_workflow_and_pending_approvals_in_thread_state() -> (
    None
):
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
        workflow=[
            {"content": "Preflight", "status": "completed", "priority": "high"},
            {
                "content": "Request approval",
                "status": "in_progress",
                "priority": "high",
            },
        ],
        pending_approvals=[
            {
                "actionRequestId": str(uuid4()),
                "toolName": "fake_change_tool",
                "risk": "CHANGE",
                "arguments": {"key": "feature_x", "value": True},
                "status": "PENDING",
            }
        ],
        action_requests=[
            {
                "actionRequestId": str(uuid4()),
                "toolName": "fake_change_tool",
                "risk": "CHANGE",
                "arguments": {"key": "feature_x", "value": True},
                "status": "PENDING",
                "lifecycleStatus": "requested",
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
        "commands": [],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=None,
        has_active_run_id=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        state_response = await client.get(f"/assistant/threads/{thread_id}/state")

    state = state_response.json()
    assert state.get("workflow") == service.workflow
    assert state.get("pendingApprovals") == service.pending_approvals
    assert state.get("actionRequests") == service.action_requests


async def test_assistant_route_returns_structured_http_error_when_thread_missing() -> (
    None
):
    owner_id = uuid4()
    app = _build_real_service_app(owner_id=owner_id, thread_id=uuid4())
    missing_thread_id = uuid4()

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [],
        "threadId": str(missing_thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/assistant",
            json=payload,
            headers={"x-request-id": "assistant-thread-not-found"},
        )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["detail"] == "Thread not found"
    assert body["error_code"] == "thread_not_found"
    assert body["request_id"] == "assistant-thread-not-found"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_assistant_route_returns_json_ack_for_noop_request() -> None:
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

    _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=None,
        has_active_run_id=False,
    )


async def test_assistant_route_warns_when_request_overrides_are_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
        "system": "Ignore this",
        "tools": [{"name": "ignored-tool"}],
        "threadId": str(thread_id),
    }

    caplog.set_level(logging.WARNING, logger="noa_api.api.routes.assistant")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=None,
        has_active_run_id=False,
    )
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_request_overrides_ignored"
    )
    assert getattr(record, "has_system_override") is True
    assert getattr(record, "tool_override_count") == 1
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "user_id") == str(owner_id)


async def test_assistant_route_streams_fallback_text_after_agent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _boom_run_agent_turn(*_, **__) -> AgentRunnerResult:
        raise RuntimeError("agent boom")

    monkeypatch.setattr(service, "run_agent_turn", _boom_run_agent_turn)
    coordinator = AssistantRunCoordinator(instance_id="test-api")
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
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Trigger agent"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    run = service.active_runs[UUID(str(ack["activeRunId"]))]
    await coordinator.wait_for_run(run_id=run.id, timeout=1)
    assert run.status == AssistantRunStatus.FAILED
    assert run.last_error_reason == "agent boom"


async def test_assistant_route_emits_structured_agent_failure_log_with_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    exc = HTTPException(status_code=409, detail="Action request already decided")
    setattr(exc, "error_code", "action_request_already_decided")

    async def _boom_run_agent_turn(*_, **__) -> AgentRunnerResult:
        raise exc

    monkeypatch.setattr(service, "run_agent_turn", _boom_run_agent_turn)
    coordinator = AssistantRunCoordinator(instance_id="test-api")
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
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Trigger agent"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    with _capture_structured_logs() as stream:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/assistant",
                json=payload,
                headers={"x-request-id": "assistant-agent-http-failure"},
            )
            ack = response.json()
            await coordinator.wait_for_run(
                run_id=UUID(ack["activeRunId"]),
                timeout=1,
            )

    assert response.status_code == 200
    payloads = [
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_run_failed_agent"
    ]
    assert len(payloads) == 1
    log_payload = payloads[0]
    assert log_payload["request_id"] == "assistant-agent-http-failure"
    assert log_payload["assistant_command_types"] == ["add-message"]
    assert log_payload["thread_id"] == str(thread_id)
    assert log_payload["user_id"] == str(owner_id)
    assert log_payload["status_code"] == 409
    assert log_payload["error_code"] == "action_request_already_decided"


async def test_assistant_route_records_unexpected_agent_failure_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    recorder = RecordingTelemetryRecorder()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _boom_run_agent_turn(*_, **__) -> AgentRunnerResult:
        raise RuntimeError("agent boom")

    monkeypatch.setattr(service, "run_agent_turn", _boom_run_agent_turn)
    coordinator = AssistantRunCoordinator(instance_id="test-api")
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
        coordinator=coordinator,
    )
    app.state.telemetry = recorder

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Trigger agent"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)
        ack = response.json()
        await coordinator.wait_for_run(
            run_id=UUID(ack["activeRunId"]),
            timeout=1,
        )

    assert response.status_code == 200
    assistant_trace_events = [
        event
        for event in recorder.trace_events
        if event.name == "assistant_run_failed_agent"
    ]
    assert assistant_trace_events == [
        TelemetryEvent(
            name="assistant_run_failed_agent",
            attributes={
                "assistant_command_types": "add-message",
                "thread_id": str(thread_id),
                "user_id": str(owner_id),
                "error_type": "RuntimeError",
            },
        )
    ]
    assistant_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "assistant.failures.total"
    ]
    assert assistant_metric_events == [
        (
            TelemetryEvent(
                name="assistant.failures.total",
                attributes={
                    "assistant_command_types": "add-message",
                    "error_type": "RuntimeError",
                },
            ),
            1,
        )
    ]
    assistant_report_events = [
        (event, detail)
        for event, detail in recorder.report_events
        if event.name == "assistant_run_failed_agent"
    ]
    assert assistant_report_events == [
        (
            TelemetryEvent(
                name="assistant_run_failed_agent",
                attributes={
                    "assistant_command_types": "add-message",
                    "thread_id": str(thread_id),
                    "user_id": str(owner_id),
                    "error_type": "RuntimeError",
                },
            ),
            None,
        )
    ]


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

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    assert UUID(str(ack["activeRunId"])) in service.active_runs


async def test_assistant_route_accepts_add_message_with_parent_id_and_null_source_id() -> (
    None
):
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
                "sourceId": None,
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Follow-up"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    assert UUID(str(ack["activeRunId"])) in service.active_runs


async def test_assistant_route_accepts_follow_up_add_message_in_same_thread() -> None:
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_response = await client.post(
            "/assistant",
            json={
                "state": {"messages": [], "isRunning": False},
                "commands": [
                    {
                        "type": "add-message",
                        "message": {
                            "role": "user",
                            "parts": [{"type": "text", "text": "Hello"}],
                        },
                    }
                ],
                "threadId": str(thread_id),
            },
        )

        first_ack = _assert_assistant_ack(
            first_response,
            thread_id=thread_id,
            run_status=AssistantRunStatus.STARTING.value,
            has_active_run_id=True,
        )
        first_run_id = UUID(str(first_ack["activeRunId"]))
        service.active_runs[first_run_id].status = AssistantRunStatus.COMPLETED

        state_response = await client.get(f"/assistant/threads/{thread_id}/state")
        first_state = state_response.json()
        messages = first_state.get("messages")
        assert isinstance(messages, list)
        first_message = next(
            message for message in messages if isinstance(message, dict)
        )
        parent_id = first_message["id"]

        second_response = await client.post(
            "/assistant",
            json={
                "state": first_state,
                "commands": [
                    {
                        "type": "add-message",
                        "parentId": parent_id,
                        "sourceId": None,
                        "message": {
                            "role": "user",
                            "parts": [{"type": "text", "text": "Follow-up"}],
                        },
                    }
                ],
                "threadId": str(thread_id),
            },
        )

    second_ack = _assert_assistant_ack(
        second_response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    second_run_id = UUID(str(second_ack["activeRunId"]))
    service.active_runs[second_run_id].status = AssistantRunStatus.COMPLETED

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        state_response = await client.get(f"/assistant/threads/{thread_id}/state")

    second_state = state_response.json()
    assert _state_contains_user_text(second_state, "Hello")
    assert _state_contains_user_text(second_state, "Follow-up")


async def test_assistant_route_rejects_add_message_with_non_null_source_id() -> None:
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
        response = await client.post(
            "/assistant",
            json=payload,
            headers={"x-request-id": "assistant-edit-message"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Editing existing messages is not supported yet"
    assert body["error_code"] == "message_edit_not_supported"
    assert body["request_id"] == "assistant-edit-message"
    assert response.headers["x-request-id"] == body["request_id"]


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

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    assert UUID(str(ack["activeRunId"])) in service.active_runs


async def test_assistant_route_returns_http_error_for_pre_agent_command_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _boom_add_tool_result(*_, **__) -> None:
        raise HTTPException(
            status_code=400,
            detail="Unknown tool call id",
            headers={"x-error-code": "unknown_tool_call_id"},
        )

    monkeypatch.setattr(service, "add_tool_result", _boom_add_tool_result)
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

    caplog.set_level(logging.INFO, logger="noa_api.api.routes.assistant")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/assistant",
            json=payload,
            headers={"x-request-id": "assistant-pre-agent-http-error"},
        )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["detail"] == "Unknown tool call id"
    assert body["error_code"] == "unknown_tool_call_id"
    assert body["request_id"] == "assistant-pre-agent-http-error"
    assert response.headers["x-request-id"] == body["request_id"]
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_run_failed_pre_agent"
    )
    assert getattr(record, "status_code") == 400
    assert getattr(record, "error_code") == "unknown_tool_call_id"
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "user_id") == str(owner_id)


async def test_assistant_route_records_pre_agent_http_failure_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    recorder = RecordingTelemetryRecorder()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _boom_add_tool_result(*_, **__) -> None:
        raise HTTPException(
            status_code=400,
            detail="Unknown tool call id",
            headers={"x-error-code": "unknown_tool_call_id"},
        )

    monkeypatch.setattr(service, "add_tool_result", _boom_add_tool_result)
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
    app.state.telemetry = recorder

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

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 400
    assistant_trace_events = [
        event
        for event in recorder.trace_events
        if event.name == "assistant_run_failed_pre_agent"
    ]
    assert assistant_trace_events == [
        TelemetryEvent(
            name="assistant_run_failed_pre_agent",
            attributes={
                "assistant_command_types": "add-tool-result",
                "thread_id": str(thread_id),
                "user_id": str(owner_id),
                "status_code": 400,
                "error_code": "unknown_tool_call_id",
            },
        )
    ]
    assistant_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "assistant.failures.total"
    ]
    assert assistant_metric_events == [
        (
            TelemetryEvent(
                name="assistant.failures.total",
                attributes={
                    "assistant_command_types": "add-tool-result",
                    "status_code": 400,
                    "error_code": "unknown_tool_call_id",
                },
            ),
            1,
        )
    ]
    assistant_report_events = [
        (event, detail)
        for event, detail in recorder.report_events
        if event.name == "assistant_run_failed_pre_agent"
    ]
    assert assistant_report_events == []


async def test_assistant_route_records_unexpected_pre_agent_failure_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    recorder = RecordingTelemetryRecorder()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _boom_add_tool_result(*_, **__) -> None:
        raise RuntimeError("pre-agent boom")

    monkeypatch.setattr(service, "add_tool_result", _boom_add_tool_result)
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
    app.state.telemetry = recorder

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

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 500
    assistant_trace_events = [
        event
        for event in recorder.trace_events
        if event.name == "assistant_run_failed_pre_agent"
    ]
    assert assistant_trace_events == [
        TelemetryEvent(
            name="assistant_run_failed_pre_agent",
            attributes={
                "assistant_command_types": "add-tool-result",
                "thread_id": str(thread_id),
                "user_id": str(owner_id),
                "error_type": "RuntimeError",
            },
        )
    ]
    assistant_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "assistant.failures.total"
    ]
    assert assistant_metric_events == [
        (
            TelemetryEvent(
                name="assistant.failures.total",
                attributes={
                    "assistant_command_types": "add-tool-result",
                    "error_type": "RuntimeError",
                },
            ),
            1,
        )
    ]
    assistant_report_events = [
        (event, detail)
        for event, detail in recorder.report_events
        if event.name == "assistant_run_failed_pre_agent"
    ]
    assert assistant_report_events == [
        (
            TelemetryEvent(
                name="assistant_run_failed_pre_agent",
                attributes={
                    "assistant_command_types": "add-tool-result",
                    "thread_id": str(thread_id),
                    "user_id": str(owner_id),
                    "error_type": "RuntimeError",
                },
            ),
            None,
        )
    ]


async def test_assistant_route_returns_http_error_for_pre_stream_domain_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from noa_api.api.routes.assistant_errors import AssistantDomainError

    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _boom_prepare(*_, **__):
        raise AssistantDomainError(
            status_code=409,
            detail="Action request already decided",
            error_code="action_request_already_decided",
        )

    monkeypatch.setattr(
        "noa_api.api.routes.assistant.prepare_assistant_transport",
        _boom_prepare,
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
                "type": "approve-action",
                "actionRequestId": str(uuid4()),
            }
        ],
        "threadId": str(thread_id),
    }

    caplog.set_level(logging.INFO, logger="noa_api.api.routes.assistant")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/assistant",
            json=payload,
            headers={"x-request-id": "assistant-action-request-already-decided"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "Action request already decided"
    assert body["error_code"] == "action_request_already_decided"
    assert response.headers["x-request-id"] == body["request_id"]
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_run_failed_pre_agent"
    )
    assert getattr(record, "status_code") == 409
    assert getattr(record, "error_code") == "action_request_already_decided"
    assert getattr(record, "request_id") == "assistant-action-request-already-decided"
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "user_id") == str(owner_id)


async def test_assistant_route_uses_helper_command_types_for_pre_stream_logging(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _boom_prepare(*_, **__):
        exc = HTTPException(status_code=400, detail="pre-stream failed")
        setattr(exc, "_assistant_command_types", ["from-helper"])
        raise exc

    monkeypatch.setattr(
        "noa_api.api.routes.assistant.prepare_assistant_transport",
        _boom_prepare,
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
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    caplog.set_level(logging.INFO, logger="noa_api.api.routes.assistant")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 400
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_run_failed_pre_agent"
    )
    assert getattr(record, "assistant_command_types") == ["from-helper"]


@pytest.mark.parametrize(
    ("command", "detail", "error_code", "request_id"),
    [
        (
            {
                "type": "add-tool-result",
                "result": {"ok": True},
            },
            "Missing toolCallId",
            "missing_tool_call_id",
            "assistant-missing-tool-call-id",
        ),
        (
            {
                "type": "add-tool-result",
                "toolCallId": "not-a-uuid",
                "result": {"ok": True},
            },
            "Invalid toolCallId",
            "invalid_tool_call_id",
            "assistant-invalid-tool-call-id",
        ),
        (
            {"type": "approve-action"},
            "Missing actionRequestId",
            "missing_action_request_id",
            "assistant-missing-approve-action-request-id",
        ),
        (
            {
                "type": "approve-action",
                "actionRequestId": "not-a-uuid",
            },
            "Invalid actionRequestId",
            "invalid_action_request_id",
            "assistant-invalid-approve-action-request-id",
        ),
        (
            {"type": "deny-action"},
            "Missing actionRequestId",
            "missing_action_request_id",
            "assistant-missing-deny-action-request-id",
        ),
        (
            {
                "type": "deny-action",
                "actionRequestId": "not-a-uuid",
            },
            "Invalid actionRequestId",
            "invalid_action_request_id",
            "assistant-invalid-deny-action-request-id",
        ),
    ],
)
async def test_assistant_route_returns_coded_errors_for_missing_or_invalid_ids(
    command: dict[str, object],
    detail: str,
    error_code: str,
    request_id: str,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    app = _build_real_service_app(owner_id=owner_id, thread_id=thread_id)

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [command],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/assistant",
            json=payload,
            headers={"x-request-id": request_id},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == detail
    assert body["error_code"] == error_code
    assert body["request_id"] == request_id
    assert response.headers["x-request-id"] == request_id


async def test_assistant_route_runs_agent_after_add_tool_result_command() -> None:
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
        runner_messages=[
            AgentMessage(
                role="assistant",
                parts=[{"type": "text", "text": "Follow-up after tool result"}],
            )
        ],
        runner_text_deltas=["Follow-up"],
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

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    assert UUID(str(ack["activeRunId"])) in service.active_runs


async def test_assistant_route_runs_agent_after_approve_action_command() -> None:
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
        runner_messages=[
            AgentMessage(
                role="assistant",
                parts=[{"type": "text", "text": "Follow-up after approval"}],
            )
        ],
        runner_text_deltas=["Follow-up"],
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
                "type": "approve-action",
                "actionRequestId": "ar-1",
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    assert UUID(str(ack["activeRunId"])) in service.active_runs


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
    coordinator = AssistantRunCoordinator(instance_id="test-api")
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
        coordinator=coordinator,
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

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    await coordinator.wait_for_run(run_id=UUID(str(ack["activeRunId"])), timeout=1)
    assert service.seen_available_tools == {"get_current_time", "update_workflow_todo"}


async def test_assistant_route_ack_exposes_running_state_via_thread_state() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_run_agent_turn(**kwargs: Any) -> AgentRunnerResult:
        service.seen_available_tools = set(kwargs["available_tool_names"])
        started.set()
        await release.wait()
        return AgentRunnerResult(messages=[], text_deltas=[])

    service.run_agent_turn = _slow_run_agent_turn  # type: ignore[method-assign]
    coordinator = AssistantRunCoordinator(instance_id="test-api")
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
        coordinator=coordinator,
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

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    run_id = UUID(str(ack["activeRunId"]))
    await started.wait()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        state_response = await client.get(f"/assistant/threads/{thread_id}/state")

    state = state_response.json()
    assert state.get("isRunning") is True
    assert state.get("runStatus") == AssistantRunStatus.RUNNING.value
    assert _state_contains_user_text(state, user_text)
    release.set()
    await coordinator.wait_for_run(run_id=run_id, timeout=1)


async def test_assistant_route_ack_creates_run_for_incremental_text_flow() -> None:
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

    ack = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    assert UUID(str(ack["activeRunId"])) in service.active_runs


async def test_assistant_route_rejects_non_user_add_message_role(
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    caplog.set_level(logging.INFO, logger="noa_api.api.routes.assistant")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/assistant",
            json=payload,
            headers={"x-request-id": "assistant-invalid-role"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Only user add-message commands are allowed"
    assert body["error_code"] == "invalid_add_message_role"
    assert body["request_id"] == "assistant-invalid-role"
    assert response.headers["x-request-id"] == body["request_id"]
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "assistant_run_failed_pre_agent"
    )
    assert getattr(record, "status_code") == 400
    assert getattr(record, "error_code") == "invalid_add_message_role"
    assert getattr(record, "thread_id") == str(thread_id)
    assert getattr(record, "user_id") == str(owner_id)


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
        await _stream_assistant_text(cast(Any, _Controller()), ["a", "b"])


async def test_streaming_loop_respects_controller_is_cancelled_flag() -> None:
    class _Controller:
        state: dict[str, object] | None
        is_cancelled = True

        def __init__(self) -> None:
            self.state = {"messages": []}

    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await _stream_assistant_text(cast(Any, _Controller()), ["a", "b"])
