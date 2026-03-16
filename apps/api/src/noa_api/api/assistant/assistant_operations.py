from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, cast

from assistant_stream import RunController
from starlette.exceptions import HTTPException as StarletteHTTPException

from noa_api.api.assistant.assistant_commands import (
    AssistantRequest,
    AssistantServiceProtocol,
    apply_commands,
    validate_commands,
)
from noa_api.api.assistant.assistant_streaming import (
    append_fallback_error_message,
    coerce_messages,
    controller_is_cancelled,
    flush_controller_state,
    make_streaming_placeholder,
)
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.logging_context import log_context
from noa_api.core.telemetry import TelemetryEvent, TelemetryRecorder
from noa_api.core.tools.registry import get_tool_registry

logger = logging.getLogger(__name__)

ASSISTANT_FAILURES_TOTAL = "assistant.failures.total"
SAFE_ASSISTANT_ERROR_TEXT = "Assistant run failed. Please try again."


class AssistantPreparationServiceProtocol(Protocol):
    async def load_state(
        self, *, owner_user_id: Any, thread_id: Any
    ) -> dict[str, object]: ...


class AssistantAuthorizationServiceProtocol(Protocol):
    async def authorize_tool_access(self, user: Any, tool_name: str) -> bool: ...


class AssistantAgentServiceProtocol(Protocol):
    async def load_state(
        self, *, owner_user_id: Any, thread_id: Any
    ) -> dict[str, object]: ...

    async def add_message(
        self,
        *,
        owner_user_id: Any,
        thread_id: Any,
        role: str,
        parts: list[dict[str, object]],
    ) -> None: ...

    async def run_agent_turn(
        self,
        *,
        owner_user_id: Any,
        owner_user_email: str | None,
        thread_id: Any,
        available_tool_names: set[str],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> Any: ...


@dataclass(slots=True)
class PreparedAssistantTransport:
    command_types: list[str]
    canonical_state: dict[str, object]


def _http_exception_error_code(exc: StarletteHTTPException) -> str | None:
    error_code = getattr(exc, "error_code", None)
    if isinstance(error_code, str):
        return error_code
    headers = exc.headers or {}
    return headers.get("x-error-code") or headers.get("X-Error-Code")


def _assistant_command_types_attribute(command_types: list[str]) -> str:
    unique_command_types = dict.fromkeys(command_types)
    return ",".join(unique_command_types) or "none"


def _assistant_failure_attributes(
    *,
    command_types: list[str],
    thread_id: Any | None = None,
    user_id: Any | None = None,
    status_code: int | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
) -> dict[str, str | int]:
    attributes: dict[str, str | int] = {
        "assistant_command_types": _assistant_command_types_attribute(command_types)
    }
    if thread_id is not None:
        attributes["thread_id"] = str(thread_id)
    if user_id is not None:
        attributes["user_id"] = str(user_id)
    if status_code is not None:
        attributes["status_code"] = status_code
    if error_code is not None:
        attributes["error_code"] = error_code
    if error_type is not None:
        attributes["error_type"] = error_type
    return attributes


def _safe_trace(telemetry: TelemetryRecorder | None, event: TelemetryEvent) -> None:
    if telemetry is None:
        return
    try:
        telemetry.trace(event)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "trace",
                "telemetry_event": event.name,
            },
        )


def _safe_metric(
    telemetry: TelemetryRecorder | None, event: TelemetryEvent, *, value: int | float
) -> None:
    if telemetry is None:
        return
    try:
        telemetry.metric(event, value=value)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "metric",
                "telemetry_event": event.name,
            },
        )


def _safe_report(telemetry: TelemetryRecorder | None, event: TelemetryEvent) -> None:
    if telemetry is None:
        return
    try:
        telemetry.report(event)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "report",
                "telemetry_event": event.name,
            },
        )


def _apply_canonical_state(
    state: dict[str, object],
    canonical_state: dict[str, object],
    *,
    is_running: bool,
) -> None:
    state["messages"] = coerce_messages(canonical_state.get("messages"))
    state["workflow"] = canonical_state.get("workflow") or []
    state["pendingApprovals"] = canonical_state.get("pendingApprovals") or []
    state["isRunning"] = is_running


def _record_assistant_failure_telemetry(
    telemetry: TelemetryRecorder | None,
    *,
    event_name: str,
    command_types: list[str],
    thread_id: Any,
    user_id: Any,
    status_code: int | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
    report: bool = False,
) -> None:
    trace_event = TelemetryEvent(
        name=event_name,
        attributes=_assistant_failure_attributes(
            command_types=command_types,
            thread_id=thread_id,
            user_id=user_id,
            status_code=status_code,
            error_code=error_code,
            error_type=error_type,
        ),
    )
    _safe_trace(telemetry, trace_event)
    metric_attributes = _assistant_failure_attributes(
        command_types=command_types,
        status_code=status_code,
        error_code=error_code,
        error_type=error_type,
    )
    _safe_metric(
        telemetry,
        TelemetryEvent(name=ASSISTANT_FAILURES_TOTAL, attributes=metric_attributes),
        value=1,
    )
    if report:
        _safe_report(telemetry, trace_event)


async def prepare_assistant_transport(
    *,
    payload: AssistantRequest,
    current_user: AuthorizationUser,
    assistant_service: AssistantPreparationServiceProtocol,
    authorization_service: AssistantAuthorizationServiceProtocol,
) -> PreparedAssistantTransport:
    command_types = [command.type for command in payload.commands]
    try:
        with log_context(assistant_command_types=command_types):
            validate_commands(payload.commands)

            await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )

            await apply_commands(
                commands=payload.commands,
                assistant_service=cast(AssistantServiceProtocol, assistant_service),
                current_user=current_user,
                payload=payload,
                authorization_service=cast(Any, authorization_service),
            )

            canonical_state = await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        setattr(exc, "_assistant_command_types", command_types)
        raise

    return PreparedAssistantTransport(
        command_types=command_types,
        canonical_state=canonical_state,
    )


