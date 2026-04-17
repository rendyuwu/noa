from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from inspect import signature
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.api.assistant.assistant_errors import (
    action_request_already_decided_error,
    action_request_not_found_error,
    change_approval_required_error,
    change_reason_required_error,
    parse_action_request_id,
    tool_access_denied_error,
    user_pending_approval_error,
)
from noa_api.api.assistant.assistant_tool_execution import build_tool_result_part
from noa_api.api.assistant.assistant_tool_result_operations import (
    AssistantMessageAuditRepositoryProtocol,
)
from noa_api.core.logging_context import log_context
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.core.agent.runner import (
    _require_matching_preflight,
    _resolve_requested_server_id,
)
from noa_api.core.tool_error_sanitizer import SanitizedToolError, sanitize_tool_error
from noa_api.core.tools.argument_validation import validate_tool_arguments
from noa_api.core.tools.registry import get_tool_definition
from noa_api.core.workflows.registry import (
    build_workflow_evidence_template,
    build_workflow_reply_template,
    build_workflow_todos,
    collect_recent_preflight_evidence,
    fetch_postflight_result,
    persist_workflow_todos,
)
from noa_api.core.workflows.types import (
    workflow_evidence_template_payload,
    workflow_reply_template_payload,
)
from noa_api.storage.postgres.action_receipts import (
    ActionReceiptService,
    SQLActionReceiptRepository,
)
from noa_api.storage.postgres.action_tool_runs import (
    ActionToolRunService,
    decrypt_sensitive_args,
)
from noa_api.storage.postgres.lifecycle import ActionRequestStatus, ToolRisk
from noa_api.storage.postgres.models import ActionRequest, ToolRun

logger = logging.getLogger(__name__)


def _has_recorded_change_reason(args: dict[str, object]) -> bool:
    reason = args.get("reason")
    return isinstance(reason, str) and reason.strip() != ""


async def _emit_update_workflow_todo_messages(
    *,
    repository: AssistantMessageAuditRepositoryProtocol,
    thread_id: UUID,
    todos: list[dict[str, object]],
) -> None:
    tool_call_id = f"workflow-todo-{uuid4()}"
    await repository.create_message(
        thread_id=thread_id,
        role="assistant",
        parts=[
            {
                "type": "tool-call",
                "toolName": "update_workflow_todo",
                "toolCallId": tool_call_id,
                "args": {"todos": todos},
            }
        ],
    )
    await repository.create_message(
        thread_id=thread_id,
        role="tool",
        parts=[
            build_tool_result_part(
                tool_name="update_workflow_todo",
                tool_call_id=tool_call_id,
                result={"ok": True, "todos": todos},
                is_error=False,
            )
        ],
    )


async def _emit_workflow_receipt_messages(
    *,
    repository: AssistantMessageAuditRepositoryProtocol,
    thread_id: UUID,
    action_request_id: UUID,
    payload: dict[str, object],
) -> None:
    tool_call_id = f"receipt-{action_request_id}"
    await repository.create_message(
        thread_id=thread_id,
        role="assistant",
        parts=[
            {
                "type": "tool-call",
                "toolName": "workflow_receipt",
                "toolCallId": tool_call_id,
                "argsText": "Receipt",
                "args": {"actionRequestId": str(action_request_id)},
            }
        ],
    )
    await repository.create_message(
        thread_id=thread_id,
        role="tool",
        parts=[
            build_tool_result_part(
                tool_name="workflow_receipt",
                tool_call_id=tool_call_id,
                result=payload,
                is_error=False,
            )
        ],
    )


