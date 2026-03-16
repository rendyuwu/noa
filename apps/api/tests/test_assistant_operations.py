from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from structlog.contextvars import get_contextvars

from noa_api.api.assistant import assistant_operations
from noa_api.api.assistant.assistant_commands import AssistantRequest
from noa_api.api.assistant.assistant_operations import prepare_assistant_transport
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.logging import configure_logging
from noa_api.core.telemetry import TelemetryEvent


def _active_user() -> AuthorizationUser:
    return AuthorizationUser(
        user_id=uuid4(),
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )


def _payload_with_add_message(thread_id: UUID | None = None) -> AssistantRequest:
    return AssistantRequest.model_validate(
        {
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
            "threadId": str(thread_id or uuid4()),
        }
    )


def _payload_with_user_message(thread_id: UUID | None = None) -> AssistantRequest:
    return _payload_with_add_message(thread_id)


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
class _FakeAssistantServiceWithCalls:
    states: list[dict[str, object]]
    calls: list[tuple[str, object | None]] = field(default_factory=list)
    load_state_calls_before_mutation: int = 0
    load_state_calls_after_mutation: int = 0
    saw_mutation: bool = False

    async def load_state(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> dict[str, object]:
        _ = owner_user_id, thread_id
        self.calls.append(("load_state", None))
        if self.saw_mutation:
            self.load_state_calls_after_mutation += 1
        else:
            self.load_state_calls_before_mutation += 1
        return self.states.pop(0)

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, object]],
    ) -> None:
        _ = owner_user_id, thread_id
        self.saw_mutation = True
        self.calls.append(("add_message", {"role": role, "parts": parts}))


@dataclass
class _FakeAuthorizationService:
    allowed_tools: set[str] = field(default_factory=set)

    async def authorize_tool_access(
        self, user: AuthorizationUser, tool_name: str
    ) -> bool:
        _ = user
        return tool_name in self.allowed_tools


@dataclass
class _FakeController:
    state: dict[str, object] | None = None
    is_cancelled: bool = False


