from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast
from uuid import uuid4

from assistant_stream import RunController

STREAMING_MESSAGE_ID = "assistant-streaming"


def coerce_messages(messages: object) -> list[object]:
    return list(messages) if isinstance(messages, list) else []


def make_streaming_placeholder(text: str) -> dict[str, object]:
    return {
        "id": STREAMING_MESSAGE_ID,
        "role": "assistant",
        "parts": [{"type": "text", "text": text}],
    }


def build_live_run_snapshot(
    *,
    canonical_messages: Sequence[object],
    streamed_text: str,
    workflow: Sequence[object],
    pending_approvals: Sequence[object],
    action_requests: Sequence[object],
    is_running: bool,
    run_status: str | None,
    active_run_id: str | None,
    waiting_for_approval: bool,
    last_error_reason: str | None,
) -> dict[str, object]:
    return {
        "messages": [
            *canonical_messages,
            make_streaming_placeholder(streamed_text),
        ],
        "workflow": list(workflow),
        "pendingApprovals": list(pending_approvals),
        "actionRequests": list(action_requests),
        "isRunning": is_running,
        "runStatus": run_status,
        "activeRunId": active_run_id,
        "waitingForApproval": waiting_for_approval,
        "lastErrorReason": last_error_reason,
    }


def make_fallback_error_message(text: str) -> dict[str, object]:
    return {
        "id": f"assistant-run-error-{uuid4()}",
        "role": "assistant",
        "parts": [{"type": "text", "text": text}],
    }


def remove_streaming_placeholder(messages: list[object]) -> list[object]:
    return [
        message
        for message in messages
        if not (
            isinstance(message, dict)
            and cast(dict[str, object], message).get("id") == STREAMING_MESSAGE_ID
        )
    ]


def append_fallback_error_message(messages: list[object], text: str) -> list[object]:
    safe_messages = remove_streaming_placeholder(messages)
    safe_messages.append(make_fallback_error_message(text))
    return safe_messages


async def flush_controller_state(controller: RunController) -> None:
    state_manager = getattr(controller, "_state_manager", None)
    if state_manager is not None:
        try:
            state_manager.flush()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
    await asyncio.sleep(0)


def controller_is_cancelled(controller: RunController) -> bool:
    value = getattr(controller, "is_cancelled", False)
    if callable(value):
        try:
            return bool(value())
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
    return bool(value)


async def _stream_assistant_text(
    controller: RunController, text_deltas: list[str]
) -> None:
    if not text_deltas:
        return
    if controller.state is None:
        controller.state = {"messages": []}

    base_messages = coerce_messages(controller.state.get("messages"))
    streaming_message = make_streaming_placeholder("")
    base_messages.append(streaming_message)
    controller.state["messages"] = base_messages

    for chunk in text_deltas:
        if controller_is_cancelled(controller):
            raise asyncio.CancelledError
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        parts = cast(list[dict[str, object]], streaming_message["parts"])
        cast_part = parts[0]
        cast_part["text"] = f"{cast_part['text']}{chunk}"
        controller.state["messages"] = base_messages
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
