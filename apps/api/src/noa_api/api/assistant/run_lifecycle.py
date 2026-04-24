from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from noa_api.api.assistant.assistant_commands import AssistantRequest
from noa_api.api.assistant.assistant_operations import execute_active_run
from noa_api.api.assistant.assistant_run_stream import (
    build_run_snapshot_event,
    encode_sse_event,
)
from noa_api.api.assistant.assistant_runs import AssistantRunCoordinator
from noa_api.api.assistant.service import AssistantService
from noa_api.core.agent.runner import AgentRunnerResult
from noa_api.core.auth.authorization import AuthorizationService, AuthorizationUser
from noa_api.core.config import get_app_settings
from noa_api.core.telemetry import get_telemetry_recorder
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.lifecycle import AssistantRunStatus

logger = logging.getLogger(__name__)


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
    return coordinator.get_task_done(run_id=run_id)


def _coordinator_task(
    *, coordinator: AssistantRunCoordinator, run_id: UUID
) -> asyncio.Task[object] | None:
    return coordinator.get_task(run_id=run_id)


def _coordinator_sequence(
    *, coordinator: AssistantRunCoordinator, run_id: UUID
) -> int | None:
    return coordinator.get_sequence(run_id=run_id)


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
    request: Any,
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
    request: Any,
    payload: AssistantRequest,
    current_user: AuthorizationUser,
    run_id: UUID,
    command_types: list[str],
    canonical_state: dict[str, object],
    coordinator: AssistantRunCoordinator,
    assistant_service: Any,
    authorization_service: Any,
    build_assistant_service_fn: Any,
    build_authorization_service_fn: Any,
) -> None:
    async def _job(handle: Any) -> object:
        if isinstance(assistant_service, AssistantService) and isinstance(
            authorization_service, AuthorizationService
        ):
            app_settings = get_app_settings(request.app)
            async with get_session_factory()() as session:
                service = build_assistant_service_fn(
                    session=session,
                    app_settings=app_settings,
                )
                authz = build_authorization_service_fn(session=session)
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
