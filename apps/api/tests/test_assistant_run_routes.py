from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_handling import install_error_handling
from noa_api.api.routes.assistant import (
    get_assistant_run_coordinator,
    get_assistant_run_live,
    get_assistant_service,
    router as assistant_router,
)
from noa_api.api.assistant.assistant_runs import AssistantRunCoordinator
from noa_api.core.auth.authorization import AuthorizationUser, get_authorization_service
from noa_api.storage.postgres.lifecycle import AssistantRunStatus

from test_assistant import (
    _FakeAssistantService,
    _FakeAuthorizationService,
    _assert_assistant_ack,
)


async def _next_chunk(chunks: AsyncIterator[bytes | str | memoryview]) -> object:
    return await anext(chunks)


def _build_run_routes_app(
    *,
    service: _FakeAssistantService,
    current_user: AuthorizationUser,
    coordinator: AssistantRunCoordinator,
) -> FastAPI:
    app = FastAPI()
    install_error_handling(app)
    app.include_router(assistant_router)
    app.dependency_overrides[get_assistant_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: current_user
    app.dependency_overrides[get_authorization_service] = lambda: (
        _FakeAuthorizationService()
    )
    app.dependency_overrides[get_assistant_run_coordinator] = lambda: coordinator
    return app


async def test_assistant_route_returns_ack_without_run_for_non_agent_commands() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=AssistantRunCoordinator(instance_id="test-api"),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [{"type": "deny-action", "actionRequestId": None}],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=None,
        has_active_run_id=False,
    )
    assert service.active_runs == {}


async def test_assistant_route_returns_ack_and_starts_detached_run_for_add_message() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    body = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    run_id = UUID(str(body["activeRunId"]))
    run = await service.get_run(run_id=run_id)
    assert run is not None
    assert run.owner_user_id == owner_id
    assert run.thread_id == thread_id
    assert coordinator.has_run(run_id=run_id) is True


async def test_assistant_route_returns_409_for_active_add_message_conflict() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    _ = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=AssistantRunCoordinator(instance_id="test-api"),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello again"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "Thread already has an active assistant run"


async def test_assistant_route_translates_create_run_integrity_conflict_to_409() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def _conflict_create_run(**_: object) -> None:
        raise IntegrityError(
            "insert into assistant_runs", {}, Exception("duplicate key")
        )

    service.create_run = _conflict_create_run  # type: ignore[method-assign]
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=AssistantRunCoordinator(instance_id="test-api"),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Start run"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "Thread already has an active assistant run"


async def test_assistant_route_reuses_waiting_approval_run_for_approve_action() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    existing_run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    await service.mark_run_waiting_approval(
        run_id=existing_run.id,
        action_request_id=uuid4(),
    )

    async def _unexpected_create_run(**_: object) -> None:
        raise AssertionError("create_run should not be called for resume")

    service.create_run = _unexpected_create_run  # type: ignore[method-assign]
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [{"type": "approve-action", "actionRequestId": "ar-1"}],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    body = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.RUNNING.value,
        has_active_run_id=True,
    )
    assert body["activeRunId"] == str(existing_run.id)
    assert coordinator.has_run(run_id=existing_run.id) is True


async def test_assistant_route_restarts_still_tracked_waiting_run_for_approve_action() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    existing_run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    await service.mark_run_waiting_approval(
        run_id=existing_run.id,
        action_request_id=uuid4(),
    )

    resumed = asyncio.Event()

    async def _resume_run_agent_turn(**kwargs: object):
        _ = kwargs
        resumed.set()
        return None

    async def _unexpected_create_run(**_: object) -> None:
        raise AssertionError("create_run should not be called for resume")

    service.run_agent_turn = _resume_run_agent_turn  # type: ignore[method-assign]
    service.create_run = _unexpected_create_run  # type: ignore[method-assign]

    coordinator = AssistantRunCoordinator(instance_id="test-api")

    pause_gate = asyncio.Event()

    async def _paused_job(handle):
        handle.publish_snapshot(snapshot={"activeRunId": str(existing_run.id)})
        await pause_gate.wait()
        return None

    coordinator.start_detached_run(run_id=existing_run.id, job_factory=_paused_job)
    await asyncio.sleep(0)

    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [{"type": "approve-action", "actionRequestId": "ar-1"}],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    body = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.RUNNING.value,
        has_active_run_id=True,
    )
    assert body["activeRunId"] == str(existing_run.id)
    await asyncio.wait_for(resumed.wait(), timeout=1)
    pause_gate.set()


