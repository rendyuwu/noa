from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, cast

from assistant_stream import RunController
from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException

from noa_api.api.routes.assistant_commands import (
    AssistantRequest,
    AssistantServiceProtocol,
    apply_commands,
    validate_commands,
)
from noa_api.api.routes.assistant_streaming import (
    append_fallback_error_message,
    coerce_messages,
    controller_is_cancelled,
    flush_controller_state,
    make_streaming_placeholder,
)
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.logging_context import log_context
from noa_api.core.tools.registry import get_tool_registry

logger = logging.getLogger(__name__)

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
        controller.state["messages"] = [
            *base_messages,
            make_streaming_placeholder(""),
        ]
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
        if isinstance(exc, HTTPException):
            logger.info(
                "assistant_run_failed_agent",
                extra={
                    "assistant_command_types": command_types,
                    "detail": exc.detail,
                    "error_code": _http_exception_error_code(exc),
                    "status_code": exc.status_code,
                    "thread_id": str(payload.thread_id),
                    "user_id": str(current_user.user_id),
                },
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

        controller.state["isRunning"] = False
        try:
            failed_state = await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )
            controller.state["messages"] = coerce_messages(failed_state.get("messages"))

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
        controller.state["messages"] = coerce_messages(updated_state.get("messages"))
        controller.state["isRunning"] = False
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
        controller.state["isRunning"] = False
        controller.state["messages"] = append_fallback_error_message(
            coerce_messages(controller.state.get("messages")),
            SAFE_ASSISTANT_ERROR_TEXT,
        )
        await flush_controller_state(controller)