def _message_with_text(text: str, *, role: str = "user") -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "role": role,
        "parts": [{"type": "text", "text": text}],
    }


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
class _FakeAssistantServiceThatFailsAgentRun:
    base_messages: list[dict[str, object]] = field(default_factory=list)
    added_messages: list[dict[str, object]] = field(default_factory=list)
    seen_available_tools: set[str] = field(default_factory=set)
    load_state_calls: int = 0
    fail_error_persistence: bool = False
    fail_state_refresh: bool = False
    agent_failure: Exception | None = None

    async def run_agent_turn(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        available_tool_names: set[str],
        on_text_delta: Any = None,
    ) -> None:
        _ = owner_user_id, owner_user_email, thread_id, on_text_delta
        self.seen_available_tools = set(available_tool_names)
        if self.agent_failure is not None:
            raise self.agent_failure
        raise RuntimeError("agent boom")

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, object]],
    ) -> None:
        _ = owner_user_id, thread_id
        if self.fail_error_persistence:
            raise RuntimeError("persist failed")
        message = {
            "id": str(uuid4()),
            "role": role,
            "parts": parts,
        }
        self.added_messages.append(message)

    async def load_state(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> dict[str, object]:
        _ = owner_user_id, thread_id
        self.load_state_calls += 1
        if self.fail_state_refresh:
            raise RuntimeError("refresh failed")
        return {
            "messages": [*self.base_messages, *self.added_messages],
            "isRunning": False,
        }


@dataclass
class _FakeAssistantServiceThatFailsStateRefresh:
    async def run_agent_turn(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        available_tool_names: set[str],
        on_text_delta: Any = None,
    ) -> None:
        _ = owner_user_id, owner_user_email, thread_id, available_tool_names
        _ = on_text_delta

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, object]],
    ) -> None:
        _ = owner_user_id, thread_id, role, parts

    async def load_state(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> dict[str, object]:
        _ = owner_user_id, thread_id
        raise RuntimeError("refresh failed")


@dataclass
class _FakeAssistantServiceThatSucceedsAgentRun:
    refreshed_state: dict[str, object]
    seen_available_tools: set[str] = field(default_factory=set)

    async def run_agent_turn(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        available_tool_names: set[str],
        on_text_delta: Any = None,
    ) -> None:
        _ = owner_user_id, owner_user_email, thread_id, on_text_delta
        self.seen_available_tools = set(available_tool_names)

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, object]],
    ) -> None:
        _ = owner_user_id, thread_id, role, parts

    async def load_state(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> dict[str, object]:
        _ = owner_user_id, thread_id
        return self.refreshed_state


async def test_prepare_assistant_transport_validates_commands_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeAssistantServiceWithCalls(
        states=[
            {
                "messages": [{"id": "before", "role": "user", "parts": []}],
                "isRunning": False,
            },
            {
                "messages": [{"id": "after", "role": "user", "parts": []}],
                "isRunning": False,
            },
        ]
    )

    def _record_validation(commands: object) -> None:
        service.calls.append(("validate_commands", commands))

    monkeypatch.setattr(assistant_operations, "validate_commands", _record_validation)

    prepared = await prepare_assistant_transport(
        payload=_payload_with_add_message(),
        current_user=_active_user(),
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
    )

    call_names = [call[0] for call in service.calls]
    assert prepared.command_types == ["add-message"]
    assert call_names.count("add_message") == 1
    assert call_names.index("validate_commands") < call_names.index("add_message")
    assert service.load_state_calls_before_mutation >= 1
    assert service.load_state_calls_after_mutation >= 1
    messages = cast(list[dict[str, object]], prepared.canonical_state["messages"])
    assert messages[0]["id"] == "after"


async def test_prepare_assistant_transport_binds_command_types_in_log_context() -> None:
    contexts: list[dict[str, object]] = []

    @dataclass
    class _FakeService:
        states: list[dict[str, object]]

        async def load_state(
            self, *, owner_user_id: UUID, thread_id: UUID
        ) -> dict[str, object]:
            _ = owner_user_id, thread_id
            contexts.append(get_contextvars())
            return self.states.pop(0)

        async def add_message(
            self,
            *,
            owner_user_id: UUID,
            thread_id: UUID,
            role: str,
            parts: list[dict[str, object]],
        ) -> None:
            _ = owner_user_id, thread_id, role, parts
            contexts.append(get_contextvars())

    await prepare_assistant_transport(
        payload=_payload_with_add_message(),
        current_user=_active_user(),
        assistant_service=_FakeService(
            states=[
                {"messages": [], "isRunning": False},
                {"messages": [], "isRunning": False},
            ]
        ),
        authorization_service=_FakeAuthorizationService(),
    )

    assert contexts
    assert all(
        context.get("assistant_command_types") == ["add-message"]
        for context in contexts
    )


async def test_run_agent_phase_persists_safe_error_message_on_failure() -> None:
    error_text = "Assistant run failed. Please try again."
    controller = _FakeController(state={"messages": [], "isRunning": True})
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")]
    )

    await assistant_operations.run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(),
        current_user=_active_user(),
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
        canonical_state={"messages": list(service.base_messages), "isRunning": False},
        command_types=["add-message"],
    )

    assert service.added_messages
    assert service.added_messages[-1]["role"] == "assistant"
    assert service.added_messages[-1]["parts"] == [{"type": "text", "text": error_text}]
    assert controller.state is not None
    assert controller.state["isRunning"] is False
    assert _state_contains_text(controller.state, error_text)


async def test_run_agent_phase_refreshes_workflow_and_pending_approvals() -> None:
    refreshed_state = {
        "messages": [_message_with_text("Agent reply", role="assistant")],
        "workflow": [
            {"content": "Preflight", "status": "completed", "priority": "high"},
            {
                "content": "Request approval",
                "status": "in_progress",
                "priority": "high",
            },
        ],
        "pendingApprovals": [
            {
                "actionRequestId": str(uuid4()),
                "toolName": "set_demo_flag",
                "risk": "CHANGE",
                "arguments": {"key": "feature_x", "value": True},
                "status": "PENDING",
            }
        ],
        "isRunning": False,
    }
    controller = _FakeController(state={})
    service = _FakeAssistantServiceThatSucceedsAgentRun(refreshed_state=refreshed_state)

    await assistant_operations.run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(),
        current_user=_active_user(),
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
        canonical_state={
            "messages": [],
            "workflow": [],
            "pendingApprovals": [],
            "isRunning": False,
        },
        command_types=["add-message"],
    )

    assert controller.state == refreshed_state


