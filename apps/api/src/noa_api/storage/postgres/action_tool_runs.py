from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import encrypt_text, maybe_decrypt_text
from noa_api.core.json_safety import json_safe
from noa_api.core.secrets.redaction import is_sensitive_key
from noa_api.core.tools.registry import get_tool_definition
from noa_api.core.tools.result_validation import validate_tool_result
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    ToolRisk,
    ToolRunStatus,
)
from noa_api.storage.postgres.models import ActionRequest, ToolRun


def _safe_dict(value: object) -> dict[str, object]:
    safe = json_safe(value)
    if isinstance(safe, dict):
        return safe
    return {"value": safe}


def _encrypt_sensitive_args(args: Mapping[str, object]) -> dict[str, object]:
    protected: dict[str, object] = {}
    for key, value in args.items():
        if isinstance(key, str) and is_sensitive_key(key):
            protected[key] = encrypt_text(value) if isinstance(value, str) else value
        elif isinstance(value, Mapping):
            protected[key] = _encrypt_sensitive_args(value)
        else:
            protected[key] = value
    return protected


def _decrypt_sensitive_args(args: Mapping[str, object]) -> dict[str, object]:
    decrypted: dict[str, object] = {}
    for key, value in args.items():
        if isinstance(key, str) and is_sensitive_key(key):
            decrypted[key] = (
                maybe_decrypt_text(value) if isinstance(value, str) else value
            )
        elif isinstance(value, Mapping):
            decrypted[key] = _decrypt_sensitive_args(value)
        else:
            decrypted[key] = value
    return decrypted


def decrypt_sensitive_args(args: Mapping[str, object]) -> dict[str, object]:
    return _decrypt_sensitive_args(args)


class ActionToolRunRepositoryProtocol(Protocol):
    async def get_action_request(
        self, *, action_request_id: UUID
    ) -> ActionRequest | None: ...

    async def create_action_request(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        risk: ToolRisk,
        requested_by_user_id: UUID,
    ) -> ActionRequest: ...

    async def decide_action_request(
        self,
        *,
        action_request_id: UUID,
        decided_by_user_id: UUID,
        status: ActionRequestStatus,
    ) -> ActionRequest | None: ...

    async def start_tool_run(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        action_request_id: UUID | None,
        requested_by_user_id: UUID | None,
    ) -> ToolRun: ...

    async def get_tool_run(self, *, tool_run_id: UUID) -> ToolRun | None: ...

    async def finish_tool_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result: dict[str, object] | None,
        error: str | None,
    ) -> ToolRun | None: ...


class SQLActionToolRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_action_request(
        self, *, action_request_id: UUID
    ) -> ActionRequest | None:
        result = await self._session.execute(
            select(ActionRequest).where(ActionRequest.id == action_request_id)
        )
        return result.scalar_one_or_none()

    async def create_action_request(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        risk: ToolRisk,
        requested_by_user_id: UUID,
    ) -> ActionRequest:
        action_request = ActionRequest(
            thread_id=thread_id,
            tool_name=tool_name,
            args=args,
            risk=risk,
            status=ActionRequestStatus.PENDING,
            requested_by_user_id=requested_by_user_id,
        )
        self._session.add(action_request)
        await self._session.flush()
        return action_request

    async def decide_action_request(
        self,
        *,
        action_request_id: UUID,
        decided_by_user_id: UUID,
        status: ActionRequestStatus,
    ) -> ActionRequest | None:
        action_request = await self.get_action_request(
            action_request_id=action_request_id
        )
        if action_request is None:
            return None
        if action_request.status != ActionRequestStatus.PENDING:
            raise ValueError("Action request has already been decided")

        action_request.status = status
        action_request.decided_by_user_id = decided_by_user_id
        action_request.decided_at = datetime.now(UTC)
        await self._session.flush()
        return action_request

    async def start_tool_run(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: dict[str, object],
        action_request_id: UUID | None,
        requested_by_user_id: UUID | None,
    ) -> ToolRun:
        tool_run = ToolRun(
            thread_id=thread_id,
            tool_name=tool_name,
            args=args,
            status=ToolRunStatus.STARTED,
            action_request_id=action_request_id,
            requested_by_user_id=requested_by_user_id,
        )
        self._session.add(tool_run)
        await self._session.flush()
        return tool_run

    async def get_tool_run(self, *, tool_run_id: UUID) -> ToolRun | None:
        result = await self._session.execute(
            select(ToolRun).where(ToolRun.id == tool_run_id)
        )
        return result.scalar_one_or_none()

    async def finish_tool_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result: dict[str, object] | None,
        error: str | None,
    ) -> ToolRun | None:
        tool_run = await self.get_tool_run(tool_run_id=tool_run_id)
        if tool_run is None:
            return None
        if tool_run.status in {ToolRunStatus.COMPLETED, ToolRunStatus.FAILED}:
            raise ValueError("Tool run is already terminal")

        tool_run.status = status
        tool_run.result = result
        tool_run.error = error
        tool_run.completed_at = datetime.now(UTC)
        await self._session.flush()
        return tool_run


