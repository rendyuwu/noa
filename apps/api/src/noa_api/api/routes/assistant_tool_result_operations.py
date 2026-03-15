from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import UUID

from noa_api.api.routes.assistant_errors import (
    parse_tool_call_id,
    tool_call_not_awaiting_result_error,
    tool_call_not_found_error,
    unknown_tool_call_id_error,
)
from noa_api.api.routes.assistant_tool_execution import build_tool_result_part
from noa_api.core.logging_context import log_context
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import ToolRunStatus

logger = logging.getLogger(__name__)


class AssistantMessageAuditRepositoryProtocol(Protocol):
    async def create_message(
        self, *, thread_id: UUID, role: str, parts: list[dict[str, object]]
    ) -> object: ...

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None: ...


async def record_tool_result(
    *,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    tool_call_id: str | None,
    result: dict[str, Any],
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
) -> None:
    tool_run_id = parse_tool_call_id(tool_call_id)
    tool_run = await action_tool_run_service.get_tool_run(tool_run_id=tool_run_id)
    if tool_run is None:
        raise unknown_tool_call_id_error()
    if (
        tool_run.thread_id != thread_id
        or tool_run.requested_by_user_id != owner_user_id
    ):
        raise tool_call_not_found_error()
    if tool_run.status != ToolRunStatus.STARTED:
        raise tool_call_not_awaiting_result_error()

    completed = await action_tool_run_service.complete_tool_run(
        tool_run_id=tool_run_id, result=result
    )
    persisted_result = (
        completed.result
        if completed is not None and isinstance(completed.result, dict)
        else result
    )

    with log_context(
        thread_id=str(thread_id),
        tool_name=tool_run.tool_name,
        tool_run_id=str(tool_run.id),
        user_id=str(owner_user_id),
    ):
        await repository.create_message(
            thread_id=thread_id,
            role="tool",
            parts=[
                build_tool_result_part(
                    tool_name=tool_run.tool_name,
                    tool_call_id=str(tool_call_id),
                    result=persisted_result,
                    is_error=False,
                )
            ],
        )
        await repository.create_audit_log(
            event_type="tool_completed",
            actor_email=owner_user_email,
            tool_name=tool_run.tool_name,
            metadata={
                "thread_id": str(thread_id),
                "tool_run_id": str(tool_run.id),
                "source": "add-tool-result",
            },
        )
        logger.info(
            "assistant_tool_result_recorded",
            extra={
                "thread_id": str(thread_id),
                "tool_name": tool_run.tool_name,
                "tool_run_id": str(tool_run.id),
                "user_id": str(owner_user_id),
            },
        )
