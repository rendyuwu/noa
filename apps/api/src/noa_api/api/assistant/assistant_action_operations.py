from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from inspect import signature
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.api.assistant.assistant_errors import (
    action_request_already_decided_error,
    action_request_not_found_error,
    change_approval_required_error,
    parse_action_request_id,
    tool_access_denied_error,
    user_pending_approval_error,
)
from noa_api.api.assistant.assistant_tool_execution import build_tool_result_part
from noa_api.api.assistant.assistant_tool_result_operations import (
    AssistantMessageAuditRepositoryProtocol,
)
from noa_api.core.logging_context import log_context
from noa_api.core.agent.runner import (
    _require_matching_preflight,
    _resolve_requested_server_id,
)
from noa_api.core.tool_error_sanitizer import SanitizedToolError, sanitize_tool_error
from noa_api.core.tools.argument_validation import validate_tool_arguments
from noa_api.core.tools.registry import get_tool_definition
from noa_api.core.workflows.registry import (
    build_workflow_todos,
    collect_recent_preflight_evidence,
    fetch_postflight_result,
    persist_workflow_todos,
)
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ActionRequestStatus, ToolRisk
from noa_api.storage.postgres.models import ActionRequest, ToolRun

logger = logging.getLogger(__name__)


class ApprovedToolExecutor(Protocol):
    async def __call__(
        self,
        *,
        started_tool_run: ToolRun,
        approved_request: ActionRequest,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        repository: AssistantMessageAuditRepositoryProtocol,
        action_tool_run_service: ActionToolRunService,
    ) -> None: ...


async def require_pending_action_request(
    *,
    owner_user_id: UUID,
    thread_id: UUID,
    action_request_id: str | None,
    action_tool_run_service: ActionToolRunService,
) -> ActionRequest:
    parsed_id = parse_action_request_id(action_request_id)
    request = await action_tool_run_service.get_action_request(
        action_request_id=parsed_id
    )
    if request is None:
        raise action_request_not_found_error()
    if request.thread_id != thread_id or request.requested_by_user_id != owner_user_id:
        raise action_request_not_found_error()
    if request.status != ActionRequestStatus.PENDING:
        raise action_request_already_decided_error()
    return request