async def run_agent_phase(
    *,
    controller: RunController,
    payload: AssistantRequest,
    current_user: AuthorizationUser,
    assistant_service: AssistantAgentServiceProtocol,
    authorization_service: AssistantAuthorizationServiceProtocol,
    canonical_state: dict[str, object],
    command_types: list[str],
    telemetry: TelemetryRecorder | None = None,
) -> None:
    if controller.state is None:
        controller.state = {}

    try:
        allowed_tools: set[str] = set()
        for tool in get_tool_registry():
            tool_name = tool.name
            if await authorization_service.authorize_tool_access(
                current_user, tool_name
            ):
                allowed_tools.add(tool_name)

        allowed_tools.add("update_workflow_todo")

        base_messages = coerce_messages(canonical_state.get("messages"))
        controller.state["workflow"] = canonical_state.get("workflow") or []
        controller.state["pendingApprovals"] = (
            canonical_state.get("pendingApprovals") or []
        )
        controller.state["messages"] = [
            *base_messages,
            make_streaming_placeholder(""),
        ]
        controller.state["isRunning"] = True
        await flush_controller_state(controller)

        streamed_text = ""

        async def _on_text_delta(delta: str) -> None:
            nonlocal streamed_text
            if not delta:
                return
            if controller_is_cancelled(controller):
                raise asyncio.CancelledError
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError

            streamed_text += delta
            controller.state["messages"] = [
                *base_messages,
                make_streaming_placeholder(streamed_text),
            ]
            await flush_controller_state(controller)

        _ = await assistant_service.run_agent_turn(
            owner_user_id=current_user.user_id,
            owner_user_email=current_user.email,
            thread_id=payload.thread_id,
            available_tool_names=allowed_tools,
            on_text_delta=_on_text_delta,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if isinstance(exc, StarletteHTTPException):
            http_exc = cast(StarletteHTTPException, exc)
            error_code = _http_exception_error_code(http_exc)
            logger.info(
                "assistant_run_failed_agent",
                extra={
                    "assistant_command_types": command_types,
                    "detail": http_exc.detail,
                    "error_code": error_code,
                    "status_code": http_exc.status_code,
                    "thread_id": str(payload.thread_id),
                    "user_id": str(current_user.user_id),
                },
            )
            _record_assistant_failure_telemetry(
                telemetry,
                event_name="assistant_run_failed_agent",
                command_types=command_types,
                thread_id=payload.thread_id,
                user_id=current_user.user_id,
                status_code=http_exc.status_code,
                error_code=error_code,
            )
        else:
            logger.exception(
                "assistant_run_failed_agent",
                extra={
                    "assistant_command_types": command_types,
                    "error_type": type(exc).__name__,
                    "thread_id": str(payload.thread_id),
                    "user_id": str(current_user.user_id),
                },
            )
            _record_assistant_failure_telemetry(
                telemetry,
                event_name="assistant_run_failed_agent",
                command_types=command_types,
                thread_id=payload.thread_id,
                user_id=current_user.user_id,
                error_type=type(exc).__name__,
                report=True,
            )

        persisted_error_message = False
        try:
            await assistant_service.add_message(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
                role="assistant",
                parts=[{"type": "text", "text": SAFE_ASSISTANT_ERROR_TEXT}],
            )
            persisted_error_message = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "assistant_error_message_persist_failed",
                extra={
                    "assistant_command_types": command_types,
                    "error_type": type(exc).__name__,
                    "thread_id": str(payload.thread_id),
                    "user_id": str(current_user.user_id),
                },
            )
            _record_assistant_failure_telemetry(
                telemetry,
                event_name="assistant_error_message_persist_failed",
                command_types=command_types,
                thread_id=payload.thread_id,
                user_id=current_user.user_id,
                error_type=type(exc).__name__,
                report=True,
            )

        controller.state["isRunning"] = False
        try:
            failed_state = await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )
            _apply_canonical_state(controller.state, failed_state, is_running=False)

            if not persisted_error_message:
                controller.state["messages"] = append_fallback_error_message(
                    coerce_messages(controller.state.get("messages")),
                    SAFE_ASSISTANT_ERROR_TEXT,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            controller.state["messages"] = append_fallback_error_message(
                coerce_messages(controller.state.get("messages")),
                SAFE_ASSISTANT_ERROR_TEXT,
            )

        await flush_controller_state(controller)
        return

    try:
        updated_state = await assistant_service.load_state(
            owner_user_id=current_user.user_id,
            thread_id=payload.thread_id,
        )
        _apply_canonical_state(controller.state, updated_state, is_running=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(
            "assistant_state_refresh_failed",
            extra={
                "assistant_command_types": command_types,
                "error_type": type(exc).__name__,
                "thread_id": str(payload.thread_id),
                "user_id": str(current_user.user_id),
            },
        )
        _record_assistant_failure_telemetry(
            telemetry,
            event_name="assistant_state_refresh_failed",
            command_types=command_types,
            thread_id=payload.thread_id,
            user_id=current_user.user_id,
            error_type=type(exc).__name__,
            report=True,
        )
        controller.state["isRunning"] = False
        controller.state["messages"] = append_fallback_error_message(
            coerce_messages(controller.state.get("messages")),
            SAFE_ASSISTANT_ERROR_TEXT,
        )
        await flush_controller_state(controller)
