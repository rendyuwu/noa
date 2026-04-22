from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping
from typing import Any, Awaitable, Callable, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse
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
    assistant_domain_error,
    assistant_http_error,
    to_assistant_http_error,
)
from noa_api.api.assistant.assistant_operations import (
    _record_assistant_failure_telemetry,
    _resume_waiting_run_state,
    execute_active_run,
    prepare_assistant_transport,
)
from noa_api.api.assistant.assistant_run_stream import (
    build_run_snapshot_event,
    encode_sse_event,
)
from noa_api.api.assistant.assistant_runs import AssistantRunCoordinator
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.api.assistant.assistant_repository import SQLAssistantRepository
from noa_api.api.assistant.assistant_streaming import _stream_assistant_text
from noa_api.api.assistant.assistant_tool_result_operations import record_tool_result
from noa_api.core.agent.runner import (
    AgentRunner,
    AgentRunnerResult,
    create_default_llm_client,
)
from noa_api.core.config import get_app_settings
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    SQLAuthorizationRepository,
    get_authorization_service,
)
from noa_api.core.logging_context import log_context
from noa_api.core.request_context import get_request_id
from noa_api.core.telemetry import get_telemetry_recorder
from noa_api.storage.postgres.action_tool_runs import (
    ActionToolRunService,
    SQLActionToolRunRepository,
)
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    AssistantRunStatus,
    ToolRunStatus,
)
from noa_api.storage.postgres.models import ActionRequest
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.workflow_todos import (
    SQLWorkflowTodoRepository,
    WorkflowTodoService,
)

router = APIRouter(tags=["assistant"])

logger = logging.getLogger(__name__)
_RUN_COORDINATOR = AssistantRunCoordinator(instance_id="api-1")

__all__ = ["_stream_assistant_text", "get_assistant_service", "router"]

_ACTIVE_RUN_CONFLICT_DETAIL = "Thread already has an active assistant run"


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


class AssistantWorkflowTodo(BaseModel):
    content: str
    status: str
    priority: str


class AssistantPendingApproval(BaseModel):
    action_request_id: str = Field(alias="actionRequestId")
    tool_name: str = Field(alias="toolName")
    risk: str
    arguments: dict[str, Any]
    status: str

    model_config = {"populate_by_name": True}


class AssistantActionRequest(BaseModel):
    action_request_id: str = Field(alias="actionRequestId")
    tool_name: str = Field(alias="toolName")
    risk: str
    arguments: dict[str, Any]
    status: str
    lifecycle_status: str = Field(alias="lifecycleStatus")

    model_config = {"populate_by_name": True}


class AssistantThreadStateResponse(BaseModel):
    messages: list[AssistantThreadStateMessage]
    workflow: list[AssistantWorkflowTodo] = Field(default_factory=list)
    pending_approvals: list[AssistantPendingApproval] = Field(
        default_factory=list,
        alias="pendingApprovals",
    )
    action_requests: list[AssistantActionRequest] = Field(
        default_factory=list,
        alias="actionRequests",
    )
    is_running: bool = Field(alias="isRunning")
    run_status: str | None = Field(default=None, alias="runStatus")
    active_run_id: str | None = Field(default=None, alias="activeRunId")
    waiting_for_approval: bool = Field(default=False, alias="waitingForApproval")
    last_error_reason: str | None = Field(default=None, alias="lastErrorReason")

    model_config = {"populate_by_name": True}


class AssistantRunAckResponse(BaseModel):
    thread_id: str = Field(alias="threadId")
    active_run_id: str | None = Field(default=None, alias="activeRunId")
    run_status: str | None = Field(default=None, alias="runStatus")

    model_config = {"populate_by_name": True}


def _serialize_pending_approval(request: ActionRequest) -> dict[str, object]:
    return {
        "actionRequestId": str(request.id),
        "toolName": request.tool_name,
        "risk": request.risk.value,
        "arguments": redact_sensitive_data(request.args),
        "status": request.status.value,
    }


