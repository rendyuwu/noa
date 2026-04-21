from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_handling import install_error_handling
from noa_api.api.routes.assistant import (
    get_assistant_run_coordinator,
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
        run_status=AssistantRunStatus.WAITING_APPROVAL.value,
        has_active_run_id=True,
    )
    assert body["activeRunId"] == str(existing_run.id)
    assert coordinator.has_run(run_id=existing_run.id) is True


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
        run_status=AssistantRunStatus.WAITING_APPROVAL.value,
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
