from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from inspect import signature
from typing import Annotated, Any, Awaitable, Callable, Literal, cast
from uuid import UUID, uuid4

from assistant_stream import RunController, create_run
from assistant_stream.serialization import AssistantTransportResponse
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.api.routes.assistant_repository import SQLAssistantRepository
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


class AssistantMessage(BaseModel):
    role: str
    parts: list[dict[str, Any]]


class AddMessageCommand(BaseModel):
    type: Literal["add-message"]
    message: AssistantMessage
    parent_id: str | None = Field(default=None, alias="parentId")
    source_id: str | None = Field(default=None, alias="sourceId")

    model_config = {"populate_by_name": True}


class ApproveActionCommand(BaseModel):
    type: Literal["approve-action"]
    action_request_id: str | None = Field(default=None, alias="actionRequestId")

    model_config = {"populate_by_name": True}


class DenyActionCommand(BaseModel):
    type: Literal["deny-action"]
    action_request_id: str | None = Field(default=None, alias="actionRequestId")

    model_config = {"populate_by_name": True}


class AddToolResultCommand(BaseModel):
    type: Literal["add-tool-result"]
    tool_call_id: str = Field(alias="toolCallId")
    result: dict[str, Any]

    model_config = {"populate_by_name": True}


AssistantCommand = Annotated[
    AddMessageCommand | ApproveActionCommand | DenyActionCommand | AddToolResultCommand,
    Field(discriminator="type"),
]


