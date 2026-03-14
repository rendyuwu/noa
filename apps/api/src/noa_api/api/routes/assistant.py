from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from inspect import signature
from typing import Any, Awaitable, Callable, cast
from uuid import UUID

from assistant_stream import RunController, create_run
from assistant_stream.serialization import AssistantTransportResponse
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from noa_api.api.error_codes import (
    ACTION_REQUEST_ALREADY_DECIDED,
    ACTION_REQUEST_NOT_FOUND,
    CHANGE_APPROVAL_REQUIRED,
    THREAD_NOT_FOUND,
    TOOL_ACCESS_DENIED,
    TOOL_CALL_NOT_AWAITING_RESULT,
    TOOL_CALL_NOT_FOUND,
    UNKNOWN_TOOL_CALL_ID,
    USER_PENDING_APPROVAL,
)
from noa_api.api.routes.assistant_commands import (
    AssistantRequest,
    apply_commands,
    should_run_agent,
    validate_commands,
)
from noa_api.api.routes.assistant_repository import SQLAssistantRepository
from noa_api.api.routes.assistant_streaming import (
    _stream_assistant_text,
    append_fallback_error_message,
    coerce_messages,
    controller_is_cancelled,
    flush_controller_state,
    make_streaming_placeholder,
)
from noa_api.api.routes.assistant_tool_execution import build_tool_result_part
from noa_api.core.agent.runner import (
    AgentRunner,
    AgentRunnerResult,
    create_default_llm_client,
)
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    get_authorization_service,
    get_current_auth_user,
)
from noa_api.core.logging_context import log_context
from noa_api.core.tool_error_sanitizer import sanitize_tool_error
from noa_api.core.tools.registry import get_tool_definition, get_tool_registry
from noa_api.storage.postgres.action_tool_runs import (
    ActionToolRunService,
    SQLActionToolRunRepository,
)
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    ToolRisk,
    ToolRunStatus,
)

router = APIRouter(tags=["assistant"])

logger = logging.getLogger(__name__)

__all__ = ["_stream_assistant_text", "get_assistant_service", "router"]


def _http_error(
    *,
    status_code: int,
    detail: str,
    error_code: str | None = None,
) -> HTTPException:
    headers = {"x-error-code": error_code} if error_code is not None else None
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def _parse_uuid(raw: str | None, *, label: str) -> UUID:
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Missing {label}"
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {label}"
        ) from exc


def _assistant_command_types(payload: AssistantRequest) -> list[str]:
    return [command.type for command in payload.commands]


def _http_exception_error_code(exc: StarletteHTTPException) -> str | None:
    error_code = getattr(exc, "error_code", None)
    if isinstance(error_code, str):
        return error_code
    headers = exc.headers or {}
    return headers.get("x-error-code") or headers.get("X-Error-Code")


class AssistantThreadStateMessage(BaseModel):
    id: str
    role: str
    parts: list[dict[str, Any]]


class AssistantThreadStateResponse(BaseModel):
    messages: list[AssistantThreadStateMessage]
    is_running: bool = Field(alias="isRunning")

    model_config = {"populate_by_name": True}


