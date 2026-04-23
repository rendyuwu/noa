from __future__ import annotations

from typing import Any, Awaitable, Callable, cast
from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.api.assistant.action_requests import (
    approve_action_request as approve_action_request_operation,
    deny_action_request as deny_action_request_operation,
)
from noa_api.api.assistant.approved_execution import (
    execute_approved_tool_run,
)
from noa_api.api.assistant.assistant_errors import (
    assistant_domain_error,
    assistant_http_error,
)
from noa_api.api.assistant.assistant_tool_result_operations import record_tool_result
from noa_api.api.error_codes import THREAD_NOT_FOUND
from noa_api.core.agent.runner import AgentRunnerResult
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    AssistantRunStatus,
    ToolRunStatus,
)
from noa_api.storage.postgres.models import ActionRequest
from noa_api.storage.postgres.workflow_todos import WorkflowTodoService


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


_ACTIVE_RUN_CONFLICT_DETAIL = "Thread already has an active assistant run"


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
