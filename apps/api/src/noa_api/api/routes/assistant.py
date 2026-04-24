from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from noa_api.api.auth_dependencies import get_active_current_auth_user
from noa_api.api.error_codes import (
    THREAD_NOT_FOUND,
)
from noa_api.api.assistant.assistant_commands import (
    AssistantRequest,
    should_run_agent,
)
from noa_api.api.assistant.assistant_errors import (
    AssistantDomainError,
    assistant_http_error,
    to_assistant_http_error,
)
from noa_api.api.assistant.assistant_operations import (
    _record_assistant_failure_telemetry,
    _resume_waiting_run_state,
    prepare_assistant_transport,
)
from noa_api.api.assistant.assistant_run_stream import (
    build_run_snapshot_event,
    encode_sse_event,
)
from noa_api.api.assistant.assistant_runs import AssistantRunCoordinator
from noa_api.api.assistant.assistant_streaming import _stream_assistant_text
from noa_api.api.assistant.dependencies import (
    _build_assistant_service,
    _build_authorization_service,
    get_assistant_service,
)
from noa_api.api.assistant.run_lifecycle import (
    _canonical_active_run_id,
    _coordinator_task_done,
    _coordinator_sequence,
    _run_detached_assistant_turn,
    _should_resume_existing_run,
    _snapshot_is_terminal,
    _terminal_live_event,
    _wait_for_tracked_run_completion,
)
from noa_api.api.assistant.schemas import (
    AssistantRunAckResponse,
    AssistantThreadStateResponse,
)
from noa_api.api.assistant.service import AssistantService
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    get_authorization_service,
)
from noa_api.core.logging_context import log_context
from noa_api.core.request_context import get_request_id
from noa_api.core.telemetry import get_telemetry_recorder
from noa_api.storage.postgres.lifecycle import AssistantRunStatus

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


def get_assistant_run_coordinator() -> AssistantRunCoordinator:
    return _RUN_COORDINATOR


@router.get(
    "/assistant/threads/{thread_id}/state",
    response_model=AssistantThreadStateResponse,
)
async def get_thread_state(
    thread_id: UUID,
    current_user: AuthorizationUser = Depends(get_active_current_auth_user),
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
    current_user: AuthorizationUser = Depends(get_active_current_auth_user),
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
                build_assistant_service_fn=_build_assistant_service,
                build_authorization_service_fn=_build_authorization_service,
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
        build_assistant_service_fn=_build_assistant_service,
        build_authorization_service_fn=_build_authorization_service,
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
    current_user: AuthorizationUser = Depends(get_active_current_auth_user),
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