class ActionToolRunService:
    def __init__(self, *, repository: ActionToolRunRepositoryProtocol) -> None:
        self._repository = repository

    async def create_action_request(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: Mapping[str, object],
        risk: ToolRisk,
        requested_by_user_id: UUID,
    ) -> ActionRequest:
        return await self._repository.create_action_request(
            thread_id=thread_id,
            tool_name=tool_name,
            args=_safe_dict(_encrypt_sensitive_args(args)),
            risk=risk,
            requested_by_user_id=requested_by_user_id,
        )

    async def get_action_request(
        self, *, action_request_id: UUID
    ) -> ActionRequest | None:
        return await self._repository.get_action_request(
            action_request_id=action_request_id
        )

    async def approve_action_request(
        self, *, action_request_id: UUID, decided_by_user_id: UUID
    ) -> ActionRequest | None:
        action_request = await self._repository.get_action_request(
            action_request_id=action_request_id
        )
        if action_request is None:
            return None
        if action_request.status != ActionRequestStatus.PENDING:
            raise ValueError("Action request has already been decided")
        return await self._repository.decide_action_request(
            action_request_id=action_request_id,
            decided_by_user_id=decided_by_user_id,
            status=ActionRequestStatus.APPROVED,
        )

    async def deny_action_request(
        self, *, action_request_id: UUID, decided_by_user_id: UUID
    ) -> ActionRequest | None:
        action_request = await self._repository.get_action_request(
            action_request_id=action_request_id
        )
        if action_request is None:
            return None
        if action_request.status != ActionRequestStatus.PENDING:
            raise ValueError("Action request has already been decided")
        return await self._repository.decide_action_request(
            action_request_id=action_request_id,
            decided_by_user_id=decided_by_user_id,
            status=ActionRequestStatus.DENIED,
        )

    async def start_tool_run(
        self,
        *,
        thread_id: UUID,
        tool_name: str,
        args: Mapping[str, object],
        action_request_id: UUID | None,
        requested_by_user_id: UUID | None,
    ) -> ToolRun:
        return await self._repository.start_tool_run(
            thread_id=thread_id,
            tool_name=tool_name,
            args=_safe_dict(_encrypt_sensitive_args(args)),
            action_request_id=action_request_id,
            requested_by_user_id=requested_by_user_id,
        )

    async def get_tool_run(self, *, tool_run_id: UUID) -> ToolRun | None:
        return await self._repository.get_tool_run(tool_run_id=tool_run_id)

    async def complete_tool_run(
        self, *, tool_run_id: UUID, result: Mapping[str, object]
    ) -> ToolRun | None:
        tool_run = await self._repository.get_tool_run(tool_run_id=tool_run_id)
        if tool_run is None:
            return None
        if tool_run.status in {ToolRunStatus.COMPLETED, ToolRunStatus.FAILED}:
            raise ValueError("Tool run is already terminal")
        safe_result = _safe_dict(result)
        tool = get_tool_definition(tool_run.tool_name)
        if tool is not None:
            validate_tool_result(tool=tool, result=safe_result)
        return await self._repository.finish_tool_run(
            tool_run_id=tool_run_id,
            status=ToolRunStatus.COMPLETED,
            result=safe_result,
            error=None,
        )

    async def fail_tool_run(self, *, tool_run_id: UUID, error: str) -> ToolRun | None:
        tool_run = await self._repository.get_tool_run(tool_run_id=tool_run_id)
        if tool_run is None:
            return None
        if tool_run.status in {ToolRunStatus.COMPLETED, ToolRunStatus.FAILED}:
            raise ValueError("Tool run is already terminal")
        return await self._repository.finish_tool_run(
            tool_run_id=tool_run_id,
            status=ToolRunStatus.FAILED,
            result=None,
            error=error,
        )
