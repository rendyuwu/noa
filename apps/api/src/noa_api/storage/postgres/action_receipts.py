from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.json_safety import json_safe
from noa_api.storage.postgres.models import ActionReceipt


class ActionReceiptRepositoryProtocol(Protocol):
    async def get_by_action_request_id(
        self, *, action_request_id: UUID
    ) -> ActionReceipt | None: ...

    async def create_action_receipt_if_missing(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        schema_version: int,
        terminal_phase: str,
        payload: dict[str, object],
    ) -> bool: ...


class SQLActionReceiptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_action_request_id(
        self, *, action_request_id: UUID
    ) -> ActionReceipt | None:
        result = await self._session.execute(
            select(ActionReceipt).where(
                ActionReceipt.action_request_id == action_request_id
            )
        )
        return result.scalar_one_or_none()

    async def create_action_receipt_if_missing(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        schema_version: int,
        terminal_phase: str,
        payload: dict[str, object],
    ) -> bool:
        safe_payload = json_safe(payload)
        payload_obj = (
            safe_payload if isinstance(safe_payload, dict) else {"value": safe_payload}
        )

        stmt = (
            insert(ActionReceipt)
            .values(
                action_request_id=action_request_id,
                tool_run_id=tool_run_id,
                schema_version=schema_version,
                terminal_phase=terminal_phase,
                payload=payload_obj,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["action_request_id"])
            .returning(ActionReceipt.action_request_id)
        )
        result = await self._session.execute(stmt)
        created = result.scalar_one_or_none() is not None
        if created:
            await self._session.flush()
        return created


class ActionReceiptService:
    def __init__(self, *, repository: ActionReceiptRepositoryProtocol) -> None:
        self._repository = repository

    async def create_action_receipt_if_missing(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        terminal_phase: str,
        payload: dict[str, object],
        schema_version: int = 1,
    ) -> bool:
        return await self._repository.create_action_receipt_if_missing(
            action_request_id=action_request_id,
            tool_run_id=tool_run_id,
            schema_version=schema_version,
            terminal_phase=terminal_phase,
            payload=payload,
        )