def _action_request_lifecycle_status(
    request: ActionRequest,
    *,
    latest_tool_run_status: ToolRunStatus | None,
) -> str:
    if request.status == ActionRequestStatus.PENDING:
        return "requested"
    if request.status == ActionRequestStatus.DENIED:
        return "denied"
    if latest_tool_run_status == ToolRunStatus.STARTED:
        return "executing"
    if latest_tool_run_status == ToolRunStatus.COMPLETED:
        return "finished"
    if latest_tool_run_status == ToolRunStatus.FAILED:
        return "failed"
    return "approved"


def _serialize_action_request(
    request: ActionRequest,
    *,
    latest_tool_run_status: ToolRunStatus | None,
) -> dict[str, object]:
    return {
        "actionRequestId": str(request.id),
        "toolName": request.tool_name,
        "risk": request.risk.value,
        "arguments": redact_sensitive_data(request.args),
        "status": request.status.value,
        "lifecycleStatus": _action_request_lifecycle_status(
            request,
            latest_tool_run_status=latest_tool_run_status,
        ),
    }


class AssistantService:
    def __init__(
        self,
        repository: Any,
        runner: Any,
        *,
        action_tool_run_service: ActionToolRunService,
        workflow_todo_service: WorkflowTodoService | None = None,
        session: AsyncSession,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._action_tool_run_service = action_tool_run_service
        self._workflow_todo_service = workflow_todo_service
        self._session = session

    async def _get_active_run(self, *, thread_id: UUID) -> Any | None:
        get_active_run = getattr(self._repository, "get_active_run", None)
        if get_active_run is None:
            return None
        return await get_active_run(thread_id=thread_id)

    async def create_run(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        owner_instance_id: str,
    ) -> Any:
        create_assistant_run = getattr(self._repository, "create_assistant_run", None)
        if create_assistant_run is None:
            raise RuntimeError("Assistant run persistence is unavailable")
        return await create_assistant_run(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            owner_instance_id=owner_instance_id,
        )

    async def get_run(self, *, run_id: UUID) -> Any | None:
        get_assistant_run = getattr(self._repository, "get_assistant_run", None)
        if get_assistant_run is None:
            return None
        return await get_assistant_run(run_id=run_id)

    async def mark_run_running(self, *, run_id: UUID) -> Any | None:
        mark_run_running = getattr(self._repository, "mark_run_running", None)
        if mark_run_running is None:
            return None
        return await mark_run_running(run_id=run_id)

    async def mark_run_waiting_approval(
        self, *, run_id: UUID, action_request_id: UUID
    ) -> Any | None:
        mark_run_waiting_approval = getattr(
            self._repository, "mark_run_waiting_approval", None
        )
        if mark_run_waiting_approval is None:
            return None
        return await mark_run_waiting_approval(
            run_id=run_id,
            action_request_id=action_request_id,
        )

    async def append_run_snapshot(
        self, *, run_id: UUID, snapshot: dict[str, object]
    ) -> Any | None:
        append_run_snapshot = getattr(self._repository, "append_run_snapshot", None)
        if append_run_snapshot is None:
            return None
        return await append_run_snapshot(run_id=run_id, snapshot=snapshot)

    async def mark_run_completed(self, *, run_id: UUID) -> Any | None:
        mark_run_completed = getattr(self._repository, "mark_run_completed", None)
        if mark_run_completed is None:
            return None
        return await mark_run_completed(run_id=run_id)

    async def mark_run_failed(self, *, run_id: UUID, reason: str) -> Any | None:
        mark_run_failed = getattr(self._repository, "mark_run_failed", None)
        if mark_run_failed is None:
            return None
        return await mark_run_failed(run_id=run_id, reason=reason)

    async def fail_run_if_owner_matches(
        self,
        *,
        run_id: UUID,
        owner_instance_id: str,
        reason: str,
    ) -> Any | None:
        fail_run_if_owner_matches = getattr(
            self._repository, "fail_run_if_owner_matches", None
        )
        if fail_run_if_owner_matches is None:
            return None
        return await fail_run_if_owner_matches(
            run_id=run_id,
            owner_instance_id=owner_instance_id,
            reason=reason,
        )

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
        workflow = (
            await self._workflow_todo_service.list_workflow(thread_id=thread_id)
            if self._workflow_todo_service is not None
            else []
        )
        action_requests = await self._repository.list_action_requests(
            thread_id=thread_id
        )
        latest_tool_run_status_by_request_id: dict[UUID, ToolRunStatus] = {}
        for tool_run in await self._repository.list_action_tool_runs(
            thread_id=thread_id
        ):
            if tool_run.action_request_id is None:
                continue
            latest_tool_run_status_by_request_id[tool_run.action_request_id] = (
                tool_run.status
            )
        pending_action_requests = await self._repository.get_pending_action_requests(
            thread_id=thread_id
        )
        active_run = await self._get_active_run(thread_id=thread_id)
        active_run_status = (
            getattr(active_run.status, "value", active_run.status)
            if active_run is not None
            else None
        )
        return {
            "messages": [
                {
                    "id": str(message.id),
                    "role": message.role,
                    "parts": message.content,
                }
                for message in messages
            ],
            "workflow": workflow,
            "pendingApprovals": [
                _serialize_pending_approval(request)
                for request in pending_action_requests
            ],
            "actionRequests": [
                _serialize_action_request(
                    request,
                    latest_tool_run_status=latest_tool_run_status_by_request_id.get(
                        request.id
                    ),
                )
                for request in action_requests
            ],
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
        parts: list[dict[str, Any]],
    ) -> None:
        _ = owner_user_id
        if role == "user":
            active_run = await self._get_active_run(thread_id=thread_id)
            if active_run is not None:
                raise assistant_domain_error(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_ACTIVE_RUN_CONFLICT_DETAIL,
                )
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
            session=self._session,
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


def _build_assistant_service(
    *, session: AsyncSession, app_settings: Any
) -> AssistantService:
    action_tool_run_service = ActionToolRunService(
        repository=SQLActionToolRunRepository(session)
    )
    return AssistantService(
        SQLAssistantRepository(session),
        AgentRunner(
            llm_client=create_default_llm_client(app_settings),
            action_tool_run_service=action_tool_run_service,
            session=session,
        ),
        action_tool_run_service=action_tool_run_service,
        workflow_todo_service=WorkflowTodoService(
            repository=SQLWorkflowTodoRepository(session)
        ),
        session=session,
    )


def _build_authorization_service(*, session: AsyncSession) -> AuthorizationService:
    return AuthorizationService(repository=SQLAuthorizationRepository(session))


async def get_assistant_service(
    request: Request,
) -> AsyncGenerator[AssistantService, None]:
    app_settings = get_app_settings(request.app)
    async with get_session_factory()() as session:
        service = _build_assistant_service(session=session, app_settings=app_settings)
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_assistant_run_coordinator() -> AssistantRunCoordinator:
    return _RUN_COORDINATOR


def _coerce_run_id(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _extract_waiting_action_request_id(state: dict[str, object]) -> UUID | None:
    if not bool(state.get("waitingForApproval")):
        return None
    pending_approvals = state.get("pendingApprovals")
    if not isinstance(pending_approvals, list):
        return None
    for pending_approval in pending_approvals:
        if not isinstance(pending_approval, dict):
            continue
        pending_approval_map = cast(Mapping[str, object], pending_approval)
        action_request_id = _coerce_run_id(pending_approval_map.get("actionRequestId"))
        if action_request_id is not None:
            return action_request_id
    return None


def _canonical_active_run_id(state: dict[str, object]) -> UUID | None:
    active_run_id = state.get("activeRunId")
    if not isinstance(active_run_id, str):
        return None
    try:
        return UUID(active_run_id)
    except ValueError:
        return None


def _should_resume_existing_run(
    *, command_types: list[str], canonical_state: dict[str, object]
) -> bool:
    if canonical_state.get("runStatus") != AssistantRunStatus.WAITING_APPROVAL.value:
        return True
    return "approve-action" in command_types


def _coordinator_task_done(
    *, coordinator: AssistantRunCoordinator, run_id: UUID
) -> bool | None:
    task = _coordinator_task(coordinator=coordinator, run_id=run_id)
    if task is None:
        return None
    done = getattr(task, "done", None)
    if not callable(done):
        return None
    return bool(done())


def _coordinator_task(
    *, coordinator: AssistantRunCoordinator, run_id: UUID
) -> asyncio.Task[object] | None:
    tracked_runs = getattr(coordinator, "_tasks", None)
    if not isinstance(tracked_runs, dict):
        return None
    tracked_run = tracked_runs.get(run_id)
    if tracked_run is None:
        return None
    task = getattr(tracked_run, "task", None)
    if task is None:
        return None
    return cast(asyncio.Task[object], task)


def _coordinator_sequence(
    *, coordinator: AssistantRunCoordinator, run_id: UUID
) -> int | None:
    sequences = getattr(coordinator, "_sequences", None)
    if not isinstance(sequences, dict):
        return None
    sequence = sequences.get(run_id)
    return sequence if isinstance(sequence, int) else None


def _snapshot_is_terminal(snapshot: Mapping[str, object]) -> bool:
    run_status = snapshot.get("runStatus")
    return run_status in {
        AssistantRunStatus.COMPLETED.value,
        AssistantRunStatus.FAILED.value,
        AssistantRunStatus.WAITING_APPROVAL.value,
    }


def _terminal_live_event(
    *,
    coordinator: AssistantRunCoordinator,
    run_id: UUID,
    fallback_snapshot: Mapping[str, object] | None,
    fallback_sequence: int,
) -> bytes | None:
    snapshot = coordinator.get_snapshot(run_id=run_id)
    if snapshot is None and fallback_snapshot is not None:
        snapshot = dict(fallback_snapshot)
    if snapshot is None or not _snapshot_is_terminal(snapshot):
        return None

    sequence = _coordinator_sequence(coordinator=coordinator, run_id=run_id)
    if sequence is None:
        sequence = fallback_sequence

    return encode_sse_event(
        event=build_run_snapshot_event(sequence=sequence, snapshot=snapshot)
    )


def _terminal_failure_reason(
    state: dict[str, object], *, agent_error_reason: str | None
) -> str | None:
    if isinstance(agent_error_reason, str) and agent_error_reason:
        return agent_error_reason

    run_status = state.get("runStatus")
    if run_status == AssistantRunStatus.FAILED.value:
        reason = state.get("lastErrorReason")
        if isinstance(reason, str) and reason:
            return reason
        return None

    return None


def _state_has_current_error_message(
    state: dict[str, object], *, previous_message_count: int
) -> bool:
    messages = state.get("messages")
    if not isinstance(messages, list) or previous_message_count < 0:
        return False
    current_messages = messages[previous_message_count:]
    for message in current_messages:
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
            if part.get("text") == "Assistant run failed. Please try again.":
                return True
    return False


async def _wait_for_tracked_run_completion(
    *, coordinator: AssistantRunCoordinator, run_id: UUID
) -> None:
    task = _coordinator_task(coordinator=coordinator, run_id=run_id)
    if task is None:
        if coordinator.has_run(run_id=run_id):
            coordinator.remove_run(run_id=run_id)
        return

    if not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "assistant_live_stream_terminal_wait_failed",
                extra={"run_id": str(run_id)},
            )

    if task.done() and coordinator.has_run(run_id=run_id):
        if not task.cancelled():
            _ = task.exception()
        coordinator.remove_run(run_id=run_id)


async def _persist_terminal_run_state(
    *,
    service: Any,
    handle: Any,
    run_id: UUID,
    final_state: dict[str, object],
    agent_error_reason: str | None,
    previous_message_count: int,
) -> None:
    waiting_action_request_id = _extract_waiting_action_request_id(final_state)
    terminal_failure_reason = _terminal_failure_reason(
        final_state,
        agent_error_reason=agent_error_reason,
    )
    if terminal_failure_reason is None and _state_has_current_error_message(
        final_state,
        previous_message_count=previous_message_count,
    ):
        terminal_failure_reason = "Assistant run failed. Please try again."

    terminal_snapshot = dict(final_state)
    terminal_snapshot["activeRunId"] = str(run_id)

    if waiting_action_request_id is not None:
        terminal_snapshot["isRunning"] = False
        terminal_snapshot["runStatus"] = AssistantRunStatus.WAITING_APPROVAL.value
        terminal_snapshot["waitingForApproval"] = True
        terminal_snapshot["lastErrorReason"] = None
        handle.publish_snapshot(snapshot=terminal_snapshot)
        await service.append_run_snapshot(run_id=run_id, snapshot=terminal_snapshot)
        await service.mark_run_waiting_approval(
            run_id=run_id,
            action_request_id=waiting_action_request_id,
        )
        return

    if terminal_failure_reason is not None:
        terminal_snapshot["isRunning"] = False
        terminal_snapshot["runStatus"] = AssistantRunStatus.FAILED.value
        terminal_snapshot["waitingForApproval"] = False
        terminal_snapshot["lastErrorReason"] = terminal_failure_reason
        handle.publish_snapshot(snapshot=terminal_snapshot)
        await service.append_run_snapshot(run_id=run_id, snapshot=terminal_snapshot)
        await service.mark_run_failed(run_id=run_id, reason=terminal_failure_reason)
        return

    terminal_snapshot["isRunning"] = False
    terminal_snapshot["runStatus"] = AssistantRunStatus.COMPLETED.value
    terminal_snapshot["waitingForApproval"] = False
    terminal_snapshot["lastErrorReason"] = None
    handle.publish_snapshot(snapshot=terminal_snapshot)
    await service.append_run_snapshot(run_id=run_id, snapshot=terminal_snapshot)
    await service.mark_run_completed(run_id=run_id)


async def _execute_detached_run_job(
    *,
    request: Request,
    payload: AssistantRequest,
    current_user: AuthorizationUser,
    command_types: list[str],
    canonical_state: dict[str, object],
    run_id: UUID,
    handle: Any,
    assistant_service: Any,
    authorization_service: Any,
) -> None:
    agent_error_reason: str | None = None

    class _ObservedAssistantService:
        def __init__(self, wrapped: Any) -> None:
            self._wrapped = wrapped

        def __getattr__(self, name: str) -> Any:
            return getattr(self._wrapped, name)

        async def run_agent_turn(self, **kwargs: Any) -> AgentRunnerResult:
            nonlocal agent_error_reason
            try:
                return await self._wrapped.run_agent_turn(**kwargs)
            except Exception as exc:
                agent_error_reason = str(exc) or type(exc).__name__
                raise

    observed_service = _ObservedAssistantService(assistant_service)
    previous_message_count = len(
        cast(list[object], canonical_state.get("messages") or [])
    )
    await observed_service.mark_run_running(run_id=run_id)

    async def _persist_snapshot(snapshot: dict[str, object]) -> None:
        await observed_service.append_run_snapshot(run_id=run_id, snapshot=snapshot)

    final_state = await execute_active_run(
        run_handle=handle,
        payload=payload,
        current_user=current_user,
        assistant_service=cast(Any, observed_service),
        authorization_service=authorization_service,
        canonical_state=canonical_state,
        command_types=command_types,
        telemetry=get_telemetry_recorder(request.app),
        on_snapshot=_persist_snapshot,
    )
    await _persist_terminal_run_state(
        service=observed_service,
        handle=handle,
        run_id=run_id,
        final_state=final_state,
        agent_error_reason=agent_error_reason,
        previous_message_count=previous_message_count,
    )


async def _run_detached_assistant_turn(
    *,
    request: Request,
    payload: AssistantRequest,
    current_user: AuthorizationUser,
    run_id: UUID,
    command_types: list[str],
    canonical_state: dict[str, object],
    coordinator: AssistantRunCoordinator,
    assistant_service: Any,
    authorization_service: Any,
) -> None:
    async def _job(handle: Any) -> object:
        if isinstance(assistant_service, AssistantService) and isinstance(
            authorization_service, AuthorizationService
        ):
            app_settings = get_app_settings(request.app)
            async with get_session_factory()() as session:
                service = _build_assistant_service(
                    session=session,
                    app_settings=app_settings,
                )
                authz = _build_authorization_service(session=session)
                try:
                    await _execute_detached_run_job(
                        request=request,
                        payload=payload,
                        current_user=current_user,
                        command_types=command_types,
                        canonical_state=canonical_state,
                        run_id=run_id,
                        handle=handle,
                        assistant_service=service,
                        authorization_service=authz,
                    )
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
                return None

        await _execute_detached_run_job(
            request=request,
            payload=payload,
            current_user=current_user,
            command_types=command_types,
            canonical_state=canonical_state,
            run_id=run_id,
            handle=handle,
            assistant_service=assistant_service,
            authorization_service=authorization_service,
        )
        return None

    try:
        coordinator.start_detached_run(run_id=run_id, job_factory=_job)
    except ValueError:
        logger.warning(
            "assistant_run_already_tracked",
            extra={"run_id": str(run_id)},
        )


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


@router.post("/assistant", response_model=AssistantRunAckResponse)
async def assistant_transport(
    request: Request,
    payload: AssistantRequest,
    current_user: AuthorizationUser = Depends(_require_active_user),
    assistant_service: AssistantService = Depends(get_assistant_service),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    coordinator: AssistantRunCoordinator = Depends(get_assistant_run_coordinator),
) -> AssistantRunAckResponse:
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
            canonical_state = _resume_waiting_run_state(
                command_types=command_types,
                canonical_state=prepared.canonical_state,
            )
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

    existing_run_id = _canonical_active_run_id(canonical_state)
    if (
        not should_run_agent(payload.commands)
        and "deny-action" in command_types
        and existing_run_id is not None
        and canonical_state.get("runStatus")
        == AssistantRunStatus.WAITING_APPROVAL.value
    ):
        canonical_state = dict(canonical_state)
        canonical_state["activeRunId"] = str(existing_run_id)
        canonical_state["isRunning"] = False
        canonical_state["runStatus"] = AssistantRunStatus.COMPLETED.value
        canonical_state["waitingForApproval"] = False
        canonical_state["lastErrorReason"] = None
        await assistant_service.append_run_snapshot(
            run_id=existing_run_id,
            snapshot=canonical_state,
        )
        await assistant_service.mark_run_completed(run_id=existing_run_id)
        if _coordinator_task_done(coordinator=coordinator, run_id=existing_run_id) in {
            True,
            False,
        }:
            coordinator.remove_run(run_id=existing_run_id)

    if not should_run_agent(payload.commands):
        return AssistantRunAckResponse.model_validate(
            {
                "threadId": str(payload.thread_id),
                "activeRunId": canonical_state.get("activeRunId"),
                "runStatus": canonical_state.get("runStatus"),
            }
        )

    if existing_run_id is not None:
        if _should_resume_existing_run(
            command_types=command_types,
            canonical_state=canonical_state,
        ):
            tracked_done = _coordinator_task_done(
                coordinator=coordinator,
                run_id=existing_run_id,
            )
            if tracked_done in {True, False}:
                coordinator.remove_run(run_id=existing_run_id)
            await _run_detached_assistant_turn(
                request=request,
                payload=payload,
                current_user=current_user,
                run_id=existing_run_id,
                command_types=command_types,
                canonical_state=canonical_state,
                coordinator=coordinator,
                assistant_service=assistant_service,
                authorization_service=authorization_service,
            )
        return AssistantRunAckResponse.model_validate(
            {
                "threadId": str(payload.thread_id),
                "activeRunId": str(existing_run_id),
                "runStatus": canonical_state.get("runStatus"),
            }
        )

    try:
        run = await assistant_service.create_run(
            owner_user_id=current_user.user_id,
            thread_id=payload.thread_id,
            owner_instance_id=coordinator.instance_id,
        )
    except IntegrityError as exc:
        raise assistant_http_error(
            status_code=status.HTTP_409_CONFLICT,
            detail=_ACTIVE_RUN_CONFLICT_DETAIL,
        ) from exc
    run_id = cast(UUID, run.id)
    canonical_state["activeRunId"] = str(run_id)
    canonical_state["runStatus"] = AssistantRunStatus.STARTING.value
    canonical_state["isRunning"] = True
    canonical_state["waitingForApproval"] = False
    canonical_state["lastErrorReason"] = None

    await _run_detached_assistant_turn(
        request=request,
        payload=payload,
        current_user=current_user,
        run_id=run_id,
        command_types=command_types,
        canonical_state=canonical_state,
        coordinator=coordinator,
        assistant_service=assistant_service,
        authorization_service=authorization_service,
    )

    return AssistantRunAckResponse.model_validate(
        {
            "threadId": str(payload.thread_id),
            "activeRunId": str(run_id),
            "runStatus": AssistantRunStatus.STARTING.value,
        }
    )


@router.get("/assistant/runs/{run_id}/live")
async def get_assistant_run_live(
    run_id: UUID,
    current_user: AuthorizationUser = Depends(_require_active_user),
    assistant_service: AssistantService = Depends(get_assistant_service),
    coordinator: AssistantRunCoordinator = Depends(get_assistant_run_coordinator),
) -> StreamingResponse:
    run = await assistant_service.get_run(run_id=run_id)
    if run is None or run.owner_user_id != current_user.user_id:
        raise assistant_http_error(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
            error_code=THREAD_NOT_FOUND,
        )

    async def _event_stream() -> AsyncGenerator[bytes, None]:
        terminal_event = _terminal_live_event(
            coordinator=coordinator,
            run_id=run_id,
            fallback_snapshot=getattr(run, "live_snapshot", None),
            fallback_sequence=int(getattr(run, "sequence", 0) or 0),
        )
        if _coordinator_task_done(coordinator=coordinator, run_id=run_id) is True:
            if terminal_event is not None:
                yield terminal_event
            coordinator.remove_run(run_id=run_id)
            return

        if not coordinator.has_run(run_id=run_id) and getattr(
            run, "live_snapshot", None
        ):
            snapshot = dict(cast(dict[str, object], run.live_snapshot))
            sequence = _coordinator_sequence(
                coordinator=coordinator, run_id=run_id
            ) or int(getattr(run, "sequence", 0) or 0)
            yield encode_sse_event(
                event=build_run_snapshot_event(sequence=sequence, snapshot=snapshot)
            )
            return

        async for event in coordinator.subscribe(run_id=run_id):
            yield encode_sse_event(event=event)
            snapshot = event.get("snapshot")
            if not isinstance(snapshot, dict) or not _snapshot_is_terminal(
                cast(Mapping[str, object], snapshot)
            ):
                continue
            terminal_snapshot = cast(Mapping[str, object], snapshot)
            if (
                terminal_snapshot.get("runStatus")
                == AssistantRunStatus.WAITING_APPROVAL.value
            ):
                if (
                    _coordinator_task_done(coordinator=coordinator, run_id=run_id)
                    is True
                ):
                    coordinator.remove_run(run_id=run_id)
                break
            await _wait_for_tracked_run_completion(
                coordinator=coordinator,
                run_id=run_id,
            )
            return

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
