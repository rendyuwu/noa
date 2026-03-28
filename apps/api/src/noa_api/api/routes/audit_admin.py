from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import (
    ACTION_RECEIPT_NOT_FOUND,
    ACTION_REQUEST_NOT_FOUND,
    ADMIN_ACCESS_REQUIRED,
    REQUEST_VALIDATION_ERROR,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import AuthorizationUser
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.lifecycle import ActionRequestStatus, ToolRisk
from noa_api.storage.postgres.models import ActionReceipt, ActionRequest, ToolRun, User

router = APIRouter(prefix="/admin/audit", tags=["audit"])

logger = logging.getLogger(__name__)


class AuditActionRequestListItem(BaseModel):
    action_request_id: str = Field(alias="actionRequestId")
    thread_id: str = Field(alias="threadId")
    tool_name: str = Field(alias="toolName")
    risk: ToolRisk
    status: ActionRequestStatus
    requested_by_email: str | None = Field(alias="requestedByEmail")
    decided_at: datetime | None = Field(alias="decidedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    tool_run_id: str | None = Field(alias="toolRunId")
    terminal_phase: str | None = Field(alias="terminalPhase")
    has_receipt: bool = Field(alias="hasReceipt")
    receipt_id: str | None = Field(alias="receiptId")

    model_config = {"populate_by_name": True}


class AuditActionRequestListResponse(BaseModel):
    items: list[AuditActionRequestListItem]
    next_cursor: str | None = Field(alias="nextCursor")

    model_config = {"populate_by_name": True}


class AuditToolRunSummary(BaseModel):
    id: str
    status: str
    created_at: datetime = Field(alias="createdAt")
    completed_at: datetime | None = Field(alias="completedAt")

    model_config = {"populate_by_name": True}


class AuditActionReceiptSummary(BaseModel):
    terminal_phase: str = Field(alias="terminalPhase")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class AuditActionRequestDetail(BaseModel):
    action_request_id: str = Field(alias="actionRequestId")
    thread_id: str = Field(alias="threadId")
    tool_name: str = Field(alias="toolName")
    risk: ToolRisk
    status: ActionRequestStatus
    args: dict[str, object]
    requested_by_email: str | None = Field(alias="requestedByEmail")
    decided_by_email: str | None = Field(alias="decidedByEmail")
    decided_at: datetime | None = Field(alias="decidedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    tool_runs: list[AuditToolRunSummary] = Field(alias="toolRuns")
    receipt: AuditActionReceiptSummary | None

    model_config = {"populate_by_name": True}


def _encode_cursor(*, created_at: datetime, action_request_id: UUID) -> str:
    payload = {
        "createdAt": created_at.isoformat(),
        "actionRequestId": str(action_request_id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw_cursor = cursor.strip()
    if not raw_cursor:
        raise ApiHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
            error_code=REQUEST_VALIDATION_ERROR,
        )

    padded = raw_cursor + "=" * (-len(raw_cursor) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        parsed = json.loads(decoded)
    except Exception as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
            error_code=REQUEST_VALIDATION_ERROR,
        ) from exc

    if not isinstance(parsed, dict):
        raise ApiHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
            error_code=REQUEST_VALIDATION_ERROR,
        )

    created_at_raw = parsed.get("createdAt")
    action_id_raw = parsed.get("actionRequestId")
    if not isinstance(created_at_raw, str) or not isinstance(action_id_raw, str):
        raise ApiHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
            error_code=REQUEST_VALIDATION_ERROR,
        )

    try:
        created_at = datetime.fromisoformat(created_at_raw)
        action_request_id = UUID(action_id_raw)
    except Exception as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
            error_code=REQUEST_VALIDATION_ERROR,
        ) from exc

    return created_at, action_request_id


async def _require_admin(
    request: Request,
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active or "admin" not in current_user.roles:
        logger.info(
            "audit_admin_access_denied",
            extra={
                "is_active": current_user.is_active,
                "roles": current_user.roles,
                "user_id": str(current_user.user_id),
            },
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
            error_code=ADMIN_ACCESS_REQUIRED,
        )
    return current_user


class SQLAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base_list_query(self) -> Select:
        # We intentionally avoid SQLAlchemy relationship properties for these models.
        return (
            select(
                ActionRequest,
                User.email.label("requested_by_email"),
                ActionReceipt.terminal_phase.label("terminal_phase"),
                ActionReceipt.tool_run_id.label("tool_run_id"),
                ActionReceipt.action_request_id.label("receipt_action_request_id"),
            )
            .join(User, User.id == ActionRequest.requested_by_user_id)
            .outerjoin(
                ActionReceipt,
                ActionReceipt.action_request_id == ActionRequest.id,
            )
        )

    async def list_action_requests(
        self,
        *,
        limit: int,
        cursor: str | None,
        tool_name: str | None,
        status_filter: ActionRequestStatus | None,
        terminal_phase: str | None,
        thread_id: UUID | None,
        requested_by_email: str | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
    ) -> tuple[list[AuditActionRequestListItem], str | None]:
        query = self._base_list_query()

        if tool_name:
            query = query.where(ActionRequest.tool_name == tool_name)
        if status_filter is not None:
            query = query.where(ActionRequest.status == status_filter)
        if terminal_phase:
            query = query.where(ActionReceipt.terminal_phase == terminal_phase)
        if thread_id is not None:
            query = query.where(ActionRequest.thread_id == thread_id)
        if requested_by_email:
            query = query.where(User.email.ilike(f"%{requested_by_email}%"))
        if from_dt is not None:
            query = query.where(ActionRequest.created_at >= from_dt)
        if to_dt is not None:
            query = query.where(ActionRequest.created_at <= to_dt)

        if cursor:
            cursor_created_at, cursor_action_id = _decode_cursor(cursor)
            query = query.where(
                or_(
                    ActionRequest.created_at < cursor_created_at,
                    and_(
                        ActionRequest.created_at == cursor_created_at,
                        ActionRequest.id < cursor_action_id,
                    ),
                )
            )

        query = query.order_by(
            desc(ActionRequest.created_at), desc(ActionRequest.id)
        ).limit(limit + 1)

        result = await self._session.execute(query)
        rows = list(result.all())

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        items: list[AuditActionRequestListItem] = []
        for (
            action_request,
            email,
            terminal_phase_value,
            tool_run_id,
            receipt_action_request_id,
        ) in rows:
            has_receipt = receipt_action_request_id is not None
            items.append(
                AuditActionRequestListItem(
                    actionRequestId=str(action_request.id),
                    threadId=str(action_request.thread_id),
                    toolName=action_request.tool_name,
                    risk=action_request.risk,
                    status=action_request.status,
                    requestedByEmail=email,
                    decidedAt=action_request.decided_at,
                    createdAt=action_request.created_at,
                    updatedAt=action_request.updated_at,
                    toolRunId=str(tool_run_id) if tool_run_id is not None else None,
                    terminalPhase=terminal_phase_value,
                    hasReceipt=has_receipt,
                    receiptId=(f"receipt-{action_request.id}" if has_receipt else None),
                )
            )

        next_cursor: str | None = None
        if has_more and items:
            # Use the underlying ordering columns for stability.
            last_created_at = rows[-1][0].created_at
            last_action_id = rows[-1][0].id
            next_cursor = _encode_cursor(
                created_at=last_created_at, action_request_id=last_action_id
            )
        return items, next_cursor

    async def get_action_request_detail(
        self, *, action_request_id: UUID
    ) -> AuditActionRequestDetail | None:
        action_request_result = await self._session.execute(
            select(ActionRequest, User.email)
            .join(User, User.id == ActionRequest.requested_by_user_id)
            .where(ActionRequest.id == action_request_id)
        )
        row = action_request_result.one_or_none()
        if row is None:
            return None
        action_request, requested_by_email = row

        decided_by_email: str | None = None
        if action_request.decided_by_user_id is not None:
            decided_by_result = await self._session.execute(
                select(User.email).where(User.id == action_request.decided_by_user_id)
            )
            decided_by_email = decided_by_result.scalar_one_or_none()

        tool_runs_result = await self._session.execute(
            select(ToolRun)
            .where(ToolRun.action_request_id == action_request_id)
            .order_by(ToolRun.created_at.asc(), ToolRun.id.asc())
        )
        tool_runs = list(tool_runs_result.scalars().all())
        tool_run_summaries = [
            AuditToolRunSummary(
                id=str(run.id),
                status=run.status.value,
                createdAt=run.created_at,
                completedAt=run.completed_at,
            )
            for run in tool_runs
        ]

        receipt_result = await self._session.execute(
            select(ActionReceipt).where(
                ActionReceipt.action_request_id == action_request_id
            )
        )
        receipt = receipt_result.scalar_one_or_none()
        receipt_summary = (
            AuditActionReceiptSummary(
                terminalPhase=receipt.terminal_phase,
                createdAt=receipt.created_at,
            )
            if receipt is not None
            else None
        )

        return AuditActionRequestDetail(
            actionRequestId=str(action_request.id),
            threadId=str(action_request.thread_id),
            toolName=action_request.tool_name,
            risk=action_request.risk,
            status=action_request.status,
            args=redact_sensitive_data(action_request.args),
            requestedByEmail=requested_by_email,
            decidedByEmail=decided_by_email,
            decidedAt=action_request.decided_at,
            createdAt=action_request.created_at,
            updatedAt=action_request.updated_at,
            toolRuns=tool_run_summaries,
            receipt=receipt_summary,
        )

    async def get_receipt_payload(
        self, *, action_request_id: UUID
    ) -> dict[str, object] | None:
        result = await self._session.execute(
            select(ActionReceipt.payload).where(
                ActionReceipt.action_request_id == action_request_id
            )
        )
        payload = result.scalar_one_or_none()
        if payload is None:
            return None
        if isinstance(payload, dict):
            redacted = redact_sensitive_data(payload)
            return redacted if isinstance(redacted, dict) else {"value": redacted}
        redacted = redact_sensitive_data(payload)
        return {"value": redacted}


class AuditService:
    def __init__(self, *, repository: SQLAuditRepository) -> None:
        self._repository = repository

    async def list_action_requests(
        self,
        *,
        limit: int,
        cursor: str | None,
        tool_name: str | None,
        status_filter: ActionRequestStatus | None,
        terminal_phase: str | None,
        thread_id: UUID | None,
        requested_by_email: str | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
    ) -> tuple[list[AuditActionRequestListItem], str | None]:
        return await self._repository.list_action_requests(
            limit=limit,
            cursor=cursor,
            tool_name=tool_name,
            status_filter=status_filter,
            terminal_phase=terminal_phase,
            thread_id=thread_id,
            requested_by_email=requested_by_email,
            from_dt=from_dt,
            to_dt=to_dt,
        )

    async def get_action_request_detail(
        self, *, action_request_id: UUID
    ) -> AuditActionRequestDetail | None:
        return await self._repository.get_action_request_detail(
            action_request_id=action_request_id
        )

    async def get_receipt_payload(
        self, *, action_request_id: UUID
    ) -> dict[str, object] | None:
        return await self._repository.get_receipt_payload(
            action_request_id=action_request_id
        )


async def get_audit_service() -> AsyncGenerator[AuditService, None]:
    async with get_session_factory()() as session:
        service = AuditService(repository=SQLAuditRepository(session))
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@router.get("/action-requests", response_model=AuditActionRequestListResponse)
async def list_action_requests(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    tool_name: str | None = Query(default=None, alias="toolName"),
    status_filter: ActionRequestStatus | None = Query(default=None, alias="status"),
    terminal_phase: str | None = Query(default=None, alias="terminalPhase"),
    thread_id: UUID | None = Query(default=None, alias="threadId"),
    requested_by_email: str | None = Query(default=None, alias="requestedByEmail"),
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    admin_user: AuthorizationUser = Depends(_require_admin),
    audit_service: AuditService = Depends(get_audit_service),
) -> AuditActionRequestListResponse:
    del request
    del admin_user
    items, next_cursor = await audit_service.list_action_requests(
        limit=limit,
        cursor=cursor,
        tool_name=tool_name,
        status_filter=status_filter,
        terminal_phase=terminal_phase,
        thread_id=thread_id,
        requested_by_email=requested_by_email,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    return AuditActionRequestListResponse(items=items, nextCursor=next_cursor)


@router.get(
    "/action-requests/{action_request_id}", response_model=AuditActionRequestDetail
)
async def get_action_request_detail(
    request: Request,
    action_request_id: UUID,
    admin_user: AuthorizationUser = Depends(_require_admin),
    audit_service: AuditService = Depends(get_audit_service),
) -> AuditActionRequestDetail:
    del request
    del admin_user
    detail = await audit_service.get_action_request_detail(
        action_request_id=action_request_id
    )
    if detail is None:
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action request not found",
            error_code=ACTION_REQUEST_NOT_FOUND,
        )
    return detail


@router.get("/action-requests/{action_request_id}/receipt")
async def get_action_request_receipt_payload(
    request: Request,
    action_request_id: UUID,
    admin_user: AuthorizationUser = Depends(_require_admin),
    audit_service: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    del request
    del admin_user
    payload = await audit_service.get_receipt_payload(
        action_request_id=action_request_id
    )
    if payload is None:
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receipt not found",
            error_code=ACTION_RECEIPT_NOT_FOUND,
        )
    return payload