class AssistantService:
    def __init__(
        self,
        repository: SQLAssistantRepository,
        runner: AgentRunner,
        *,
        action_tool_run_service: ActionToolRunService,
        session: AsyncSession,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._action_tool_run_service = action_tool_run_service
        self._session = session

    async def load_state(
        self, *, owner_user_id: UUID, thread_id: UUID
    ) -> dict[str, object]:
        thread = await self._repository.get_thread(
            owner_user_id=owner_user_id, thread_id=thread_id
        )
        if thread is None:
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found",
                error_code=THREAD_NOT_FOUND,
            )

        messages = await self._repository.list_messages(thread_id=thread_id)
        return {
            "messages": [
                {
                    "id": str(message.id),
                    "role": message.role,
                    "parts": message.content,
                }
                for message in messages
            ],
            "isRunning": False,
        }

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, Any]],
    ) -> None:
        _ = owner_user_id
        await self._repository.create_message(
            thread_id=thread_id, role=role, parts=parts
        )

    async def add_tool_result(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        tool_call_id: str,
        result: dict[str, Any],
    ) -> None:
        tool_run_id = _parse_uuid(tool_call_id, label="toolCallId")
        tool_run = await self._action_tool_run_service.get_tool_run(
            tool_run_id=tool_run_id
        )
        if tool_run is None:
            raise _http_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown tool call id",
                error_code=UNKNOWN_TOOL_CALL_ID,
            )
        if (
            tool_run.thread_id != thread_id
            or tool_run.requested_by_user_id != owner_user_id
        ):
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tool call not found",
                error_code=TOOL_CALL_NOT_FOUND,
            )
        if tool_run.status != ToolRunStatus.STARTED:
            raise _http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tool call is not awaiting result",
                error_code=TOOL_CALL_NOT_AWAITING_RESULT,
            )

        completed = await self._action_tool_run_service.complete_tool_run(
            tool_run_id=tool_run_id, result=result
        )
        persisted_result = (
            completed.result
            if completed is not None and isinstance(completed.result, dict)
            else result
        )
        await self._repository.create_message(
            thread_id=thread_id,
            role="tool",
            parts=[
                build_tool_result_part(
                    tool_name=tool_run.tool_name,
                    tool_call_id=tool_call_id,
                    result=persisted_result,
                    is_error=False,
                )
            ],
        )
        await self._repository.create_audit_log(
            event_type="tool_completed",
            actor_email=owner_user_email,
            tool_name=tool_run.tool_name,
            metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(tool_run.id),
                "source": "add-tool-result",
            },
        )

    async def approve_action(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        action_request_id: str | None,
        is_user_active: bool,
        authorize_tool_access: Callable[[str], Awaitable[bool]],
    ) -> None:
        if not is_user_active:
            raise _http_error(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User pending approval",
                error_code=USER_PENDING_APPROVAL,
            )

        parsed_id = _parse_uuid(action_request_id, label="actionRequestId")
        request = await self._action_tool_run_service.get_action_request(
            action_request_id=parsed_id
        )
        if request is None:
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if (
            request.thread_id != thread_id
            or request.requested_by_user_id != owner_user_id
        ):
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if request.status != ActionRequestStatus.PENDING:
            raise _http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
                error_code=ACTION_REQUEST_ALREADY_DECIDED,
            )
        if request.risk != ToolRisk.CHANGE:
            raise _http_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only CHANGE actions require approval",
                error_code=CHANGE_APPROVAL_REQUIRED,
            )
        if not await authorize_tool_access(request.tool_name):
            raise _http_error(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tool access denied",
                error_code=TOOL_ACCESS_DENIED,
            )

        try:
            approved = await self._action_tool_run_service.approve_action_request(
                action_request_id=parsed_id,
                decided_by_user_id=owner_user_id,
            )
        except ValueError as exc:
            raise _http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
                error_code=ACTION_REQUEST_ALREADY_DECIDED,
            ) from exc
        if approved is None:
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )

        await self._repository.create_audit_log(
            event_type="action_approved",
            actor_email=owner_user_email,
            tool_name=approved.tool_name,
            metadata={
                "thread_id": str(thread_id),
                "action_request_id": str(approved.id),
            },
        )

        started = await self._action_tool_run_service.start_tool_run(
            thread_id=thread_id,
            tool_name=approved.tool_name,
            args=approved.args,
            action_request_id=approved.id,
            requested_by_user_id=owner_user_id,
        )
        tool_call_id = str(started.id)
        await self._repository.create_audit_log(
            event_type="tool_started",
            actor_email=owner_user_email,
            tool_name=approved.tool_name,
            metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started.id),
                "action_request_id": str(approved.id),
            },
        )
        await self._repository.create_message(
            thread_id=thread_id,
            role="assistant",
            parts=[
                {
                    "type": "tool-call",
                    "toolName": approved.tool_name,
                    "toolCallId": tool_call_id,
                    "args": approved.args,
                }
            ],
        )

        tool = get_tool_definition(approved.tool_name)
        if tool is None:
            error = "Requested tool is unavailable"
            _ = await self._action_tool_run_service.fail_tool_run(
                tool_run_id=started.id,
                error=error,
            )
            await self._repository.create_message(
                thread_id=thread_id,
                role="tool",
                parts=[
                    build_tool_result_part(
                        tool_name=approved.tool_name,
                        tool_call_id=tool_call_id,
                        result=cast(dict[str, object], {"error": error}),
                        is_error=True,
                    )
                ],
            )
            await self._repository.create_audit_log(
                event_type="tool_failed",
                actor_email=owner_user_email,
                tool_name=approved.tool_name,
                metadata={
                    "thread_id": str(thread_id),
                    "tool_run_id": str(started.id),
                    "action_request_id": str(approved.id),
                    "error": error,
                },
            )
            return
        if tool.risk != ToolRisk.CHANGE:
            error = "Approved tool risk mismatch"
            _ = await self._action_tool_run_service.fail_tool_run(
                tool_run_id=started.id,
                error=error,
            )
            await self._repository.create_message(
                thread_id=thread_id,
                role="tool",
                parts=[
                    build_tool_result_part(
                        tool_name=approved.tool_name,
                        tool_call_id=tool_call_id,
                        result=cast(
                            dict[str, object],
                            {
                                "error": error,
                                "expectedRisk": ToolRisk.CHANGE.value,
                                "actualRisk": tool.risk.value,
                            },
                        ),
                        is_error=True,
                    )
                ],
            )
            await self._repository.create_audit_log(
                event_type="tool_failed",
                actor_email=owner_user_email,
                tool_name=approved.tool_name,
                metadata={
                    "thread_id": str(thread_id),
                    "tool_run_id": str(started.id),
                    "action_request_id": str(approved.id),
                    "error": error,
                },
            )
            return

        try:
            result = await self._execute_tool(tool=tool, args=approved.args)
            completed = await self._action_tool_run_service.complete_tool_run(
                tool_run_id=started.id, result=result
            )
            persisted_result = (
                completed.result
                if completed is not None and isinstance(completed.result, dict)
                else result
            )
            await self._repository.create_message(
                thread_id=thread_id,
                role="tool",
                parts=[
                    build_tool_result_part(
                        tool_name=approved.tool_name,
                        tool_call_id=tool_call_id,
                        result=persisted_result,
                        is_error=False,
                    )
                ],
            )
            await self._repository.create_audit_log(
                event_type="tool_completed",
                actor_email=owner_user_email,
                tool_name=approved.tool_name,
                metadata={
                    "thread_id": str(thread_id),
                    "tool_run_id": str(started.id),
                    "action_request_id": str(approved.id),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            sanitized_error = sanitize_tool_error(exc)
            logger.exception(
                "assistant_approved_tool_execution_failed",
                extra={
                    "action_request_id": str(approved.id),
                    "error_code": sanitized_error.error_code,
                    "thread_id": str(thread_id),
                    "tool_name": approved.tool_name,
                    "tool_run_id": str(started.id),
                    "user_id": str(owner_user_id),
                },
            )
            _ = await self._action_tool_run_service.fail_tool_run(
                tool_run_id=started.id,
                error=sanitized_error.error_code,
            )
            await self._repository.create_message(
                thread_id=thread_id,
                role="tool",
                parts=[
                    build_tool_result_part(
                        tool_name=approved.tool_name,
                        tool_call_id=tool_call_id,
                        result=cast(dict[str, object], sanitized_error.as_result()),
                        is_error=True,
                    )
                ],
            )
            await self._repository.create_audit_log(
                event_type="tool_failed",
                actor_email=owner_user_email,
                tool_name=approved.tool_name,
                metadata={
                    "thread_id": str(thread_id),
                    "tool_run_id": str(started.id),
                    "action_request_id": str(approved.id),
                    "error": sanitized_error.error,
                    "error_code": sanitized_error.error_code,
                },
            )
            return

    async def deny_action(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        parsed_id = _parse_uuid(action_request_id, label="actionRequestId")
        request = await self._action_tool_run_service.get_action_request(
            action_request_id=parsed_id
        )
        if request is None:
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if (
            request.thread_id != thread_id
            or request.requested_by_user_id != owner_user_id
        ):
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if request.status != ActionRequestStatus.PENDING:
            raise _http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
                error_code=ACTION_REQUEST_ALREADY_DECIDED,
            )

        try:
            denied = await self._action_tool_run_service.deny_action_request(
                action_request_id=parsed_id,
                decided_by_user_id=owner_user_id,
            )
        except ValueError as exc:
            raise _http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
                error_code=ACTION_REQUEST_ALREADY_DECIDED,
            ) from exc
        if denied is None:
            raise _http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )

        await self._repository.create_message(
            thread_id=thread_id,
            role="assistant",
            parts=[
                {
                    "type": "text",
                    "text": f"Denied action request for tool '{denied.tool_name}'.",
                }
            ],
        )
        await self._repository.create_audit_log(
            event_type="action_denied",
            actor_email=owner_user_email,
            tool_name=denied.tool_name,
            metadata={"thread_id": str(thread_id), "action_request_id": str(denied.id)},
        )

    async def run_agent_turn(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        available_tool_names: set[str],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunnerResult:
        state = await self.load_state(owner_user_id=owner_user_id, thread_id=thread_id)
        thread_messages = cast(list[dict[str, object]], state["messages"])
        result = await self._runner.run_turn(
            thread_messages=thread_messages,
            available_tool_names=available_tool_names,
            thread_id=thread_id,
            requested_by_user_id=owner_user_id,
            on_text_delta=on_text_delta,
        )
        for message in result.messages:
            await self._repository.create_message(
                thread_id=thread_id, role=message.role, parts=message.parts
            )
            for part in message.parts:
                if (
                    part.get("type") != "tool-call"
                    or part.get("toolName") != "request_approval"
                ):
                    continue
                args = part.get("args")
                if not isinstance(args, dict):
                    continue
                action_request_id = args.get("actionRequestId")
                tool_name = args.get("toolName")
                if not isinstance(action_request_id, str) or not isinstance(
                    tool_name, str
                ):
                    continue
                await self._repository.create_audit_log(
                    event_type="action_requested",
                    actor_email=owner_user_email,
                    tool_name=tool_name,
                    metadata={
                        "thread_id": str(thread_id),
                        "action_request_id": action_request_id,
                    },
                )
        return result

    async def _execute_tool(
        self, *, tool: Any, args: dict[str, object]
    ) -> dict[str, object]:
        if (
            self._session is not None
            and "session" in signature(tool.execute).parameters
        ):
            return await tool.execute(session=self._session, **args)
        return await tool.execute(**args)


async def get_assistant_service() -> AsyncGenerator[AssistantService, None]:
    async with get_session_factory()() as session:
        service = AssistantService(
            SQLAssistantRepository(session),
            AgentRunner(
                llm_client=create_default_llm_client(),
                action_tool_run_service=ActionToolRunService(
                    repository=SQLActionToolRunRepository(session)
                ),
                session=session,
            ),
            action_tool_run_service=ActionToolRunService(
                repository=SQLActionToolRunRepository(session)
            ),
            session=session,
        )
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _require_active_user(
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active:
        logger.info(
            "assistant_access_denied_inactive_user",
            extra={"user_id": str(current_user.user_id)},
        )
        raise _http_error(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User pending approval",
            error_code=USER_PENDING_APPROVAL,
        )
    return current_user


@router.get(
    "/assistant/threads/{thread_id}/state",
    response_model=AssistantThreadStateResponse,
)
async def get_thread_state(
    thread_id: UUID,
    current_user: AuthorizationUser = Depends(_require_active_user),
    assistant_service: AssistantService = Depends(get_assistant_service),
) -> AssistantThreadStateResponse:
    state = await assistant_service.load_state(
        owner_user_id=current_user.user_id,
        thread_id=thread_id,
    )
    return AssistantThreadStateResponse.model_validate(state)


@router.post("/assistant")
async def assistant_transport(
    payload: AssistantRequest,
    current_user: AuthorizationUser = Depends(_require_active_user),
    assistant_service: AssistantService = Depends(get_assistant_service),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AssistantTransportResponse:
    command_types = _assistant_command_types(payload)

    with log_context(
        assistant_command_types=command_types,
        thread_id=str(payload.thread_id),
        user_id=str(current_user.user_id),
    ):
        try:
            validate_commands(payload.commands)

            # Fail before starting SSE so route-level errors still propagate as structured
            # HTTP responses and roll back any partial command mutations.
            await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )

            await apply_commands(
                commands=payload.commands,
                assistant_service=assistant_service,
                current_user=current_user,
                payload=payload,
                authorization_service=authorization_service,
            )

            canonical_state = await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, StarletteHTTPException):
                logger.info(
                    "assistant_run_failed_pre_agent",
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
                    "assistant_run_failed_pre_agent",
                    extra={
                        "assistant_command_types": command_types,
                        "error_type": type(exc).__name__,
                        "thread_id": str(payload.thread_id),
                        "user_id": str(current_user.user_id),
                    },
                )
            raise

    async def run_callback(controller: RunController) -> None:
        error_text = "Assistant run failed. Please try again."

        with log_context(
            assistant_command_types=command_types,
            thread_id=str(payload.thread_id),
            user_id=str(current_user.user_id),
        ):
            if controller.state is None:
                controller.state = {}

            controller.state["messages"] = canonical_state["messages"]
            controller.state["isRunning"] = True

            if should_run_agent(payload.commands):
                try:
                    allowed_tools: set[str] = set()
                    for tool in get_tool_registry():
                        tool_name = tool.name
                        if await authorization_service.authorize_tool_access(
                            current_user, tool_name
                        ):
                            allowed_tools.add(tool_name)

                    # Workflow TODO cards are always available for active users.
                    allowed_tools.add("update_workflow_todo")

                    base_messages = coerce_messages(canonical_state.get("messages"))

                    # Emit a running assistant message immediately so the UI can show
                    # a first-token loading state before any text arrives.
                    controller.state["messages"] = [
                        *base_messages,
                        make_streaming_placeholder(""),
                    ]

                    # Flush pending state updates before the first delta.
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
                            parts=[{"type": "text", "text": error_text}],
                        )
                        persisted_error_message = True
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("assistant_error_message_persist_failed")

                    controller.state["isRunning"] = False
                    try:
                        failed_state = await assistant_service.load_state(
                            owner_user_id=current_user.user_id,
                            thread_id=payload.thread_id,
                        )
                        controller.state["messages"] = coerce_messages(
                            failed_state.get("messages")
                        )

                        # If we couldn't persist the assistant error message, ensure the
                        # client still sees an error by appending one locally.
                        if not persisted_error_message:
                            controller.state["messages"] = (
                                append_fallback_error_message(
                                    coerce_messages(controller.state.get("messages")),
                                    error_text,
                                )
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        controller.state["messages"] = append_fallback_error_message(
                            coerce_messages(controller.state.get("messages")),
                            error_text,
                        )

                    await flush_controller_state(controller)
                    return

            try:
                updated_state = await assistant_service.load_state(
                    owner_user_id=current_user.user_id,
                    thread_id=payload.thread_id,
                )
                controller.state["messages"] = coerce_messages(
                    updated_state.get("messages")
                )
                controller.state["isRunning"] = False
            except asyncio.CancelledError:
                raise
            except Exception:
                # Avoid escaping exceptions at the end of the callback; keep the stream alive.
                logger.exception("assistant_state_refresh_failed")
                controller.state["isRunning"] = False

                controller.state["messages"] = append_fallback_error_message(
                    coerce_messages(controller.state.get("messages")),
                    error_text,
                )
                await flush_controller_state(controller)
                return

    stream = create_run(run_callback, state=payload.state)
    return AssistantTransportResponse(stream)
