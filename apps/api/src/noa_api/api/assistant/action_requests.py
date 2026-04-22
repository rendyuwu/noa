from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

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
from noa_api.api.assistant.assistant_tool_result_operations import (
    AssistantMessageAuditRepositoryProtocol,
)
from noa_api.api.assistant.workflow_emission import (
    ApprovedToolExecutor,
    _build_change_receipt_v1,
    _emit_update_workflow_todo_messages,
    _emit_workflow_receipt_messages,
    _has_recorded_change_reason,
)
from noa_api.core.logging_context import log_context
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.core.tools.registry import get_tool_definition
from noa_api.core.workflows.registry import (
    build_workflow_evidence_template,
    build_workflow_reply_template,
    build_workflow_todos,
    collect_recent_preflight_evidence,
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
from noa_api.storage.postgres.models import ActionRequest

logger = logging.getLogger(__name__)


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