async def test_assistant_route_keeps_waiting_approval_run_blocked_for_add_tool_result() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    existing_run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    await service.mark_run_waiting_approval(
        run_id=existing_run.id,
        action_request_id=uuid4(),
    )

    async def _unexpected_create_run(**_: object) -> None:
        raise AssertionError("create_run should not be called for resume")

    service.create_run = _unexpected_create_run  # type: ignore[method-assign]
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-tool-result",
                "toolCallId": str(uuid4()),
                "result": {"ok": True},
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    body = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.WAITING_APPROVAL.value,
        has_active_run_id=True,
    )
    assert body["activeRunId"] == str(existing_run.id)
    assert coordinator.has_run(run_id=existing_run.id) is False


async def test_assistant_route_restarts_existing_run_when_prior_tracker_finished() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    existing_run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    await service.mark_run_waiting_approval(
        run_id=existing_run.id,
        action_request_id=uuid4(),
    )

    resumed = asyncio.Event()

    async def _resume_run_agent_turn(**kwargs: object):
        _ = kwargs
        resumed.set()
        return None

    async def _unexpected_create_run(**_: object) -> None:
        raise AssertionError("create_run should not be called for resume")

    service.run_agent_turn = _resume_run_agent_turn  # type: ignore[method-assign]
    service.create_run = _unexpected_create_run  # type: ignore[method-assign]

    coordinator = AssistantRunCoordinator(instance_id="test-api")

    async def _stale_job(handle):
        handle.publish_snapshot(snapshot={"activeRunId": str(existing_run.id)})
        return None

    coordinator.start_detached_run(run_id=existing_run.id, job_factory=_stale_job)
    await coordinator.wait_for_run(run_id=existing_run.id, timeout=1)

    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [{"type": "approve-action", "actionRequestId": "ar-1"}],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    body = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.RUNNING.value,
        has_active_run_id=True,
    )
    assert body["activeRunId"] == str(existing_run.id)
    await asyncio.wait_for(resumed.wait(), timeout=1)


async def test_assistant_run_live_route_requires_owner_match() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=uuid4(),
            email="intruder@example.com",
            display_name="Intruder",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/assistant/runs/{run.id}/live")

    assert response.status_code == 404
    assert response.json()["detail"] == "Thread not found"