async def test_run_agent_phase_emits_structured_agent_failure_log() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")]
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})

    with _capture_structured_logs() as stream:
        await assistant_operations.run_agent_phase(
            controller=controller,
            payload=_payload_with_user_message(thread_id),
            current_user=current_user,
            assistant_service=service,
            authorization_service=_FakeAuthorizationService(),
            canonical_state={
                "messages": list(service.base_messages),
                "isRunning": False,
            },
            command_types=["add-message"],
        )

    payloads = [
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_run_failed_agent"
    ]
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["assistant_command_types"] == ["add-message"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["user_id"] == str(current_user.user_id)
    assert payload["error_type"] == "RuntimeError"


async def test_run_agent_phase_emits_structured_http_agent_failure_log() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    exc = HTTPException(status_code=409, detail="Agent request already decided")
    setattr(exc, "error_code", "action_request_already_decided")
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")],
        agent_failure=exc,
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})

    with _capture_structured_logs() as stream:
        await assistant_operations.run_agent_phase(
            controller=controller,
            payload=_payload_with_user_message(thread_id),
            current_user=current_user,
            assistant_service=service,
            authorization_service=_FakeAuthorizationService(),
            canonical_state={
                "messages": list(service.base_messages),
                "isRunning": False,
            },
            command_types=["add-message"],
        )

    payloads = [
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_run_failed_agent"
    ]
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["assistant_command_types"] == ["add-message"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["user_id"] == str(current_user.user_id)
    assert payload["status_code"] == 409
    assert payload["error_code"] == "action_request_already_decided"
    assert payload["detail"] == "Agent request already decided"


async def test_run_agent_phase_records_http_agent_failure_trace_and_metric_only() -> (
    None
):
    current_user = _active_user()
    thread_id = uuid4()
    exc = HTTPException(status_code=409, detail="Agent request already decided")
    setattr(exc, "error_code", "action_request_already_decided")
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")],
        agent_failure=exc,
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})
    recorder = RecordingTelemetryRecorder()

    await assistant_operations.run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(thread_id),
        current_user=current_user,
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
        canonical_state={
            "messages": list(service.base_messages),
            "isRunning": False,
        },
        command_types=["add-message"],
        telemetry=recorder,
    )

    assert recorder.trace_events == [
        TelemetryEvent(
            name="assistant_run_failed_agent",
            attributes={
                "assistant_command_types": "add-message",
                "thread_id": str(thread_id),
                "user_id": str(current_user.user_id),
                "status_code": 409,
                "error_code": "action_request_already_decided",
            },
        )
    ]
    assert recorder.metric_events == [
        (
            TelemetryEvent(
                name="assistant.failures.total",
                attributes={
                    "assistant_command_types": "add-message",
                    "status_code": 409,
                    "error_code": "action_request_already_decided",
                },
            ),
            1,
        )
    ]
    assert recorder.report_events == []


async def test_run_agent_phase_records_unexpected_agent_failure_reporting_candidate() -> (
    None
):
    current_user = _active_user()
    thread_id = uuid4()
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")]
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})
    recorder = RecordingTelemetryRecorder()

    await assistant_operations.run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(thread_id),
        current_user=current_user,
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
        canonical_state={
            "messages": list(service.base_messages),
            "isRunning": False,
        },
        command_types=["add-message"],
        telemetry=recorder,
    )

    assert recorder.trace_events == [
        TelemetryEvent(
            name="assistant_run_failed_agent",
            attributes={
                "assistant_command_types": "add-message",
                "thread_id": str(thread_id),
                "user_id": str(current_user.user_id),
                "error_type": "RuntimeError",
            },
        )
    ]
    assert recorder.metric_events == [
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
    assert recorder.report_events == [
        (
            TelemetryEvent(
                name="assistant_run_failed_agent",
                attributes={
                    "assistant_command_types": "add-message",
                    "thread_id": str(thread_id),
                    "user_id": str(current_user.user_id),
                    "error_type": "RuntimeError",
                },
            ),
            None,
        )
    ]


async def test_run_agent_phase_emits_structured_error_persist_failure_log() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")],
        fail_error_persistence=True,
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})

    with _capture_structured_logs() as stream:
        await assistant_operations.run_agent_phase(
            controller=controller,
            payload=_payload_with_user_message(thread_id),
            current_user=current_user,
            assistant_service=service,
            authorization_service=_FakeAuthorizationService(),
            canonical_state={
                "messages": list(service.base_messages),
                "isRunning": False,
            },
            command_types=["add-message"],
        )

    payloads = [
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_error_message_persist_failed"
    ]
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["assistant_command_types"] == ["add-message"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["user_id"] == str(current_user.user_id)
    assert payload["error_type"] == "RuntimeError"


async def test_run_agent_phase_records_error_message_persist_failure_telemetry() -> (
    None
):
    current_user = _active_user()
    thread_id = uuid4()
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")],
        fail_error_persistence=True,
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})
    recorder = RecordingTelemetryRecorder()

    await assistant_operations.run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(thread_id),
        current_user=current_user,
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
        canonical_state={
            "messages": list(service.base_messages),
            "isRunning": False,
        },
        command_types=["add-message"],
        telemetry=recorder,
    )

    persist_trace_events = [
        event
        for event in recorder.trace_events
        if event.name == "assistant_error_message_persist_failed"
    ]
    assert persist_trace_events == [
        TelemetryEvent(
            name="assistant_error_message_persist_failed",
            attributes={
                "assistant_command_types": "add-message",
                "thread_id": str(thread_id),
                "user_id": str(current_user.user_id),
                "error_type": "RuntimeError",
            },
        )
    ]
    persist_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "assistant.failures.total"
    ]
    assert persist_metric_events == [
        (
            TelemetryEvent(
                name="assistant.failures.total",
                attributes={
                    "assistant_command_types": "add-message",
                    "error_type": "RuntimeError",
                },
            ),
            1,
        ),
        (
            TelemetryEvent(
                name="assistant.failures.total",
                attributes={
                    "assistant_command_types": "add-message",
                    "error_type": "RuntimeError",
                },
            ),
            1,
        ),
    ]
    persist_report_events = [
        (event, detail)
        for event, detail in recorder.report_events
        if event.name == "assistant_error_message_persist_failed"
    ]
    assert persist_report_events == [
        (
            TelemetryEvent(
                name="assistant_error_message_persist_failed",
                attributes={
                    "assistant_command_types": "add-message",
                    "thread_id": str(thread_id),
                    "user_id": str(current_user.user_id),
                    "error_type": "RuntimeError",
                },
            ),
            None,
        )
    ]


