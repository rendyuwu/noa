from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from noa_api.core.tools.registry import ToolDefinition
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.storage.postgres.action_tool_runs import ActionToolRunService
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    ToolRisk,
    ToolRunStatus,
)
from noa_api.storage.postgres.models import ActionRequest, ToolRun


async def test_lifecycle_enums_define_machine_stable_values() -> None:
    assert ToolRisk.READ.value == "READ"
    assert ToolRisk.CHANGE.value == "CHANGE"
    assert ActionRequestStatus.PENDING.value == "PENDING"
    assert ActionRequestStatus.APPROVED.value == "APPROVED"
    assert ActionRequestStatus.DENIED.value == "DENIED"
    assert ToolRunStatus.STARTED.value == "STARTED"
    assert ToolRunStatus.COMPLETED.value == "COMPLETED"
    assert ToolRunStatus.FAILED.value == "FAILED"


@dataclass
class _FakeActionToolRunRepository:
    action_requests: dict[UUID, ActionRequest]
    tool_runs: dict[UUID, ToolRun]

    async def get_action_request(
        self, *, action_request_id: UUID
    ) -> ActionRequest | None:
        return self.action_requests.get(action_request_id)

    async def create_action_request(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        risk: ToolRisk,
        requested_by_user_id: UUID,
    ) -> ActionRequest:
        created = ActionRequest(
            id=uuid4(),
            thread_id=thread_id,
            tool_name=tool_name,
            args=args,
            risk=risk,
            status=ActionRequestStatus.PENDING,
            requested_by_user_id=requested_by_user_id,
            decided_by_user_id=None,
            decided_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.action_requests[created.id] = created
        return created

    async def decide_action_request(
        self,
        *,
        action_request_id: UUID,
        decided_by_user_id: UUID,
        status: ActionRequestStatus,
    ) -> ActionRequest | None:
        existing = self.action_requests.get(action_request_id)
        if existing is None:
            return None
        existing.status = status
        existing.decided_by_user_id = decided_by_user_id
        existing.decided_at = datetime.now(UTC)
        existing.updated_at = datetime.now(UTC)
        return existing

    async def start_tool_run(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        action_request_id: UUID | None,
        requested_by_user_id: UUID | None,
    ) -> ToolRun:
        started = ToolRun(
            id=uuid4(),
            thread_id=thread_id,
            tool_name=tool_name,
            args=args,
            status=ToolRunStatus.STARTED,
            result=None,
            error=None,
            action_request_id=action_request_id,
            requested_by_user_id=requested_by_user_id,
            created_at=datetime.now(UTC),
            completed_at=None,
        )
        self.tool_runs[started.id] = started
        return started

    async def get_tool_run(self, *, tool_run_id: UUID) -> ToolRun | None:
        return self.tool_runs.get(tool_run_id)

    async def finish_tool_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result: dict[str, object] | None,
        error: str | None,
    ) -> ToolRun | None:
        existing = self.tool_runs.get(tool_run_id)
        if existing is None:
            return None
        existing.status = status
        existing.result = result
        existing.error = error
        existing.completed_at = datetime.now(UTC)
        return existing


async def test_action_tool_run_service_transitions_core_states() -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)
    thread_id = uuid4()
    actor_id = uuid4()

    request = await service.create_action_request(
        thread_id=thread_id,
        tool_name="fake_change_tool",
        args={"key": "k", "value": "v"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=actor_id,
    )
    assert request.status == ActionRequestStatus.PENDING
    assert request.risk == ToolRisk.CHANGE

    approved = await service.approve_action_request(
        action_request_id=request.id, decided_by_user_id=actor_id
    )
    assert approved is not None
    assert approved.status == ActionRequestStatus.APPROVED

    with pytest.raises(ValueError, match="already been decided"):
        await service.deny_action_request(
            action_request_id=request.id, decided_by_user_id=actor_id
        )

    run = await service.start_tool_run(
        thread_id=thread_id,
        tool_name="fake_change_tool",
        args={"key": "k", "value": "v"},
        action_request_id=request.id,
        requested_by_user_id=actor_id,
    )
    assert run.status == ToolRunStatus.STARTED

    completed = await service.complete_tool_run(
        tool_run_id=run.id,
        result={"ok": True, "flag": {"key": "k", "value": "v"}},
    )
    assert completed is not None
    assert completed.status == ToolRunStatus.COMPLETED
    assert completed.result == {"ok": True, "flag": {"key": "k", "value": "v"}}
    assert completed.error is None


async def test_action_tool_run_service_can_fail_tool_run() -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)
    thread_id = uuid4()

    run = await service.start_tool_run(
        thread_id=thread_id,
        tool_name="fake_change_tool",
        args={"key": "k", "value": "v"},
        action_request_id=None,
        requested_by_user_id=None,
    )

    failed = await service.fail_tool_run(tool_run_id=run.id, error="boom")

    assert failed is not None
    assert failed.status == ToolRunStatus.FAILED
    assert failed.result is None
    assert failed.error == "boom"


async def test_action_tool_run_service_rejects_re_deciding_action_request() -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)
    thread_id = uuid4()
    actor_id = uuid4()

    request = await service.create_action_request(
        thread_id=thread_id,
        tool_name="fake_change_tool",
        args={"key": "k", "value": "v"},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=actor_id,
    )
    _ = await service.approve_action_request(
        action_request_id=request.id, decided_by_user_id=actor_id
    )

    with pytest.raises(ValueError, match="already been decided"):
        await service.approve_action_request(
            action_request_id=request.id, decided_by_user_id=actor_id
        )

    with pytest.raises(ValueError, match="already been decided"):
        await service.deny_action_request(
            action_request_id=request.id, decided_by_user_id=actor_id
        )


