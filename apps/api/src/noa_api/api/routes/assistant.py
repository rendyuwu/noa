from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any, Awaitable, Callable, cast
from uuid import UUID

from assistant_stream import RunController, create_run
from assistant_stream.serialization import AssistantTransportResponse
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import (
    THREAD_NOT_FOUND,
    USER_PENDING_APPROVAL,
)
from noa_api.api.assistant.assistant_commands import (
    AssistantRequest,
    should_run_agent,
)
from noa_api.api.assistant.assistant_action_operations import (
    approve_action_request as approve_action_request_operation,
    deny_action_request as deny_action_request_operation,
    execute_approved_tool_run,
)
from noa_api.api.assistant.assistant_errors import (
    AssistantDomainError,
    assistant_http_error,
    to_assistant_http_error,
)
from noa_api.api.assistant.assistant_operations import (
    _record_assistant_failure_telemetry,
    prepare_assistant_transport,
    run_agent_phase,
)
from noa_api.api.assistant.assistant_repository import SQLAssistantRepository
from noa_api.api.assistant.assistant_streaming import _stream_assistant_text
from noa_api.api.assistant.assistant_tool_result_operations import record_tool_result
from noa_api.core.agent.runner import (
    AgentRunner,
    AgentRunnerResult,
    create_default_llm_client,
)
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    get_authorization_service,
)
from noa_api.core.logging_context import log_context
from noa_api.core.request_context import get_request_id
from noa_api.core.telemetry import get_telemetry_recorder
from noa_api.storage.postgres.action_tool_runs import (
    ActionToolRunService,
    SQLActionToolRunRepository,
)
from noa_api.storage.postgres.client import get_session_factory

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
        async def execute_tool(
            *,
            started_tool_run: Any,
            approved_request: Any,
            owner_user_id: UUID,
            owner_user_email: str | None,
            thread_id: UUID,
            repository: Any,
            action_tool_run_service: ActionToolRunService,
        ) -> None:
            await execute_approved_tool_run(
                started_tool_run=started_tool_run,
                approved_request=approved_request,
                owner_user_id=owner_user_id,
                owner_user_email=owner_user_email,
                thread_id=thread_id,
                repository=repository,
                action_tool_run_service=action_tool_run_service,
                session=self._session,
            )

        await approve_action_request_operation(
            owner_user_id=owner_user_id,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            action_request_id=action_request_id,
            is_user_active=is_user_active,
            authorize_tool_access=authorize_tool_access,
            repository=self._repository,
            action_tool_run_service=self._action_tool_run_service,
            execute_tool=execute_tool,
        )

    async def deny_action(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        await deny_action_request_operation(
            owner_user_id=owner_user_id,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            action_request_id=action_request_id,
            repository=self._repository,
            action_tool_run_service=self._action_tool_run_service,
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
    request: Request,
    payload: AssistantRequest,
    current_user: AuthorizationUser = Depends(_require_active_user),
    assistant_service: AssistantService = Depends(get_assistant_service),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AssistantTransportResponse:
    command_types: list[str] = []
    telemetry = get_telemetry_recorder(request.app)

    with log_context(
        thread_id=str(payload.thread_id),
        user_id=str(current_user.user_id),
    ):
        if payload.system is not None or payload.tools is not None:
            logger.warning(
                "assistant_request_overrides_ignored",
                extra={
                    "has_system_override": payload.system is not None,
                    "tool_override_count": len(payload.tools or []),
                    "thread_id": str(payload.thread_id),
                    "user_id": str(current_user.user_id),
                },
            )
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
            translated_exc = (
                to_assistant_http_error(exc)
                if isinstance(exc, AssistantDomainError)
                else None
            )
            http_exc: StarletteHTTPException | None
            if translated_exc is not None:
                http_exc = translated_exc
            elif isinstance(exc, StarletteHTTPException):
                http_exc = exc
            else:
                http_exc = None
            if http_exc is not None:
                error_code = _http_exception_error_code(http_exc)
                logger.info(
                    "assistant_run_failed_pre_agent",
                    extra={
                        "assistant_command_types": command_types,
                        "detail": http_exc.detail,
                        "error_code": error_code,
                        "request_id": get_request_id(),
                        "status_code": http_exc.status_code,
                        "thread_id": str(payload.thread_id),
                        "user_id": str(current_user.user_id),
                    },
                )
                _record_assistant_failure_telemetry(
                    telemetry,
                    event_name="assistant_run_failed_pre_agent",
                    command_types=command_types,
                    thread_id=payload.thread_id,
                    user_id=current_user.user_id,
                    status_code=http_exc.status_code,
                    error_code=error_code,
                )
            else:
                logger.exception(
                    "assistant_run_failed_pre_agent",
                    extra={
                        "assistant_command_types": command_types,
                        "error_type": type(exc).__name__,
                        "request_id": get_request_id(),
                        "thread_id": str(payload.thread_id),
                        "user_id": str(current_user.user_id),
                    },
                )
                _record_assistant_failure_telemetry(
                    telemetry,
                    event_name="assistant_run_failed_pre_agent",
                    command_types=command_types,
                    thread_id=payload.thread_id,
                    user_id=current_user.user_id,
                    error_type=type(exc).__name__,
                    report=True,
                )
            if translated_exc is not None:
                raise translated_exc from exc
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
                    telemetry=telemetry,
                )
                return

            controller.state["isRunning"] = False

    stream = create_run(run_callback, state=payload.state)
    return AssistantTransportResponse(stream)