async def test_assistant_run_live_route_streams_coordinator_events_for_owner() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    coordinator = AssistantRunCoordinator(instance_id="test-api")

    start_gate = asyncio.Event()
    release_run = asyncio.Event()

    async def _job(handle):
        handle.publish_snapshot(
            snapshot={
                "messages": [],
                "workflow": [],
                "pendingApprovals": [],
                "actionRequests": [],
                "isRunning": True,
                "runStatus": "RUNNING",
                "activeRunId": str(run.id),
                "waitingForApproval": False,
                "lastErrorReason": None,
            }
        )
        handle.publish_snapshot(
            snapshot={
                "messages": [],
                "workflow": [],
                "pendingApprovals": [],
                "actionRequests": [],
                "isRunning": True,
                "runStatus": "RUNNING",
                "activeRunId": str(run.id),
                "waitingForApproval": False,
                "lastErrorReason": None,
                "marker": "latest",
            }
        )
        start_gate.set()
        await release_run.wait()
        return None

    coordinator.start_detached_run(run_id=run.id, job_factory=_job)
    await start_gate.wait()

    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    async def _cleanup_run() -> None:
        await asyncio.sleep(0.05)
        release_run.set()
        await coordinator.wait_for_run(run_id=run.id, timeout=1)
        coordinator.remove_run(run_id=run.id)

    cleanup_task = asyncio.create_task(_cleanup_run())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/assistant/runs/{run.id}/live")

    await cleanup_task
    response_text = response.text

    assert "event: snapshot" in response_text
    data_lines = [
        line[len("data: ") :]
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(data_lines) == 1
    event = json.loads(data_lines[0])
    assert event["type"] == "snapshot"
    assert event["sequence"] == 2
    assert event["snapshot"]["activeRunId"] == str(run.id)
    assert event["snapshot"]["marker"] == "latest"


async def test_assistant_run_live_route_completes_after_terminal_run_finishes() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    coordinator = AssistantRunCoordinator(instance_id="test-api")

    terminal_snapshot: dict[str, object] = {
        "messages": [],
        "workflow": [],
        "pendingApprovals": [],
        "actionRequests": [],
        "isRunning": False,
        "runStatus": AssistantRunStatus.COMPLETED.value,
        "activeRunId": str(run.id),
        "waitingForApproval": False,
        "lastErrorReason": None,
    }

    await service.append_run_snapshot(run_id=run.id, snapshot=terminal_snapshot)
    await service.mark_run_completed(run_id=run.id)

    async def _job(handle):
        handle.publish_snapshot(snapshot=terminal_snapshot)
        return None

    coordinator.start_detached_run(run_id=run.id, job_factory=_job)
    await coordinator.wait_for_run(run_id=run.id, timeout=1)

    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await asyncio.wait_for(
            client.get(f"/assistant/runs/{run.id}/live"),
            timeout=1,
        )

    data_lines = [
        line[len("data: ") :]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(data_lines) == 1
    event = json.loads(data_lines[0])
    assert event["type"] == "snapshot"
    assert event["snapshot"]["runStatus"] == AssistantRunStatus.COMPLETED.value
    assert event["snapshot"]["activeRunId"] == str(run.id)


async def test_assistant_run_live_route_completed_snapshot_even_if_task_finishes_later() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    allow_finish = asyncio.Event()

    terminal_snapshot: dict[str, object] = {
        "messages": [],
        "workflow": [],
        "pendingApprovals": [],
        "actionRequests": [],
        "isRunning": False,
        "runStatus": AssistantRunStatus.COMPLETED.value,
        "activeRunId": str(run.id),
        "waitingForApproval": False,
        "lastErrorReason": None,
    }

    async def _job(handle):
        handle.publish_snapshot(snapshot=terminal_snapshot)
        await allow_finish.wait()
        return None

    coordinator.start_detached_run(run_id=run.id, job_factory=_job)

    current_user = AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    response = await get_assistant_run_live(
        run_id=run.id,
        current_user=current_user,
        assistant_service=cast(Any, service),
        coordinator=coordinator,
    )
    chunks = aiter(response.body_iterator)

    first_chunk = await asyncio.wait_for(_next_chunk(chunks), timeout=1)
    assert isinstance(first_chunk, bytes)
    payload_lines = first_chunk.decode().splitlines()

    assert payload_lines[0] == "event: snapshot"
    assert payload_lines[2] == ""
    assert coordinator.has_run(run_id=run.id) is True

    pending_next_chunk = asyncio.create_task(_next_chunk(chunks))
    try:
        await asyncio.wait_for(asyncio.shield(pending_next_chunk), timeout=0.05)
        raise AssertionError("live stream should wait for the run task to finish")
    except TimeoutError:
        pass

    allow_finish.set()
    await coordinator.wait_for_run(run_id=run.id, timeout=1)

    try:
        await asyncio.wait_for(pending_next_chunk, timeout=1)
        raise AssertionError("live stream should close without emitting another event")
    except StopAsyncIteration:
        pass

    assert coordinator.has_run(run_id=run.id) is False

    event = json.loads(payload_lines[1][len("data: ") :])
    assert event["type"] == "snapshot"
    assert event["snapshot"]["runStatus"] == AssistantRunStatus.COMPLETED.value
    assert event["snapshot"]["activeRunId"] == str(run.id)


async def test_assistant_run_live_route_returns_waiting_snapshot_when_persisted_before_connect() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    action_request_id = uuid4()

    terminal_snapshot: dict[str, object] = {
        "messages": [],
        "workflow": [],
        "pendingApprovals": [
            {"actionRequestId": str(action_request_id), "status": "PENDING"}
        ],
        "actionRequests": [],
        "isRunning": False,
        "runStatus": AssistantRunStatus.WAITING_APPROVAL.value,
        "activeRunId": str(run.id),
        "waitingForApproval": True,
        "lastErrorReason": None,
    }

    await service.append_run_snapshot(run_id=run.id, snapshot=terminal_snapshot)
    await service.mark_run_waiting_approval(
        run_id=run.id,
        action_request_id=action_request_id,
    )

    async def _job(handle):
        handle.publish_snapshot(snapshot=terminal_snapshot)
        return None

    coordinator.start_detached_run(run_id=run.id, job_factory=_job)
    await coordinator.wait_for_run(run_id=run.id, timeout=1)

    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await asyncio.wait_for(
            client.get(f"/assistant/runs/{run.id}/live"),
            timeout=1,
        )

    data_lines = [
        line[len("data: ") :]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(data_lines) == 1
    event = json.loads(data_lines[0])
    assert event["type"] == "snapshot"
    assert event["snapshot"]["runStatus"] == AssistantRunStatus.WAITING_APPROVAL.value
    assert event["snapshot"]["waitingForApproval"] is True
    assert event["snapshot"]["activeRunId"] == str(run.id)
    assert coordinator.has_run(run_id=run.id) is False


async def test_assistant_run_live_route_closes_after_waiting_approval_transition() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    allow_pause = asyncio.Event()
    action_request_id = uuid4()

    async def _job(handle):
        handle.publish_snapshot(
            snapshot={
                "messages": [],
                "workflow": [],
                "pendingApprovals": [],
                "actionRequests": [],
                "isRunning": True,
                "runStatus": AssistantRunStatus.RUNNING.value,
                "activeRunId": str(run.id),
                "waitingForApproval": False,
                "lastErrorReason": None,
            }
        )
        await allow_pause.wait()
        terminal_snapshot = {
            "messages": [],
            "workflow": [],
            "pendingApprovals": [
                {"actionRequestId": str(action_request_id), "status": "PENDING"}
            ],
            "actionRequests": [],
            "isRunning": False,
            "runStatus": AssistantRunStatus.WAITING_APPROVAL.value,
            "activeRunId": str(run.id),
            "waitingForApproval": True,
            "lastErrorReason": None,
        }
        handle.publish_snapshot(snapshot=terminal_snapshot)
        await service.append_run_snapshot(run_id=run.id, snapshot=terminal_snapshot)
        await service.mark_run_waiting_approval(
            run_id=run.id,
            action_request_id=action_request_id,
        )
        return None

    coordinator.start_detached_run(run_id=run.id, job_factory=_job)

    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    async def _wait_for_subscription() -> None:
        for _ in range(100):
            subscribers = getattr(coordinator, "_subscribers", {})
            if subscribers.get(run.id):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("live route did not subscribe in time")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response_task = asyncio.create_task(
            client.get(f"/assistant/runs/{run.id}/live")
        )
        await _wait_for_subscription()
        allow_pause.set()
        response = await asyncio.wait_for(response_task, timeout=1)

    if coordinator.has_run(run_id=run.id):
        await coordinator.wait_for_run(run_id=run.id, timeout=1)
        coordinator.remove_run(run_id=run.id)

    data_lines = [
        line[len("data: ") :]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(data_lines) == 2
    running_event = json.loads(data_lines[0])
    waiting_event = json.loads(data_lines[1])
    assert running_event["snapshot"]["runStatus"] == AssistantRunStatus.RUNNING.value
    assert (
        waiting_event["snapshot"]["runStatus"]
        == AssistantRunStatus.WAITING_APPROVAL.value
    )
    assert waiting_event["snapshot"]["waitingForApproval"] is True
    assert coordinator.has_run(run_id=run.id) is False


async def test_assistant_route_deny_action_completes_waiting_run_and_unblocks_thread() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    existing_run = await service.create_run(
        owner_user_id=owner_id,
        thread_id=thread_id,
        owner_instance_id="test-api",
    )
    action_request_id = uuid4()
    await service.mark_run_waiting_approval(
        run_id=existing_run.id,
        action_request_id=action_request_id,
    )
    await service.append_run_snapshot(
        run_id=existing_run.id,
        snapshot={
            "messages": [],
            "workflow": [],
            "pendingApprovals": [
                {"actionRequestId": str(action_request_id), "status": "PENDING"}
            ],
            "actionRequests": [],
            "isRunning": False,
            "runStatus": AssistantRunStatus.WAITING_APPROVAL.value,
            "activeRunId": str(existing_run.id),
            "waitingForApproval": True,
            "lastErrorReason": None,
        },
    )

    coordinator = AssistantRunCoordinator(instance_id="test-api")
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    deny_payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {"type": "deny-action", "actionRequestId": str(action_request_id)}
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=deny_payload)

        body = _assert_assistant_ack(
            response,
            thread_id=thread_id,
            run_status=AssistantRunStatus.COMPLETED.value,
            has_active_run_id=True,
        )
        assert body["activeRunId"] == str(existing_run.id)

        follow_up_response = await client.post(
            "/assistant",
            json={
                "state": {"messages": [], "isRunning": False},
                "commands": [
                    {
                        "type": "add-message",
                        "message": {
                            "role": "user",
                            "parts": [{"type": "text", "text": "Continue"}],
                        },
                    }
                ],
                "threadId": str(thread_id),
            },
        )

    run = await service.get_run(run_id=existing_run.id)
    assert run is not None
    assert run.status == AssistantRunStatus.COMPLETED
    assert run.live_snapshot["runStatus"] == AssistantRunStatus.COMPLETED.value
    assert run.live_snapshot["waitingForApproval"] is False

    follow_up_body = _assert_assistant_ack(
        follow_up_response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    assert follow_up_body["activeRunId"] != str(existing_run.id)


async def test_assistant_route_persists_failed_status_for_refresh_failure() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)
    coordinator = AssistantRunCoordinator(instance_id="test-api")

    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    class _FailingAuthorizationService:
        async def authorize_tool_access(
            self, user: AuthorizationUser, tool_name: str
        ) -> bool:
            _ = user, tool_name
            return False

        async def get_allowed_tool_names(self, user: AuthorizationUser) -> set[str]:
            _ = user
            raise RuntimeError("allowed tools boom")

    app.dependency_overrides[get_authorization_service] = lambda: (
        _FailingAuthorizationService()
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    body = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    run_id = UUID(str(body["activeRunId"]))
    await coordinator.wait_for_run(run_id=run_id, timeout=1)

    run = await service.get_run(run_id=run_id)
    assert run is not None
    assert run.status == AssistantRunStatus.FAILED
    assert run.live_snapshot["runStatus"] == AssistantRunStatus.FAILED.value
    assert run.last_error_reason == "Assistant run failed. Please try again."


async def test_assistant_route_does_not_misclassify_success_from_old_failure_text() -> (
    None
):
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        messages=[
            {
                "id": str(uuid4()),
                "role": "assistant",
                "parts": [
                    {
                        "type": "text",
                        "text": "Assistant run failed. Please try again.",
                    }
                ],
            }
        ],
        runner_messages=[],
        runner_text_deltas=[],
    )
    coordinator = AssistantRunCoordinator(instance_id="test-api")
    app = _build_run_routes_app(
        service=service,
        current_user=AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
        coordinator=coordinator,
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Try again"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    body = _assert_assistant_ack(
        response,
        thread_id=thread_id,
        run_status=AssistantRunStatus.STARTING.value,
        has_active_run_id=True,
    )
    run_id = UUID(str(body["activeRunId"]))
    await coordinator.wait_for_run(run_id=run_id, timeout=1)

    run = await service.get_run(run_id=run_id)
    assert run is not None
    assert run.status == AssistantRunStatus.COMPLETED
    assert run.live_snapshot["runStatus"] == AssistantRunStatus.COMPLETED.value
    assert run.last_error_reason is None