async def test_action_tool_run_service_rejects_complete_then_failed_transition() -> (
    None
):
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)

    run = await service.start_tool_run(
        thread_id=uuid4(),
        tool_name="fake_change_tool",
        args={"key": "k", "value": "v"},
        action_request_id=None,
        requested_by_user_id=None,
    )
    _ = await service.complete_tool_run(
        tool_run_id=run.id,
        result={"ok": True, "flag": {"key": "k", "value": "v"}},
    )

    with pytest.raises(ValueError, match="already terminal"):
        await service.fail_tool_run(tool_run_id=run.id, error="should-not-transition")


async def test_action_tool_run_service_rejects_failed_then_completed_transition() -> (
    None
):
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)

    run = await service.start_tool_run(
        thread_id=uuid4(),
        tool_name="fake_change_tool",
        args={"key": "k", "value": "v"},
        action_request_id=None,
        requested_by_user_id=None,
    )
    _ = await service.fail_tool_run(tool_run_id=run.id, error="boom")

    with pytest.raises(ValueError, match="already terminal"):
        await service.complete_tool_run(tool_run_id=run.id, result={"ok": True})


async def test_action_tool_run_service_sanitizes_non_json_result_values() -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)

    run = await service.start_tool_run(
        thread_id=uuid4(),
        tool_name="test_tool",
        args={},
        action_request_id=None,
        requested_by_user_id=None,
    )

    raw = {
        "ok": True,
        "flag": {"completed_at": datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)},
    }
    completed = await service.complete_tool_run(tool_run_id=run.id, result=raw)

    assert completed is not None
    assert completed.result is not None
    assert completed.result["flag"]["completed_at"] == "2026-03-13T12:00:00+00:00"


async def test_action_tool_run_service_protects_sensitive_args_at_rest_and_supports_decryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noa_api.storage.postgres import action_tool_runs

    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)
    encrypted = "enc::new-secret"

    monkeypatch.setattr(
        action_tool_runs,
        "encrypt_text",
        lambda value: value if value.startswith("enc::") else f"enc::{value}",
    )
    monkeypatch.setattr(
        action_tool_runs,
        "maybe_decrypt_text",
        lambda value: (
            value.removeprefix("enc::") if value.startswith("enc::") else value
        ),
    )

    request = await service.create_action_request(
        thread_id=uuid4(),
        tool_name="proxmox_reset_vm_cloudinit_password",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "new_password": "new-secret",
            "reason": "Ticket #1661262",
        },
        risk=ToolRisk.CHANGE,
        requested_by_user_id=uuid4(),
    )

    assert request.args["new_password"] == encrypted
    assert redact_sensitive_data(request.args)["new_password"] == "[redacted]"

    run = await service.start_tool_run(
        thread_id=uuid4(),
        tool_name="proxmox_reset_vm_cloudinit_password",
        args=request.args,
        action_request_id=request.id,
        requested_by_user_id=uuid4(),
    )

    assert run.args["new_password"] == encrypted
    assert redact_sensitive_data(run.args)["new_password"] == "[redacted]"
    assert (
        action_tool_runs.decrypt_sensitive_args(run.args)["new_password"]
        == "new-secret"
    )


async def test_decrypt_sensitive_args_ignores_non_sensitive_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-sensitive strings are never passed through maybe_decrypt_text (V55)."""
    from noa_api.storage.postgres import action_tool_runs

    calls: list[str] = []

    def _tracking_maybe_decrypt(value: str) -> str:
        calls.append(value)
        return value.removeprefix("enc::") if value.startswith("enc::") else value

    monkeypatch.setattr(action_tool_runs, "maybe_decrypt_text", _tracking_maybe_decrypt)

    args = {
        "server_ref": "enc::looks-like-encrypted",
        "node": "pve1-node",
        "vmid": 101,
        "new_password": "enc::actual-secret",
        "reason": "Ticket #1661262",
    }

    result = action_tool_runs.decrypt_sensitive_args(args)

    # Non-sensitive keys preserved verbatim even if they look encrypted.
    assert result["server_ref"] == "enc::looks-like-encrypted"
    assert result["node"] == "pve1-node"
    assert result["reason"] == "Ticket #1661262"
    assert result["vmid"] == 101

    # Sensitive key was decrypted.
    assert result["new_password"] == "actual-secret"

    # Only sensitive key was passed through maybe_decrypt_text.
    assert calls == ["enc::actual-secret"]


async def test_action_tool_run_service_rejects_invalid_tool_result_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)

    async def _execute(**_kwargs: object) -> dict[str, object]:
        return {}

    fake_tool = ToolDefinition(
        name="fake_change_tool",
        description="fake tool for lifecycle validation",
        risk=ToolRisk.CHANGE,
        parameters_schema={"type": "object", "properties": {}, "required": []},
        execute=_execute,
        result_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "flag": {"type": "object"},
            },
            "required": ["flag"],
            "additionalProperties": False,
        },
    )
    monkeypatch.setattr(
        "noa_api.storage.postgres.action_tool_runs.get_tool_definition",
        lambda tool_name: fake_tool if tool_name == "fake_change_tool" else None,
    )

    run = await service.start_tool_run(
        thread_id=uuid4(),
        tool_name="fake_change_tool",
        args={"key": "k", "value": "v"},
        action_request_id=None,
        requested_by_user_id=None,
    )

    with pytest.raises(Exception) as exc_info:
        await service.complete_tool_run(tool_run_id=run.id, result={"ok": True})

    assert type(exc_info.value).__name__ == "ToolResultValidationError"
    assert getattr(exc_info.value, "error_code") == "invalid_tool_result"
    assert getattr(exc_info.value, "details") == ("Missing required field 'flag'",)