def _build_change_receipt_v1(
    *,
    thread_id: UUID,
    action_request_id: UUID,
    tool_run_id: UUID | None,
    tool_name: str,
    workflow_family: str,
    terminal_phase: str,
    reply_template: dict[str, object] | None,
    evidence_sections: list[dict[str, object]],
    error_code: str | None = None,
) -> dict[str, object]:
    receipt_id = f"receipt-{action_request_id}"
    generated_at = datetime.now(UTC).isoformat()
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "receiptId": receipt_id,
        "threadId": str(thread_id),
        "actionRequestId": str(action_request_id),
        "toolRunId": str(tool_run_id) if tool_run_id is not None else None,
        "toolName": tool_name,
        "workflowFamily": workflow_family,
        "terminalPhase": terminal_phase,
        "generatedAt": generated_at,
        "replyTemplate": reply_template,
        "evidenceSections": evidence_sections,
    }
    if error_code is not None:
        payload["errorCode"] = error_code
    return payload


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
    workflow_family: str | None = None
    if tool is not None:
        workflow_family = tool.workflow_family
    should_create_workflow_receipt = (
        workflow_family is not None and denied.risk == ToolRisk.CHANGE
    )
    receipt_payload: dict[str, object] | None = None
    should_emit_receipt = False
    if should_create_workflow_receipt:
        assert workflow_family is not None
        denied_args = decrypt_sensitive_args(denied.args)
        working_messages = await _list_working_messages(
            repository=repository,
            thread_id=thread_id,
        )
        preflight_evidence = collect_recent_preflight_evidence(working_messages)
        workflow_todos = build_workflow_todos(
            tool_name=denied.tool_name,
            workflow_family=workflow_family,
            args=denied_args,
            phase="denied",
            preflight_evidence=preflight_evidence,
        )
        await persist_workflow_todos(
            session=session,
            thread_id=thread_id,
            todos=workflow_todos,
        )
        if isinstance(workflow_todos, list):
            await _emit_update_workflow_todo_messages(
                repository=repository,
                thread_id=thread_id,
                todos=cast(list[dict[str, object]], workflow_todos),
            )

        reply_template = build_workflow_reply_template(
            tool_name=denied.tool_name,
            workflow_family=workflow_family,
            args=denied_args,
            phase="denied",
            preflight_evidence=preflight_evidence,
        )
        reply_payload = (
            workflow_reply_template_payload(reply_template)
            if reply_template is not None
            else None
        )
        evidence_template = build_workflow_evidence_template(
            tool_name=denied.tool_name,
            workflow_family=workflow_family,
            args=denied_args,
            phase="denied",
            preflight_evidence=preflight_evidence,
        )
        evidence_payload = (
            workflow_evidence_template_payload(evidence_template)
            if evidence_template is not None
            else None
        )
        evidence_sections: list[dict[str, object]] = []
        if evidence_payload is not None:
            raw_sections = evidence_payload.get("evidenceSections")
            if isinstance(raw_sections, list):
                evidence_sections = [
                    section for section in raw_sections if isinstance(section, dict)
                ]

        receipt_payload = _build_change_receipt_v1(
            thread_id=thread_id,
            action_request_id=denied.id,
            tool_run_id=None,
            tool_name=denied.tool_name,
            workflow_family=workflow_family,
            terminal_phase="denied",
            reply_template=reply_payload,
            evidence_sections=evidence_sections,
        )
        if session is not None:
            receipt_service = ActionReceiptService(
                repository=SQLActionReceiptRepository(session)
            )
            should_emit_receipt = (
                await receipt_service.create_action_receipt_if_missing(
                    action_request_id=denied.id,
                    tool_run_id=None,
                    terminal_phase="denied",
                    payload=receipt_payload,
                )
            )
        else:
            should_emit_receipt = True

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
                    "text": "Denied. Receipt below."
                    if should_create_workflow_receipt
                    else f"Denied action request for tool '{denied.tool_name}'.",
                }
            ],
        )
        if should_emit_receipt and receipt_payload is not None:
            await _emit_workflow_receipt_messages(
                repository=repository,
                thread_id=thread_id,
                action_request_id=denied.id,
                payload=receipt_payload,
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
    if not _has_recorded_change_reason(request.args):
        raise change_reason_required_error()
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
        args=decrypt_sensitive_args(approved.args),
        action_request_id=approved.id,
        requested_by_user_id=owner_user_id,
    )
    tool_call_id = str(started.id)
    execution_args = decrypt_sensitive_args(approved.args)
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
                    "args": redact_sensitive_data(execution_args),
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
            assistant_text=None,
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
            assistant_text=None,
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
        args=decrypt_sensitive_args(approved_request.args),
        thread_id=thread_id,
        repository=repository,
        session=session,
    )
    if preflight_error is not None:
        preflight_evidence: list[dict[str, object]] = []
        if tool.workflow_family is not None:
            working_messages = await _list_working_messages(
                repository=repository,
                thread_id=thread_id,
            )
            preflight_evidence = collect_recent_preflight_evidence(working_messages)
            failed_todos = build_workflow_todos(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=decrypt_sensitive_args(approved_request.args),
                phase="failed",
                preflight_evidence=preflight_evidence,
                error_code=preflight_error.error_code,
            )
            await persist_workflow_todos(
                session=session,
                thread_id=thread_id,
                todos=failed_todos,
            )
            if isinstance(failed_todos, list):
                await _emit_update_workflow_todo_messages(
                    repository=repository,
                    thread_id=thread_id,
                    todos=cast(list[dict[str, object]], failed_todos),
                )
        await _persist_failed_tool_run(
            started_tool_run=started_tool_run,
            approved_request=approved_request,
            owner_user_email=owner_user_email,
            thread_id=thread_id,
            repository=repository,
            action_tool_run_service=action_tool_run_service,
            tool_run_error=preflight_error.error_code,
            tool_result=preflight_error.as_result(),
            assistant_text=None,
            audit_metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started_tool_run.id),
                "action_request_id": str(approved_request.id),
                "error": preflight_error.error,
                "error_code": preflight_error.error_code,
            },
        )
        if tool.workflow_family is not None:
            reply_template = build_workflow_reply_template(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=decrypt_sensitive_args(approved_request.args),
                phase="failed",
                preflight_evidence=preflight_evidence,
                error_code=preflight_error.error_code,
            )
            reply_payload = (
                workflow_reply_template_payload(reply_template)
                if reply_template is not None
                else None
            )
            evidence_template = build_workflow_evidence_template(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=decrypt_sensitive_args(approved_request.args),
                phase="failed",
                preflight_evidence=preflight_evidence,
                error_code=preflight_error.error_code,
            )
            evidence_payload = (
                workflow_evidence_template_payload(evidence_template)
                if evidence_template is not None
                else None
            )
            evidence_sections: list[dict[str, object]] = []
            if evidence_payload is not None:
                raw_sections = evidence_payload.get("evidenceSections")
                if isinstance(raw_sections, list):
                    evidence_sections = [
                        section for section in raw_sections if isinstance(section, dict)
                    ]
            receipt_payload = _build_change_receipt_v1(
                thread_id=thread_id,
                action_request_id=approved_request.id,
                tool_run_id=started_tool_run.id,
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                terminal_phase="failed",
                reply_template=reply_payload,
                evidence_sections=evidence_sections,
                error_code=preflight_error.error_code,
            )
            should_emit_receipt = True
            if session is not None:
                receipt_service = ActionReceiptService(
                    repository=SQLActionReceiptRepository(session)
                )
                should_emit_receipt = (
                    await receipt_service.create_action_receipt_if_missing(
                        action_request_id=approved_request.id,
                        tool_run_id=started_tool_run.id,
                        terminal_phase="failed",
                        payload=receipt_payload,
                    )
                )
            if should_emit_receipt:
                await _emit_workflow_receipt_messages(
                    repository=repository,
                    thread_id=thread_id,
                    action_request_id=approved_request.id,
                    payload=receipt_payload,
                )
        return

    working_messages = await _list_working_messages(
        repository=repository,
        thread_id=thread_id,
    )
    execution_args = decrypt_sensitive_args(approved_request.args)
    preflight_evidence = collect_recent_preflight_evidence(working_messages)
    if tool.workflow_family is not None:
        executing_todos = build_workflow_todos(
            tool_name=approved_request.tool_name,
            workflow_family=tool.workflow_family,
            args=execution_args,
            phase="executing",
            preflight_evidence=preflight_evidence,
        )
        await persist_workflow_todos(
            session=session,
            thread_id=thread_id,
            todos=executing_todos,
        )
        if isinstance(executing_todos, list):
            await _emit_update_workflow_todo_messages(
                repository=repository,
                thread_id=thread_id,
                todos=cast(list[dict[str, object]], executing_todos),
            )

    try:
        validate_tool_arguments(tool=tool, args=execution_args)
        result = await _execute_tool(
            tool=tool,
            args=execution_args,
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
                args=execution_args,
                session=session,
            )
            completed_todos = build_workflow_todos(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=execution_args,
                phase="completed",
                preflight_evidence=preflight_evidence,
                result=persisted_result,
                postflight_result=postflight_result,
            )
            await persist_workflow_todos(
                session=session,
                thread_id=thread_id,
                todos=completed_todos,
            )
            if isinstance(completed_todos, list):
                await _emit_update_workflow_todo_messages(
                    repository=repository,
                    thread_id=thread_id,
                    todos=cast(list[dict[str, object]], completed_todos),
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
        if tool.workflow_family is not None:
            reply_template = build_workflow_reply_template(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=execution_args,
                phase="completed",
                preflight_evidence=preflight_evidence,
                result=persisted_result,
                postflight_result=postflight_result,
            )
            reply_payload = (
                workflow_reply_template_payload(reply_template)
                if reply_template is not None
                else None
            )
            evidence_template = build_workflow_evidence_template(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=execution_args,
                phase="completed",
                preflight_evidence=preflight_evidence,
                result=persisted_result,
                postflight_result=postflight_result,
            )
            evidence_payload = (
                workflow_evidence_template_payload(evidence_template)
                if evidence_template is not None
                else None
            )
            evidence_sections: list[dict[str, object]] = []
            if evidence_payload is not None:
                raw_sections = evidence_payload.get("evidenceSections")
                if isinstance(raw_sections, list):
                    evidence_sections = [
                        section for section in raw_sections if isinstance(section, dict)
                    ]
            receipt_payload = _build_change_receipt_v1(
                thread_id=thread_id,
                action_request_id=approved_request.id,
                tool_run_id=started_tool_run.id,
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                terminal_phase="completed",
                reply_template=reply_payload,
                evidence_sections=evidence_sections,
            )
            should_emit_receipt = True
            if session is not None:
                receipt_service = ActionReceiptService(
                    repository=SQLActionReceiptRepository(session)
                )
                should_emit_receipt = (
                    await receipt_service.create_action_receipt_if_missing(
                        action_request_id=approved_request.id,
                        tool_run_id=started_tool_run.id,
                        terminal_phase="completed",
                        payload=receipt_payload,
                    )
                )
            if should_emit_receipt:
                await _emit_workflow_receipt_messages(
                    repository=repository,
                    thread_id=thread_id,
                    action_request_id=approved_request.id,
                    payload=receipt_payload,
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
            failed_todos = build_workflow_todos(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=execution_args,
                phase="failed",
                preflight_evidence=preflight_evidence,
                error_code=sanitized_error.error_code,
            )
            await persist_workflow_todos(
                session=session,
                thread_id=thread_id,
                todos=failed_todos,
            )
            if isinstance(failed_todos, list):
                await _emit_update_workflow_todo_messages(
                    repository=repository,
                    thread_id=thread_id,
                    todos=cast(list[dict[str, object]], failed_todos),
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
            assistant_text=None,
            audit_metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(started_tool_run.id),
                "action_request_id": str(approved_request.id),
                "error": sanitized_error.error,
                "error_code": sanitized_error.error_code,
            },
        )

        if tool.workflow_family is not None:
            reply_template = build_workflow_reply_template(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=execution_args,
                phase="failed",
                preflight_evidence=preflight_evidence,
                error_code=sanitized_error.error_code,
            )
            reply_payload = (
                workflow_reply_template_payload(reply_template)
                if reply_template is not None
                else None
            )
            evidence_template = build_workflow_evidence_template(
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                args=execution_args,
                phase="failed",
                preflight_evidence=preflight_evidence,
                error_code=sanitized_error.error_code,
            )
            evidence_payload = (
                workflow_evidence_template_payload(evidence_template)
                if evidence_template is not None
                else None
            )
            evidence_sections: list[dict[str, object]] = []
            if evidence_payload is not None:
                raw_sections = evidence_payload.get("evidenceSections")
                if isinstance(raw_sections, list):
                    evidence_sections = [
                        section for section in raw_sections if isinstance(section, dict)
                    ]
            receipt_payload = _build_change_receipt_v1(
                thread_id=thread_id,
                action_request_id=approved_request.id,
                tool_run_id=started_tool_run.id,
                tool_name=approved_request.tool_name,
                workflow_family=tool.workflow_family,
                terminal_phase="failed",
                reply_template=reply_payload,
                evidence_sections=evidence_sections,
                error_code=sanitized_error.error_code,
            )
            should_emit_receipt = True
            if session is not None:
                receipt_service = ActionReceiptService(
                    repository=SQLActionReceiptRepository(session)
                )
                should_emit_receipt = (
                    await receipt_service.create_action_receipt_if_missing(
                        action_request_id=approved_request.id,
                        tool_run_id=started_tool_run.id,
                        terminal_phase="failed",
                        payload=receipt_payload,
                    )
                )
            if should_emit_receipt:
                await _emit_workflow_receipt_messages(
                    repository=repository,
                    thread_id=thread_id,
                    action_request_id=approved_request.id,
                    payload=receipt_payload,
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
    assistant_text: str | None,
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
    if assistant_text is not None:
        await repository.create_message(
            thread_id=thread_id,
            role="assistant",
            parts=[{"type": "text", "text": assistant_text}],
        )
    await repository.create_audit_log(
        event_type="tool_failed",
        actor_email=owner_user_email,
        tool_name=approved_request.tool_name,
        metadata=audit_metadata,
    )