async def test_run_agent_phase_emits_structured_state_refresh_failure_log() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    controller = _FakeController(state={"messages": [], "isRunning": True})

    with _capture_structured_logs() as stream:
        await assistant_operations.run_agent_phase(
            controller=controller,
            payload=_payload_with_user_message(thread_id),
            current_user=current_user,
            assistant_service=_FakeAssistantServiceThatFailsStateRefresh(),
            authorization_service=_FakeAuthorizationService(),
            canonical_state={"messages": [], "isRunning": False},
            command_types=["add-message"],
        )

    payloads = [
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_state_refresh_failed"
    ]
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["assistant_command_types"] == ["add-message"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["user_id"] == str(current_user.user_id)
    assert payload["error_type"] == "RuntimeError"


async def test_run_agent_phase_records_state_refresh_failure_telemetry() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    recorder = RecordingTelemetryRecorder()

    await assistant_operations.run_agent_phase(
        controller=_FakeController(state={"messages": [], "isRunning": True}),
        payload=_payload_with_user_message(thread_id),
        current_user=current_user,
        assistant_service=_FakeAssistantServiceThatFailsStateRefresh(),
        authorization_service=_FakeAuthorizationService(),
        canonical_state={"messages": [], "isRunning": False},
        command_types=["add-message"],
        telemetry=recorder,
    )

    refresh_trace_events = [
        event
        for event in recorder.trace_events
        if event.name == "assistant_state_refresh_failed"
    ]
    assert refresh_trace_events == [
        TelemetryEvent(
            name="assistant_state_refresh_failed",
            attributes={
                "assistant_command_types": "add-message",
                "thread_id": str(thread_id),
                "user_id": str(current_user.user_id),
                "error_type": "RuntimeError",
            },
        )
    ]
    refresh_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "assistant.failures.total"
    ]
    assert refresh_metric_events == [
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
    refresh_report_events = [
        (event, detail)
        for event, detail in recorder.report_events
        if event.name == "assistant_state_refresh_failed"
    ]
    assert refresh_report_events == [
        (
            TelemetryEvent(
                name="assistant_state_refresh_failed",
                attributes={
                    "assistant_command_types": "add-message",
                    "thread_id": str(thread_id),
                    "user_id": str(current_user.user_id),
                    "error_type": "RuntimeError",
                },
            ),
            None,
        )
    ]


async def test_run_agent_phase_appends_local_fallback_when_persistence_and_refresh_fail() -> (
    None
):
    error_text = "Assistant run failed. Please try again."
    base_messages = [_message_with_text("Trigger agent")]
    controller = _FakeController(
        state={"messages": list(base_messages), "isRunning": True}
    )
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=base_messages,
        fail_error_persistence=True,
        fail_state_refresh=True,
    )

    await assistant_operations.run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(),
        current_user=_active_user(),
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
        canonical_state={"messages": list(base_messages), "isRunning": False},
        command_types=["add-message"],
    )

    assert service.load_state_calls == 1
    assert not service.added_messages
    assert controller.state is not None
    assert controller.state["isRunning"] is False
    assert _state_contains_text(controller.state, "Trigger agent")
    assert _state_contains_text(controller.state, error_text)
    messages = cast(list[dict[str, object]], controller.state["messages"])
    assert all(message.get("id") != "assistant-streaming" for message in messages)
