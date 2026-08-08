"""Deciding a pending CHANGE request (T37 — V15, V27, V28, V29, V32).

T33 opens the question and T34's row *is* the authorization. This is the only thing that
answers it, and it is deliberately a different module from `core.approvals.repository`.

**Why not one repository.** `SQLActionRequestRepository` writes `PENDING` and exposes no way
to write anything else, because it is reached from the MCP tool path — the path an LLM can
reach. A single class that could write `APPROVED` would be a second door on the
authorization, on the side V22 exists to close. So the decision writer lives here, nothing
puts it on `McpToolContext`, and a test asserts that absence rather than trusting it.

**The lock is the invariant, not a precaution.** V28 permits exactly one
`pending → decided` transition. `lock_for_decision` is `SELECT … FOR UPDATE`, and every
guard — requester-match, status, deadline — is evaluated *after* it, inside the same
transaction. `noa-old` read the row first, checked its status, and only then took the lock
(`MCP:apps/api/src/noa_api/storage/postgres/action_tool_runs.py:256-270`), which put the
check outside the thing that makes it hold: two concurrent approvals both read `PENDING`,
both proceed, and the second overwrites the first's decision.

**One transaction covers the decision and the run it starts.** An approval writes a
`tool_runs` row (V46) and links it from `action_requests.tool_run_id`, and both land in the
same commit — `start_change_run` is on this repository rather than taken as a caller-supplied
collaborator precisely so "same session" is a fact of construction and not an obligation a
caller can get wrong. What that buys: `APPROVED` with no run, and a run with no approval,
are both unrepresentable.

**The executor is handed the run after the commit** (V29: the state lives in the database,
not in a connection). A process that dies in that gap leaves a committed `APPROVED` row and
a `STARTED` run, which is exactly the pair T38's reaper is specified to sweep — whereas
handing off first could start a change whose authorization then rolled back.

`ApprovedChangeExecutor` is the seam T38 fills. Today the only implementation is
`DeferredApprovedChangeExecutor`, which records and does nothing: T22-T29 are unbuilt, so no
CHANGE tool exists to open a request, and nothing can reach these endpoints in production
yet. That is what makes the placeholder inert rather than a silent hole — and why the
correctness of everything above it is carried by tests instead of by use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol, runtime_checkable
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import as_utc, now_utc
from core.approvals.context import CONTEXT_ARGUMENTS_KEY, arguments_from_context
from core.approvals.errors import (
    ActionRequestAlreadyDecidedError,
    ActionRequestExpiredError,
    ActionRequestNotFoundError,
    ChangeReasonRequiredError,
)
from core.audit.tool_runs import SQLToolRunRepository
from core.db.lifecycle import ActionRequestStatus, ToolRisk
from core.db.models import ActionRequest

# One structured event per terminal transition. Identifiers only — the reason is the
# operator's own words about a production change and belongs in the row that authorises it,
# not duplicated into a log store (V8's spirit, one field over).
LOG_REQUEST_APPROVED: Final = "action_request_approved"
LOG_REQUEST_DENIED: Final = "action_request_denied"
LOG_REQUEST_EXPIRED_ON_READ: Final = "action_request_expired_on_read"

# The handoff failed *after* the decision was committed. Not an error for the operator (the
# approval stands and is durable); it is the reaper's problem now.
LOG_EXECUTION_HANDOFF_FAILED: Final = "approved_change_execution_handoff_failed"

# The placeholder executor ran. Every occurrence is a run that will sit STARTED until T38.
LOG_EXECUTION_DEFERRED: Final = "approved_change_execution_deferred"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LockedActionRequest:
    """A pending request, read under `FOR UPDATE` (V28).

    A value object rather than the ORM row, for the reason `support.tool_runs` gives one
    layer down: the service is what decides, and handing it a live ORM instance would let it
    write columns this design does not permit it to write. It carries exactly the fields the
    decision needs — who may decide (V27), whether it still may be decided (V28, V32), and
    what an approved run has to record (V46, V47).
    """

    action_request_id: UUID
    requested_by_user_id: UUID | None
    status: ActionRequestStatus
    tool_name: str
    conversation_ref: str | None
    approval_context: dict[str, Any]
    expires_at: datetime

    @property
    def redacted_arguments(self) -> dict[str, Any]:
        """The tool arguments as the gate redacted them at request time (V8, V33).

        `core.approvals.context` owns the key and the extraction rule, because T63 reads the
        same payload for `noa_get_action_result` and two readers of one JSONB column with two
        spellings of its key is one spelling too many (V66).
        """
        return arguments_from_context(self.approval_context)


@dataclass(frozen=True)
class ApprovalOutcome:
    """An approval that committed, and the run it started (V29)."""

    action_request_id: UUID
    tool_run_id: UUID


@dataclass(frozen=True)
class DenialOutcome:
    """A denial that committed. No run, and nowhere to put one."""

    action_request_id: UUID


@runtime_checkable
class ApprovedChangeExecutor(Protocol):
    """Starts the execution an approval authorised (T38's seam, V29, V30).

    `runtime_checkable` so `noa_api.api.deps` can type-check the instance it reads off
    `app.state` the way it does every other long-lived object.
    """

    async def start(self, *, tool_run_id: UUID, action_request_id: UUID) -> None: ...


class DeferredApprovedChangeExecutor:
    """Records the handoff and starts nothing — T38's placeholder.

    Deliberately not a silent no-op: every call is a `tool_runs` row that will sit `STARTED`
    until T38's executor and reaper land, and the log line is what makes that visible rather
    than something an operator discovers by waiting.

    Inert today: T22-T29 are unbuilt, so nothing can open an `action_requests` row through a
    real CHANGE tool, so nothing can reach an approval endpoint in production.
    """

    async def start(self, *, tool_run_id: UUID, action_request_id: UUID) -> None:
        logger.warning(
            LOG_EXECUTION_DEFERRED,
            tool_run_id=str(tool_run_id),
            action_request_id=str(action_request_id),
        )


class ActionDecisionRepository(Protocol):
    """What a decision needs: a lock, a run, a write, and a commit (V28, V29)."""

    async def lock_for_decision(self, *, action_request_id: UUID) -> LockedActionRequest | None: ...

    async def start_change_run(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        conversation_ref: str | None,
        args: dict[str, Any],
    ) -> UUID: ...

    async def write_decision(
        self,
        *,
        action_request_id: UUID,
        status: ActionRequestStatus,
        reason: str | None,
        decided_at: datetime,
        tool_run_id: UUID | None,
    ) -> None: ...

    async def commit(self) -> None: ...


class SQLActionDecisionRepository:
    """`ActionDecisionRepository` over one `AsyncSession`.

    Unlike `SQLActionRequestRepository`, this one runs inside FastAPI's dependency graph and
    joins the request's session (`noa_api.api.deps.get_db_session`), so a handler that raises
    rolls the decision back with it. `commit()` is still explicit and still the service's
    call, because the moment the decision becomes durable is the moment it becomes true.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_for_decision(self, *, action_request_id: UUID) -> LockedActionRequest | None:
        """Take the row lock and read the row through it (V28).

        `populate_existing=True` because the session may already hold this row from an
        earlier read in the same request: without it SQLAlchemy would hand back the
        identity-map copy and the locked read would be a lock with stale data behind it.

        Returns a detached value object. The `UPDATE` in `write_decision` runs in this same
        transaction, so the lock is held across both.
        """
        result = await self._session.execute(
            select(ActionRequest)
            .where(ActionRequest.id == action_request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        return LockedActionRequest(
            action_request_id=row.id,
            requested_by_user_id=row.requested_by_user_id,
            status=ActionRequestStatus(row.status),
            tool_name=row.tool_name,
            conversation_ref=row.conversation_ref,
            approval_context=dict(row.approval_context or {}),
            expires_at=as_utc(row.expires_at),
        )

    async def start_change_run(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        conversation_ref: str | None,
        args: dict[str, Any],
    ) -> UUID:
        """Insert the `STARTED` run this approval authorises (V46, V47).

        `SQLToolRunRepository` reused rather than a second insert written here (V66) — the
        audit trail has one writer per table — and constructed on *this* session, which is
        what makes the run and the decision one transaction.

        `risk` is fixed to `CHANGE` rather than taken as a parameter: V16 means every row in
        `action_requests` is a change by construction, and a parameter would be somewhere for
        a caller to write `READ` into an approved change's audit row.
        """
        return await SQLToolRunRepository(self._session).start_run(
            tool_name=tool_name,
            requested_by_user_id=requested_by_user_id,
            risk=ToolRisk.CHANGE,
            conversation_ref=conversation_ref,
            args=args,
        )

    async def write_decision(
        self,
        *,
        action_request_id: UUID,
        status: ActionRequestStatus,
        reason: str | None,
        decided_at: datetime,
        tool_run_id: UUID | None,
    ) -> None:
        """Move the locked row to its terminal state (V28).

        A bare `UPDATE` rather than load-mutate-save, matching `SQLToolRunRepository`:
        `lock_for_decision` already read the row moments ago in this transaction and holds
        its lock, so reading it back only to write it again is a round trip that can fail on
        its own.

        No `WHERE status = 'PENDING'` guard. The lock is the serialization, and adding a
        second mechanism would leave two things to keep in step — and would turn a genuine
        bug (deciding an already-decided row) into a silent zero-row update.
        """
        await self._session.execute(
            update(ActionRequest)
            .where(ActionRequest.id == action_request_id)
            .values(
                status=status,
                reason=reason,
                decided_at=decided_at,
                tool_run_id=tool_run_id,
            )
        )

    async def commit(self) -> None:
        """Make the decision, and any run it started, durable together."""
        await self._session.commit()


class ActionDecisionService:
    """Approve or deny one pending request (T37 — V15, V27, V28, V29, V32).

    The ordering lives here, in one place, rather than in the two routes: every guard runs
    inside the lock, and a caller cannot separate taking the lock from writing the answer.
    """

    def __init__(
        self,
        *,
        repository: ActionDecisionRepository,
        executor: ApprovedChangeExecutor,
    ) -> None:
        self._repository = repository
        self._executor = executor

    async def approve(
        self,
        *,
        action_request_id: UUID,
        caller_user_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> ApprovalOutcome:
        """Authorise the change, start its run, hand it off (V15, V28, V29, V46)."""
        decided_at = now_utc(now)
        locked = await self._locked_pending(
            action_request_id=action_request_id,
            caller_user_id=caller_user_id,
            reason=reason,
            decided_at=decided_at,
        )
        tool_run_id = await self._repository.start_change_run(
            tool_name=locked.tool_name,
            # The caller, not `locked.requested_by_user_id` — they are the same value, and
            # `_locked_pending` 404s unless they are (V27). Written this way because it is
            # also the narrower type, and because it says the thing T34 says by *dropping*
            # `decided_by_user_id`: the decider is the requester, one identity, not two.
            requested_by_user_id=caller_user_id,
            conversation_ref=locked.conversation_ref,
            args=locked.redacted_arguments,
        )
        await self._repository.write_decision(
            action_request_id=locked.action_request_id,
            status=ActionRequestStatus.APPROVED,
            reason=reason.strip(),
            decided_at=decided_at,
            tool_run_id=tool_run_id,
        )
        await self._repository.commit()

        logger.info(
            LOG_REQUEST_APPROVED,
            action_request_id=str(locked.action_request_id),
            tool=locked.tool_name,
            tool_run_id=str(tool_run_id),
            decided_by_user_id=str(caller_user_id),
        )
        await self._hand_off(tool_run_id=tool_run_id, action_request_id=locked.action_request_id)
        return ApprovalOutcome(
            action_request_id=locked.action_request_id,
            tool_run_id=tool_run_id,
        )

    async def deny(
        self,
        *,
        action_request_id: UUID,
        caller_user_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> DenialOutcome:
        """Refuse the change (V15, V28).

        `tool_run_id` stays NULL and there is no branch here that could set it: a denied
        change did not run, and an audit row saying otherwise would be worse than none.
        """
        decided_at = now_utc(now)
        locked = await self._locked_pending(
            action_request_id=action_request_id,
            caller_user_id=caller_user_id,
            reason=reason,
            decided_at=decided_at,
        )

        await self._repository.write_decision(
            action_request_id=locked.action_request_id,
            status=ActionRequestStatus.DENIED,
            reason=reason.strip(),
            decided_at=decided_at,
            tool_run_id=None,
        )
        await self._repository.commit()

        logger.info(
            LOG_REQUEST_DENIED,
            action_request_id=str(locked.action_request_id),
            tool=locked.tool_name,
            decided_by_user_id=str(caller_user_id),
        )
        return DenialOutcome(action_request_id=locked.action_request_id)

    async def _locked_pending(
        self,
        *,
        action_request_id: UUID,
        caller_user_id: UUID,
        reason: str,
        decided_at: datetime,
    ) -> LockedActionRequest:
        """Every guard a decision must pass, in the order it must pass them.

        1. **Reason first**, before any I/O (V15). A decision with nothing in the box is
           refused whatever the row says, and refusing before the lock means a blank submit
           does not queue behind someone else's transaction.
        2. **Lock, then read** (V28). Everything below is evaluated through the lock.
        3. **Absent or not the caller's → 404** (V27), one refusal for both, so the response
           is not an oracle for which requests exist. A NULL requester (the FK is `SET NULL`,
           T34) matches nobody and lands here too, which is the fail-closed direction.
        4. **Not PENDING → 409** (V28). The one permitted transition already happened.
        5. **Past its deadline → terminal EXPIRED, then 409** (V32). This is V32's
           check-on-read, and it *writes*: refusing without the write would leave a row that
           still reads PENDING, so the next reader would have to make the same discovery
           again. The write happens under the lock already held, and the reason stays NULL —
           an expiry is the absence of an answer, not one.
        """
        if not reason.strip():
            raise ChangeReasonRequiredError(
                f"decision on `{action_request_id}` carried a blank reason (C8, V15)"
            )

        locked = await self._repository.lock_for_decision(action_request_id=action_request_id)
        if locked is None or locked.requested_by_user_id != caller_user_id:
            raise ActionRequestNotFoundError(
                f"`{action_request_id}` is absent or not requested by `{caller_user_id}` (V27)"
            )

        if locked.status is not ActionRequestStatus.PENDING:
            raise ActionRequestAlreadyDecidedError(
                f"`{action_request_id}` is already `{locked.status.value}` (V28)"
            )

        if locked.expires_at <= decided_at:
            await self._expire(locked, decided_at=decided_at)
            raise ActionRequestExpiredError(
                f"`{action_request_id}` expired at {locked.expires_at.isoformat()} (V32)"
            )

        return locked

    async def _expire(self, locked: LockedActionRequest, *, decided_at: datetime) -> None:
        """Make a stale PENDING terminal, under the lock already held (V32)."""
        await self._repository.write_decision(
            action_request_id=locked.action_request_id,
            status=ActionRequestStatus.EXPIRED,
            reason=None,
            decided_at=decided_at,
            tool_run_id=None,
        )
        await self._repository.commit()
        logger.info(
            LOG_REQUEST_EXPIRED_ON_READ,
            action_request_id=str(locked.action_request_id),
            tool=locked.tool_name,
            expires_at=locked.expires_at.isoformat(),
        )

    async def _hand_off(self, *, tool_run_id: UUID, action_request_id: UUID) -> None:
        """Start the execution, and never fail the approval over it (V29, V30).

        The decision is committed by the time this runs. Raising here would answer 500 for a
        change that *is* approved and recorded, and the operator's only move would be to
        click again — which lands on V28's 409 and tells them nothing. So a handoff failure
        is logged and swallowed: the row is `APPROVED`, the run is `STARTED`, and T38's
        reaper is specified for exactly that pair.
        """
        try:
            await self._executor.start(
                tool_run_id=tool_run_id,
                action_request_id=action_request_id,
            )
        except Exception as exc:
            logger.error(
                LOG_EXECUTION_HANDOFF_FAILED,
                tool_run_id=str(tool_run_id),
                action_request_id=str(action_request_id),
                cause=type(exc).__name__,
                detail=str(exc),
            )


__all__ = [
    "CONTEXT_ARGUMENTS_KEY",
    "LOG_EXECUTION_DEFERRED",
    "LOG_EXECUTION_HANDOFF_FAILED",
    "LOG_REQUEST_APPROVED",
    "LOG_REQUEST_DENIED",
    "LOG_REQUEST_EXPIRED_ON_READ",
    "ActionDecisionRepository",
    "ActionDecisionService",
    "ApprovalOutcome",
    "ApprovedChangeExecutor",
    "DeferredApprovedChangeExecutor",
    "DenialOutcome",
    "LockedActionRequest",
    "SQLActionDecisionRepository",
]
