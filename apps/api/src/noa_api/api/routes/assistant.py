from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from inspect import signature
from typing import Any, Awaitable, Callable, cast
from uuid import UUID

from assistant_stream import RunController, create_run
from assistant_stream.serialization import AssistantTransportResponse
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from noa_api.api.error_codes import (
    ACTION_REQUEST_ALREADY_DECIDED,
    ACTION_REQUEST_NOT_FOUND,
    CHANGE_APPROVAL_REQUIRED,
    THREAD_NOT_FOUND,
    TOOL_ACCESS_DENIED,
    USER_PENDING_APPROVAL,
)
from noa_api.api.routes.assistant_commands import (
    AssistantRequest,
    should_run_agent,
)
from noa_api.api.routes.assistant_errors import (
    assistant_http_error,
    parse_action_request_id,
)
from noa_api.api.routes.assistant_operations import (
    prepare_assistant_transport,
    run_agent_phase,
)
from noa_api.api.routes.assistant_repository import SQLAssistantRepository
from noa_api.api.routes.assistant_streaming import _stream_assistant_text
from noa_api.api.routes.assistant_tool_result_operations import record_tool_result
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
from noa_api.core.tools.registry import get_tool_definition
from noa_api.storage.postgres.action_tool_runs import (
    ActionToolRunService,
    SQLActionToolRunRepository,
)
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    ToolRisk,
)

router = APIRouter(tags=["assistant"])

logger = logging.getLogger(__name__)

__all__ = ["_stream_assistant_text", "get_assistant_service", "router"]


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
            raise assistant_http_error(
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
        tool_call_id: str | None,
        result: dict[str, Any],
    ) -> None:
        await record_tool_result(
            owner_user_id=owner_user_id,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            result=result,
            repository=self._repository,
            action_tool_run_service=self._action_tool_run_service,
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
            raise assistant_http_error(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User pending approval",
                error_code=USER_PENDING_APPROVAL,
            )

        parsed_id = parse_action_request_id(action_request_id)
        request = await self._action_tool_run_service.get_action_request(
            action_request_id=parsed_id
        )
        if request is None:
            raise assistant_http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if (
            request.thread_id != thread_id
            or request.requested_by_user_id != owner_user_id
        ):
            raise assistant_http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if request.status != ActionRequestStatus.PENDING:
            raise assistant_http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
                error_code=ACTION_REQUEST_ALREADY_DECIDED,
            )
        if request.risk != ToolRisk.CHANGE:
            raise assistant_http_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only CHANGE actions require approval",
                error_code=CHANGE_APPROVAL_REQUIRED,
            )
        if not await authorize_tool_access(request.tool_name):
            raise assistant_http_error(
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
            raise assistant_http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
                error_code=ACTION_REQUEST_ALREADY_DECIDED,
            ) from exc
        if approved is None:
            raise assistant_http_error(
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
        with log_context(
            action_request_id=str(approved.id),
            thread_id=str(thread_id),
            tool_name=approved.tool_name,
            tool_run_id=str(started.id),
            user_id=str(owner_user_id),
        ):
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
            logger.info(
                "assistant_action_approved",
                extra={
                    "action_request_id": str(approved.id),
                    "thread_id": str(thread_id),
                    "tool_name": approved.tool_name,
                    "tool_run_id": str(started.id),
                    "user_id": str(owner_user_id),
                },
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
        parsed_id = parse_action_request_id(action_request_id)
        request = await self._action_tool_run_service.get_action_request(
            action_request_id=parsed_id
        )
        if request is None:
            raise assistant_http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if (
            request.thread_id != thread_id
            or request.requested_by_user_id != owner_user_id
        ):
            raise assistant_http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )
        if request.status != ActionRequestStatus.PENDING:
            raise assistant_http_error(
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
            raise assistant_http_error(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
                error_code=ACTION_REQUEST_ALREADY_DECIDED,
            ) from exc
        if denied is None:
            raise assistant_http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action request not found",
                error_code=ACTION_REQUEST_NOT_FOUND,
            )

        with log_context(
            action_request_id=str(denied.id),
            thread_id=str(thread_id),
            tool_name=denied.tool_name,
            user_id=str(owner_user_id),
        ):
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
                metadata={
                    "thread_id": str(thread_id),
                    "action_request_id": str(denied.id),
                },
            )
            logger.info(
                "assistant_action_denied",
                extra={
                    "action_request_id": str(denied.id),
                    "thread_id": str(thread_id),
                    "tool_name": denied.tool_name,
                    "user_id": str(owner_user_id),
                },
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
        raise assistant_http_error(
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
    command_types: list[str] = []

    with log_context(
        thread_id=str(payload.thread_id),
        user_id=str(current_user.user_id),
    ):
        try:
            prepared = await prepare_assistant_transport(
                payload=payload,
                current_user=current_user,
                assistant_service=assistant_service,
                authorization_service=authorization_service,
            )
            command_types = prepared.command_types
            canonical_state = prepared.canonical_state
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            command_types = getattr(exc, "_assistant_command_types", [])
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
                await run_agent_phase(
                    controller=controller,
                    payload=payload,
                    current_user=current_user,
                    assistant_service=assistant_service,
                    authorization_service=authorization_service,
                    canonical_state=canonical_state,
                    command_types=command_types,
                )
                return

            controller.state["isRunning"] = False

    stream = create_run(run_callback, state=payload.state)
    return AssistantTransportResponse(stream)
