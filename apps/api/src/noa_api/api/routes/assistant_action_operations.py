from __future__ import annotations

import logging
from uuid import UUID

from noa_api.api.routes.assistant_errors import (
    action_request_already_decided_error,
    action_request_not_found_error,
    parse_action_request_id,
)
from noa_api.api.routes.assistant_tool_result_operations import (
    AssistantMessageAuditRepositoryProtocol,
)
from noa_api.core.logging_context import log_context
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ActionRequestStatus
from noa_api.storage.postgres.models import ActionRequest

logger = logging.getLogger(__name__)


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