async def deny_action_request(
    *,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    action_request_id: str | None,
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
    session: AsyncSession | None = None,
) -> None:
    request = await require_pending_action_request(
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        action_request_id=action_request_id,
        action_tool_run_service=action_tool_run_service,
    )

    try:
        denied = await action_tool_run_service.deny_action_request(
            action_request_id=request.id,
            decided_by_user_id=owner_user_id,
        )
    except ValueError as exc:
        raise action_request_already_decided_error() from exc
    if denied is None:
        raise action_request_not_found_error()

    tool = get_tool_definition(denied.tool_name)
    if tool is not None and tool.workflow_family is not None:
        working_messages = await _list_working_messages(
            repository=repository,
            thread_id=thread_id,
        )
        await persist_workflow_todos(
            session=session,
            thread_id=thread_id,
            todos=build_workflow_todos(
                tool_name=denied.tool_name,
                workflow_family=tool.workflow_family,
                args=denied.args,
                phase="denied",
                preflight_evidence=collect_recent_preflight_evidence(working_messages),
            ),
        )

    with log_context(
        action_request_id=str(denied.id),
        thread_id=str(thread_id),
        tool_name=denied.tool_name,
        user_id=str(owner_user_id),
    ):
        await repository.create_message(
            thread_id=thread_id,
            role="assistant",
            parts=[
                {
                    "type": "text",
                    "text": f"Denied action request for tool '{denied.tool_name}'.",
                }
            ],
        )
        await repository.create_audit_log(
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


async def approve_action_request(
    *,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    action_request_id: str | None,
    is_user_active: bool,
    authorize_tool_access: Callable[[str], Awaitable[bool]],
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
    execute_tool: ApprovedToolExecutor,
) -> None:
    if not is_user_active:
        raise user_pending_approval_error()

    request = await require_pending_action_request(
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        action_request_id=action_request_id,
        action_tool_run_service=action_tool_run_service,
    )
    if request.risk != ToolRisk.CHANGE:
        raise change_approval_required_error()
    if not await authorize_tool_access(request.tool_name):
        raise tool_access_denied_error()

    try:
        approved = await action_tool_run_service.approve_action_request(
            action_request_id=request.id,
            decided_by_user_id=owner_user_id,
        )
    except ValueError as exc:
        raise action_request_already_decided_error() from exc
    if approved is None:
        raise action_request_not_found_error()

    await repository.create_audit_log(
        event_type="action_approved",
        actor_email=owner_user_email,
        tool_name=approved.tool_name,
        metadata={
            "thread_id": str(thread_id),
            "action_request_id": str(approved.id),
        },
    )

    started = await action_tool_run_service.start_tool_run(
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
        await repository.create_audit_log(
            event_type="tool_started",
            actor_email=owner_user_email,
            tool_name=approved.tool_name,
            metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started.id),
                "action_request_id": str(approved.id),
            },
        )
        await repository.create_message(
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
        await execute_tool(
            started_tool_run=started,
            approved_request=approved,
            owner_user_id=owner_user_id,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            repository=repository,
            action_tool_run_service=action_tool_run_service,
        )


async def execute_approved_tool_run(
    *,
    started_tool_run: ToolRun,
    approved_request: ActionRequest,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
    session: AsyncSession | None,
) -> None:
    tool_call_id = str(started_tool_run.id)
    tool = get_tool_definition(approved_request.tool_name)
    if tool is None:
        error = "Requested tool is unavailable"
        await _persist_failed_tool_run(
            started_tool_run=started_tool_run,
            approved_request=approved_request,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            repository=repository,
            action_tool_run_service=action_tool_run_service,
            tool_run_error=error,
            tool_result={"error": error},
            audit_metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started_tool_run.id),
                "action_request_id": str(approved_request.id),
                "error": error,
            },
        )
        return
    if tool.risk != ToolRisk.CHANGE:
        error = "Approved tool risk mismatch"
        await _persist_failed_tool_run(
            started_tool_run=started_tool_run,
            approved_request=approved_request,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            repository=repository,
            action_tool_run_service=action_tool_run_service,
            tool_run_error=error,
            tool_result={
                "error": error,
                "expectedRisk": ToolRisk.CHANGE.value,
                "actualRisk": tool.risk.value,
            },
            audit_metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started_tool_run.id),
                "action_request_id": str(approved_request.id),
                "error": error,
            },
        )
        return

    preflight_error = await _validate_approved_tool_preflight(
        tool_name=approved_request.tool_name,
        args=approved_request.args,
        thread_id=thread_id,
        repository=repository,
        session=session,
    )
    if preflight_error is not None:
        await _persist_failed_tool_run(
            started_tool_run=started_tool_run,
            approved_request=approved_request,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            repository=repository,
            action_tool_run_service=action_tool_run_service,
            tool_run_error=preflight_error.error_code,
            tool_result=preflight_error.as_result(),
            audit_metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started_tool_run.id),
                "action_request_id": str(approved_request.id),
                "error": preflight_error.error,
                "error_code": preflight_error.error_code,
            },
        )
        return

    working_messages = await _list_working_messages(
        repository=repository,
        thread_id=thread_id,
    )
    preflight_evidence = collect_recent_preflight_evidence(working_messages)
    if tool.workflow_family is not None:
        await persist_workflow_todos(
            session=session,
            thread_id=thread_id,
            todos=build_workflow_todos(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=approved_request.args,
                phase="executing",
                preflight_evidence=preflight_evidence,
            ),
        )

    try:
        validate_tool_arguments(tool=tool, args=approved_request.args)
        result = await _execute_tool(
            tool=tool,
            args=approved_request.args,
            session=session,
            thread_id=thread_id,
            requested_by_user_id=owner_user_id,
        )
        completed = await action_tool_run_service.complete_tool_run(
            tool_run_id=started_tool_run.id,
            result=result,
        )
        persisted_result = (
            completed.result
            if completed is not None and isinstance(completed.result, dict)
            else result
        )
        postflight_result: dict[str, object] | None = None
        if tool.workflow_family is not None:
            postflight_result = await fetch_postflight_result(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=approved_request.args,
                session=session,
            )
            await persist_workflow_todos(
                session=session,
                thread_id=thread_id,
                todos=build_workflow_todos(
                    tool_name=approved_request.tool_name,
                    workflow_family=tool.workflow_family,
                    args=approved_request.args,
                    phase="completed",
                    preflight_evidence=preflight_evidence,
                    result=persisted_result,
                    postflight_result=postflight_result,
                ),
            )
        await repository.create_message(
            thread_id=thread_id,
            role="tool",
            parts=[
                build_tool_result_part(
                    tool_name=approved_request.tool_name,
                    tool_call_id=tool_call_id,
                    result=persisted_result,
                    is_error=False,
                )
            ],
        )
        await repository.create_audit_log(
            event_type="tool_completed",
            actor_email=owner_user_email,
            tool_name=approved_request.tool_name,
            metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started_tool_run.id),
                "action_request_id": str(approved_request.id),
            },
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        sanitized_error = sanitize_tool_error(exc)
        logger.exception(
            "assistant_approved_tool_execution_failed",
            extra={
                "action_request_id": str(approved_request.id),
                "error_code": sanitized_error.error_code,
                "thread_id": str(thread_id),
                "tool_name": approved_request.tool_name,
                "tool_run_id": str(started_tool_run.id),
                "user_id": str(owner_user_id),
            },
        )
        if tool.workflow_family is not None:
            await persist_workflow_todos(
                session=session,
                thread_id=thread_id,
                todos=build_workflow_todos(
                    tool_name=approved_request.tool_name,
                    workflow_family=tool.workflow_family,
                    args=approved_request.args,
                    phase="failed",
                    preflight_evidence=preflight_evidence,
                    error_code=sanitized_error.error_code,
                ),
            )
        await _persist_failed_tool_run(
            started_tool_run=started_tool_run,
            approved_request=approved_request,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            repository=repository,
            action_tool_run_service=action_tool_run_service,
            tool_run_error=sanitized_error.error_code,
            tool_result=cast(dict[str, object], sanitized_error.as_result()),
            audit_metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started_tool_run.id),
                "action_request_id": str(approved_request.id),
                "error": sanitized_error.error,
                "error_code": sanitized_error.error_code,
            },
        )