class AssistantRequest(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)
    commands: list[AssistantCommand] = Field(default_factory=list)
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    thread_id: UUID = Field(alias="threadId")

    model_config = {"populate_by_name": True}


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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found"
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown tool call id"
            )
        if (
            tool_run.thread_id != thread_id
            or tool_run.requested_by_user_id != owner_user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Tool call not found"
            )
        if tool_run.status != ToolRunStatus.STARTED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tool call is not awaiting result",
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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User pending approval"
            )

        parsed_id = _parse_uuid(action_request_id, label="actionRequestId")
        request = await self._action_tool_run_service.get_action_request(
            action_request_id=parsed_id
        )
        if request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Action request not found"
            )
        if (
            request.thread_id != thread_id
            or request.requested_by_user_id != owner_user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Action request not found"
            )
        if request.status != ActionRequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
            )
        if request.risk != ToolRisk.CHANGE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only CHANGE actions require approval",
            )
        if not await authorize_tool_access(request.tool_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Tool access denied"
            )

        try:
            approved = await self._action_tool_run_service.approve_action_request(
                action_request_id=parsed_id,
                decided_by_user_id=owner_user_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
            ) from exc
        if approved is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Action request not found"
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
                        result={"error": error},
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
                        result={
                            "error": error,
                            "expectedRisk": ToolRisk.CHANGE.value,
                            "actualRisk": tool.risk.value,
                        },
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
                "Approved tool execution failed (tool_name=%s thread_id=%s action_request_id=%s tool_run_id=%s owner_user_id=%s error_code=%s)",
                approved.tool_name,
                thread_id,
                approved.id,
                started.id,
                owner_user_id,
                sanitized_error.error_code,
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
                        result=sanitized_error.as_result(),
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Action request not found"
            )
        if (
            request.thread_id != thread_id
            or request.requested_by_user_id != owner_user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Action request not found"
            )
        if request.status != ActionRequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
            )

        try:
            denied = await self._action_tool_run_service.deny_action_request(
                action_request_id=parsed_id,
                decided_by_user_id=owner_user_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action request already decided",
            ) from exc
        if denied is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Action request not found"
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User pending approval"
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
    for command in payload.commands:
        if isinstance(command, AddMessageCommand) and (
            command.parent_id is not None or command.source_id is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Editing existing messages is not supported yet",
            )
        if isinstance(command, AddMessageCommand) and command.message.role != "user":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only user add-message commands are allowed",
            )

    async def run_callback(controller: RunController) -> None:
        error_text = "Assistant run failed. Please try again."

        def _make_error_message(*, text: str) -> dict[str, object]:
            return {
                "id": f"assistant-run-error-{uuid4()}",
                "role": "assistant",
                "parts": [{"type": "text", "text": text}],
            }

        def _remove_streaming_placeholder(messages: list[object]) -> list[object]:
            return [
                message
                for message in messages
                if not (
                    isinstance(message, dict)
                    and message.get("id") == "assistant-streaming"
                )
            ]

        if controller.state is None:
            controller.state = {}

        try:
            # Ensure the thread exists and belongs to the current user before applying commands.
            await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )

            for command in payload.commands:
                if isinstance(command, AddMessageCommand):
                    await assistant_service.add_message(
                        owner_user_id=current_user.user_id,
                        thread_id=payload.thread_id,
                        role=command.message.role,
                        parts=command.message.parts,
                    )
                elif isinstance(command, ApproveActionCommand):
                    await assistant_service.approve_action(
                        owner_user_id=current_user.user_id,
                        owner_user_email=current_user.email,
                        thread_id=payload.thread_id,
                        action_request_id=command.action_request_id,
                        is_user_active=current_user.is_active,
                        authorize_tool_access=lambda tool_name: (
                            authorization_service.authorize_tool_access(
                                current_user,
                                tool_name,
                            )
                        ),
                    )
                elif isinstance(command, DenyActionCommand):
                    await assistant_service.deny_action(
                        owner_user_id=current_user.user_id,
                        owner_user_email=current_user.email,
                        thread_id=payload.thread_id,
                        action_request_id=command.action_request_id,
                    )
                elif isinstance(command, AddToolResultCommand):
                    await assistant_service.add_tool_result(
                        owner_user_id=current_user.user_id,
                        owner_user_email=current_user.email,
                        thread_id=payload.thread_id,
                        tool_call_id=command.tool_call_id,
                        result=command.result,
                    )

            canonical_state = await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )
            controller.state["messages"] = canonical_state["messages"]
            controller.state["isRunning"] = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, HTTPException):
                logger.info(
                    "Assistant run failed (pre-agent) (status_code=%s thread_id=%s user_id=%s)",
                    exc.status_code,
                    payload.thread_id,
                    current_user.user_id,
                )
            else:
                logger.exception(
                    "Assistant run failed (pre-agent) (thread_id=%s user_id=%s)",
                    payload.thread_id,
                    current_user.user_id,
                )
            controller.state["isRunning"] = False

            safe_messages: list[object]
            try:
                safe_state = await assistant_service.load_state(
                    owner_user_id=current_user.user_id,
                    thread_id=payload.thread_id,
                )
                safe_messages_obj = safe_state.get("messages")
                safe_messages = (
                    list(safe_messages_obj)
                    if isinstance(safe_messages_obj, list)
                    else []
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                existing_messages_obj = controller.state.get("messages")
                safe_messages = (
                    list(existing_messages_obj)
                    if isinstance(existing_messages_obj, list)
                    else []
                )

            safe_messages = _remove_streaming_placeholder(safe_messages)
            safe_messages.append(_make_error_message(text=error_text))
            controller.state["messages"] = safe_messages

            state_manager = getattr(controller, "_state_manager", None)
            if state_manager is not None:
                try:
                    state_manager.flush()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            await asyncio.sleep(0)
            return

        should_run_agent = any(
            (isinstance(command, AddMessageCommand) and command.message.role == "user")
            or isinstance(command, ApproveActionCommand)
            or isinstance(command, AddToolResultCommand)
            for command in payload.commands
        )
        if should_run_agent:
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

                base_messages_obj = canonical_state.get("messages")
                base_messages: list[object] = (
                    list(base_messages_obj)
                    if isinstance(base_messages_obj, list)
                    else []
                )

                def _make_streaming_message(text: str) -> dict[str, object]:
                    return {
                        "id": "assistant-streaming",
                        "role": "assistant",
                        "parts": [{"type": "text", "text": text}],
                    }

                # Emit a running assistant message immediately so the UI can show
                # a first-token loading state before any text arrives.
                controller.state["messages"] = [
                    *base_messages,
                    _make_streaming_message(""),
                ]

                # Flush pending state updates before the first delta.
                state_manager = getattr(controller, "_state_manager", None)
                if state_manager is not None:
                    state_manager.flush()
                await asyncio.sleep(0)

                streamed_text = ""

                async def _on_text_delta(delta: str) -> None:
                    nonlocal streamed_text
                    if not delta:
                        return
                    if _controller_is_cancelled(controller):
                        raise asyncio.CancelledError
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise asyncio.CancelledError

                    streamed_text += delta
                    controller.state["messages"] = [
                        *base_messages,
                        _make_streaming_message(streamed_text),
                    ]
                    state_manager = getattr(controller, "_state_manager", None)
                    if state_manager is not None:
                        state_manager.flush()
                    await asyncio.sleep(0)

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
                        "Assistant run failed (agent) (status_code=%s thread_id=%s user_id=%s)",
                        exc.status_code,
                        payload.thread_id,
                        current_user.user_id,
                    )
                else:
                    logger.exception(
                        "Assistant run failed (thread_id=%s user_id=%s)",
                        payload.thread_id,
                        current_user.user_id,
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
                    logger.exception(
                        "Failed to persist assistant error message (thread_id=%s user_id=%s)",
                        payload.thread_id,
                        current_user.user_id,
                    )

                controller.state["isRunning"] = False
                try:
                    failed_state = await assistant_service.load_state(
                        owner_user_id=current_user.user_id,
                        thread_id=payload.thread_id,
                    )
                    controller.state["messages"] = failed_state["messages"]

                    # If we couldn't persist the assistant error message, ensure the
                    # client still sees an error by appending one locally.
                    if not persisted_error_message:
                        canonical_messages_obj = controller.state.get("messages")
                        canonical_messages: list[object] = (
                            list(canonical_messages_obj)
                            if isinstance(canonical_messages_obj, list)
                            else []
                        )
                        canonical_messages = _remove_streaming_placeholder(
                            canonical_messages
                        )
                        canonical_messages.append(_make_error_message(text=error_text))
                        controller.state["messages"] = canonical_messages
                except asyncio.CancelledError:
                    raise
                except Exception:
                    existing_messages_obj = controller.state.get("messages")
                    existing_messages: list[object] = (
                        list(existing_messages_obj)
                        if isinstance(existing_messages_obj, list)
                        else []
                    )
                    existing_messages = _remove_streaming_placeholder(existing_messages)
                    existing_messages.append(_make_error_message(text=error_text))
                    controller.state["messages"] = existing_messages

                state_manager = getattr(controller, "_state_manager", None)
                if state_manager is not None:
                    try:
                        state_manager.flush()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                await asyncio.sleep(0)
                return

        try:
            updated_state = await assistant_service.load_state(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
            )
            controller.state["messages"] = updated_state["messages"]
            controller.state["isRunning"] = False
        except asyncio.CancelledError:
            raise
        except Exception:
            # Avoid escaping exceptions at the end of the callback; keep the stream alive.
            logger.exception(
                "Assistant state refresh failed (thread_id=%s user_id=%s)",
                payload.thread_id,
                current_user.user_id,
            )
            controller.state["isRunning"] = False

            existing_messages_obj = controller.state.get("messages")
            existing_messages: list[object] = (
                list(existing_messages_obj)
                if isinstance(existing_messages_obj, list)
                else []
            )
            existing_messages = _remove_streaming_placeholder(existing_messages)
            existing_messages.append(_make_error_message(text=error_text))
            controller.state["messages"] = existing_messages

            state_manager = getattr(controller, "_state_manager", None)
            if state_manager is not None:
                try:
                    state_manager.flush()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            await asyncio.sleep(0)
            return

    stream = create_run(run_callback, state=payload.state)
    return AssistantTransportResponse(stream)


async def _stream_assistant_text(
    controller: RunController, text_deltas: list[str]
) -> None:
    if not text_deltas:
        return
    if controller.state is None:
        controller.state = {"messages": []}

    base_messages = list(controller.state.get("messages", []))
    streaming_message = {
        "id": "assistant-streaming",
        "role": "assistant",
        "parts": [{"type": "text", "text": ""}],
    }
    base_messages.append(streaming_message)
    controller.state["messages"] = base_messages

    for chunk in text_deltas:
        if _controller_is_cancelled(controller):
            raise asyncio.CancelledError
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        cast_part = streaming_message["parts"][0]
        cast_part["text"] += chunk
        controller.state["messages"] = base_messages
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise


def _controller_is_cancelled(controller: RunController) -> bool:
    value = getattr(controller, "is_cancelled", False)
    if callable(value):
        try:
            return bool(value())
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
    return bool(value)
