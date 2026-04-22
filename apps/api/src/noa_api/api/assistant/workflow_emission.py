from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from noa_api.api.assistant.assistant_tool_execution import build_tool_result_part
from noa_api.api.assistant.assistant_tool_result_operations import (
    AssistantMessageAuditRepositoryProtocol,
)
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
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