async def _validate_approved_tool_preflight(
    *,
    tool_name: str,
    args: dict[str, object],
    thread_id: UUID,
    repository: AssistantMessageAuditRepositoryProtocol,
    session: AsyncSession | None,
) -> SanitizedToolError | None:
    messages = await repository.list_messages(thread_id=thread_id)
    requested_server_id = await _resolve_requested_server_id(args=args, session=session)
    working_messages: list[dict[str, object]] = []
    for message in messages:
        role = getattr(message, "role", None)
        parts = getattr(message, "content", None)
        if not isinstance(role, str) or not isinstance(parts, list):
            continue
        working_messages.append({"role": role, "parts": parts})
    return _require_matching_preflight(
        tool_name=tool_name,
        args=args,
        working_messages=working_messages,
        requested_server_id=requested_server_id,
    )


async def _list_working_messages(
    *,
    repository: AssistantMessageAuditRepositoryProtocol,
    thread_id: UUID,
) -> list[dict[str, object]]:
    messages = await repository.list_messages(thread_id=thread_id)
    working_messages: list[dict[str, object]] = []
    for message in messages:
        role = getattr(message, "role", None)
        parts = getattr(message, "content", None)
        if not isinstance(role, str) or not isinstance(parts, list):
            continue
        working_messages.append({"role": role, "parts": parts})
    return working_messages


async def _execute_tool(
    *,
    tool: Any,
    args: dict[str, object],
    session: AsyncSession | None,
    thread_id: UUID,
    requested_by_user_id: UUID,
) -> dict[str, object]:
    execute_parameters = signature(tool.execute).parameters
    execute_kwargs: dict[str, object] = dict(args)
    if session is not None and "session" in execute_parameters:
        execute_kwargs["session"] = session
    if "thread_id" in execute_parameters:
        execute_kwargs["thread_id"] = thread_id
    if "requested_by_user_id" in execute_parameters:
        execute_kwargs["requested_by_user_id"] = requested_by_user_id

    if execute_kwargs is not args:
        return await tool.execute(**execute_kwargs)
    return await tool.execute(**args)


async def _persist_failed_tool_run(
    *,
    started_tool_run: ToolRun,
    approved_request: ActionRequest,
    owner_user_email: str | None,
    thread_id: UUID,
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
    tool_run_error: str,
    tool_result: dict[str, object],
    audit_metadata: dict[str, object],
) -> None:
    _ = await action_tool_run_service.fail_tool_run(
        tool_run_id=started_tool_run.id,
        error=tool_run_error,
    )
    await repository.create_message(
        thread_id=thread_id,
        role="tool",
        parts=[
            build_tool_result_part(
                tool_name=approved_request.tool_name,
                tool_call_id=str(started_tool_run.id),
                result=tool_result,
                is_error=True,
            )
        ],
    )
    await repository.create_audit_log(
        event_type="tool_failed",
        actor_email=owner_user_email,
        tool_name=approved_request.tool_name,
        metadata=audit_metadata,
    )
