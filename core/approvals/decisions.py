"""Deciding a pending CHANGE request.

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
`tool_runs` row and links it from `action_requests.tool_run_id`, and both land in the
same commit — `start_change_run` is on this repository rather than taken as a caller-supplied
collaborator precisely so "same session" is a fact of construction and not an obligation a
caller can get wrong. What that buys: `APPROVED` with no run, and a run with no approval,
are both unrepresentable.

**The executor is handed the run after the commit** (V29: the state lives in the database,
not in a connection). A process that dies in that gap leaves a committed `APPROVED` row and
a `STARTED` run, which is exactly the pair T38's reaper is specified to sweep — whereas
handing off first could start a change whose authorization then rolled back.

`ApprovedChangeExecutor` is the seam T38 filled: `AsyncioApprovedChangeExecutor`
(`core.approvals.execution_host`) schedules an in-process task per approved change. The
`DeferredApprovedChangeExecutor` placeholder this module carried between T37 and T38 is gone —
production no longer wires it, and a placeholder nothing uses is dead code that reads as a
supported mode.

**V31's cap lives here too, and it is the reason this file grew a second lock.** An approval
starts a change, so the bound on how many changes one operator may have in flight belongs at
the moment one starts and nowhere else: over the limit is a 409, never a queue. Counting alone
would not hold it — two approvals in flight for the same operator both read a count of zero
under READ COMMITTED, because neither sees the other's uncommitted `tool_runs` insert. So the
count is taken under a per-user advisory lock held for the rest of the transaction, and the
repository takes the lock and returns the count in one call so "counted under the lock" is
construction rather than an obligation (the argument `start_change_run` makes for "same
session", one method over).

That lock is always acquired *after* the row lock and never held while waiting for one, so it
adds no deadlock cycle: two approvals for one operator queue on the advisory key, and two
approvals of one request queue on the row — never both ways round.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol, runtime_checkable
from uuid import UUID

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import as_utc, now_utc
from core.approvals.context import (
    AUDIT_CREDENTIAL_KEY,
    CONTEXT_ARGUMENTS_KEY,
    arguments_from_context,
    audit_identity_from_context,
)
from core.approvals.errors import (
    ActionRequestAlreadyDecidedError,
    ActionRequestExpiredError,
    ActionRequestNotFoundError,
    ChangeExecutionLimitReachedError,
    ChangeReasonRequiredError,
)
from core.audit.tool_runs import SQLToolRunRepository
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import ActionRequest, ToolRun

# One structured event per terminal transition. Identifiers only — the reason is the
# operator's own words about a production change and belongs in the row that authorises it,
# not duplicated into a log store (V8's spirit, one field over).
LOG_REQUEST_APPROVED: Final = "action_request_approved"
LOG_REQUEST_DENIED: Final = "action_request_denied"
LOG_REQUEST_EXPIRED_ON_READ: Final = "action_request_expired_on_read"

# The handoff failed *after* the decision was committed. Not an error for the operator (the
# approval stands and is durable); it is the reaper's problem now.
LOG_EXECUTION_HANDOFF_FAILED: Final = "approved_change_execution_handoff_failed"

# An approval was refused because the operator already has their allowance of changes running
# . Logged because "my approve button returns 409" is otherwise indistinguishable, to the
# operator, from a request that expired.
LOG_INFLIGHT_LIMIT_REACHED: Final = "approved_change_inflight_limit_reached"

# The advisory-lock key space V31's count is serialized in. Prefixed rather than bare so the
# hash is drawn from a namespace nothing else in NOA shares: two features hashing raw user ids
# would serialize against each other for no reason.
INFLIGHT_LOCK_NAMESPACE: Final = "noa:approvals:inflight-changes"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LockedActionRequest:
    """A pending request, read under `FOR UPDATE`.

    A value object rather than the ORM row, for the reason `support.tool_runs` gives one
    layer down: the service is what decides, and handing it a live ORM instance would let it
    write columns this design does not permit it to write. It carries exactly the fields the
    decision needs — who may decide, whether it still may be decided, and
    what an approved run has to record.
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
        """The tool arguments as the gate redacted them at request time.

        `core.approvals.context` owns the key and the extraction rule, because T63 reads the
        same payload for `noa_get_action_result` and two readers of one JSONB column with two
        spellings of its key is one spelling too many.

        Unchanged by §V108, deliberately: this is the value a **model** can reach, through
        `ActionResultView.arguments`, so the credential the change acts as is added to the
        audit row next door rather than merged in here.
        """
        return arguments_from_context(self.approval_context)

    @property
    def audit_arguments(self) -> dict[str, Any]:
        """What `tool_runs.args` records for an approved change (V47, §V108).

        The gate's redacted arguments, plus — under one nested key, so nothing here reads as a
        tool parameter — which credential the change will act as. A privileged write whose
        credential is not recorded is not auditable, and `tool_runs` is the table the audit
        surface reads; `action_receipts` carries the same fields for a different reader, and a
        fact reachable only through a join nobody performs is recorded rather than reported.

        Additive by construction: `audit_identity_from_context` answers `{}` unless the evidence
        names an `api_username`, so every CHANGE tool that does not record a credential — the
        firewall pair, the Proxmox pair, PMG's whitelist, all of which do record a `server` —
        writes exactly what it wrote before, and no tool can widen this row by recording more.
        """
        identity = audit_identity_from_context(self.approval_context)
        arguments = self.redacted_arguments
        return arguments if not identity else {**arguments, AUDIT_CREDENTIAL_KEY: identity}


@dataclass(frozen=True)
class ApprovalOutcome:
    """An approval that committed, and the run it started."""

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


class ActionDecisionRepository(Protocol):
    """What a decision needs: a lock, a count, a run, a write, and a commit."""

    async def lock_for_decision(self, *, action_request_id: UUID) -> LockedActionRequest | None: ...

    async def inflight_changes_under_user_lock(self, *, requested_by_user_id: UUID) -> int: ...

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
        """Take the row lock and read the row through it.

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

    async def inflight_changes_under_user_lock(self, *, requested_by_user_id: UUID) -> int:
        """Lock this operator's change slots, then count what is running.

        **One method for both halves on purpose.** A bare count is not the invariant: two
        approvals in flight for one operator each read the other's `tool_runs` insert as absent
        under READ COMMITTED, so both would pass a cap of one. `pg_advisory_xact_lock` is what
        serializes them, and it is held until this transaction ends — by which time the winner's
        insert is committed and the loser's count sees it.

        **Advisory rather than a row lock.** `SELECT … FROM users FOR UPDATE` would serialize
        the same way and would also block `PATCH /admin/users/{id}`: an admin disabling an
        operator would queue behind that operator's approval. A key in a namespace of NOA's own
        blocks nothing but other approvals. `hashtextextended` may collide across user ids,
        which costs two unrelated operators a moment of queueing and cannot cost correctness.

        **In flight = `STARTED` and `CHANGE`.** A run stranded by a dead process still counts,
        which is deliberate and is one of the two reasons the reaper has a deadline: without it,
        one crash would spend an operator's allowance until someone noticed.
        """
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(text(":key"), 0))).params(
                key=f"{INFLIGHT_LOCK_NAMESPACE}:{requested_by_user_id}",
            )
        )
        result = await self._session.execute(
            select(func.count())
            .select_from(ToolRun)
            .where(
                ToolRun.requested_by_user_id == requested_by_user_id,
                ToolRun.risk == ToolRisk.CHANGE,
                ToolRun.status == ToolRunStatus.STARTED,
            )
        )
        return int(result.scalar_one())

    async def start_change_run(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        conversation_ref: str | None,
        args: dict[str, Any],
    ) -> UUID:
        """Insert the `STARTED` run this approval authorises.

        `SQLToolRunRepository` reused rather than a second insert written here — the
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
        """Move the locked row to its terminal state.

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
    """Approve or deny one pending request.

    The ordering lives here, in one place, rather than in the two routes: every guard runs
    inside the lock, and a caller cannot separate taking the lock from writing the answer.
    """

    def __init__(
        self,
        *,
        repository: ActionDecisionRepository,
        executor: ApprovedChangeExecutor,
        max_inflight_per_user: int,
    ) -> None:
        self._repository = repository
        self._executor = executor
        # `APPROVAL_MAX_INFLIGHT_PER_USER`. Required rather than defaulted, for T33(e)'s reason one
        # setting over: a default here would be a second answer to "how many changes may one
        # operator have running", and the copy that drifts is always the one nobody edits.
        self._max_inflight_per_user = max_inflight_per_user

    async def approve(
        self,
        *,
        action_request_id: UUID,
        caller_user_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> ApprovalOutcome:
        """Authorise the change, start its run, hand it off."""
        decided_at = now_utc(now)
        locked = await self._locked_pending(
            action_request_id=action_request_id,
            caller_user_id=caller_user_id,
            reason=reason,
            decided_at=decided_at,
        )
        await self._assert_below_inflight_cap(caller_user_id)
        tool_run_id = await self._repository.start_change_run(
            tool_name=locked.tool_name,
            # The caller, not `locked.requested_by_user_id` — they are the same value, and
            # `_locked_pending` 404s unless they are. Written this way because it is
            # also the narrower type, and because it says the thing T34 says by *dropping*
            # `decided_by_user_id`: the decider is the requester, one identity, not two.
            requested_by_user_id=caller_user_id,
            conversation_ref=locked.conversation_ref,
            # The arguments, plus which credential this change acts as (§V108). See
            # `LockedActionRequest.audit_arguments`: `redacted_arguments` is what a model can
            # reach and is left alone.
            args=locked.audit_arguments,
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
        """Refuse the change.

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

        1. **Reason first**, before any I/O. A decision with nothing in the box is
           refused whatever the row says, and refusing before the lock means a blank submit
           does not queue behind someone else's transaction.
        2. **Lock, then read**. Everything below is evaluated through the lock.
        3. **Absent or not the caller's → 404**, one refusal for both, so the response
           is not an oracle for which requests exist. A NULL requester (the FK is `SET NULL`,
           T34) matches nobody and lands here too, which is the fail-closed direction.
        4. **Not PENDING → 409**. The one permitted transition already happened.
        5. **Past its deadline → terminal EXPIRED, then 409**. This is V32's
           check-on-read, and it *writes*: refusing without the write would leave a row that
           still reads PENDING, so the next reader would have to make the same discovery
           again. The write happens under the lock already held, and the reason stays NULL —
           an expiry is the absence of an answer, not one.
        """
        if not reason.strip():
            raise ChangeReasonRequiredError(
                f"decision on `{action_request_id}` carried a blank reason"
            )

        locked = await self._repository.lock_for_decision(action_request_id=action_request_id)
        if locked is None or locked.requested_by_user_id != caller_user_id:
            raise ActionRequestNotFoundError(
                f"`{action_request_id}` is absent or not requested by `{caller_user_id}`"
            )

        if locked.status is not ActionRequestStatus.PENDING:
            raise ActionRequestAlreadyDecidedError(
                f"`{action_request_id}` is already `{locked.status.value}`"
            )

        if locked.expires_at <= decided_at:
            await self._expire(locked, decided_at=decided_at)
            raise ActionRequestExpiredError(
                f"`{action_request_id}` expired at {locked.expires_at.isoformat()}"
            )

        return locked

    async def _assert_below_inflight_cap(self, caller_user_id: UUID) -> None:
        """Refuse a change this operator has no room to run.

        **Last of the guards, and that ordering is deliberate.** It runs after the row is
        locked and after the request has been found live, so an operator at their limit learns
        it about a request that was actually theirs to approve — and a 404 or an expiry never
        costs an advisory lock. It runs *before* `start_change_run`, because the row that
        insert writes is the thing being counted: after it, the cap could only ever be checked
        against a number this call already changed.

        409, never a queue: V31 says so, and the reason is that a queued approval is an
        authorisation whose moment has passed by the time it runs. The operator's remedy is to
        wait for their running change to finish and approve again — the request stays PENDING
        until its TTL, so nothing is lost by refusing.
        """
        inflight = await self._repository.inflight_changes_under_user_lock(
            requested_by_user_id=caller_user_id,
        )
        if inflight < self._max_inflight_per_user:
            return

        logger.warning(
            LOG_INFLIGHT_LIMIT_REACHED,
            requested_by_user_id=str(caller_user_id),
            inflight=inflight,
            limit=self._max_inflight_per_user,
        )
        raise ChangeExecutionLimitReachedError(
            f"`{caller_user_id}` already has {inflight} CHANGE run(s) in flight, "
            f"limit {self._max_inflight_per_user}"
        )

    async def _expire(self, locked: LockedActionRequest, *, decided_at: datetime) -> None:
        """Make a stale PENDING terminal, under the lock already held."""
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
        """Start the execution, and never fail the approval over it.

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
    "INFLIGHT_LOCK_NAMESPACE",
    "LOG_EXECUTION_HANDOFF_FAILED",
    "LOG_INFLIGHT_LIMIT_REACHED",
    "LOG_REQUEST_APPROVED",
    "LOG_REQUEST_DENIED",
    "LOG_REQUEST_EXPIRED_ON_READ",
    "ActionDecisionRepository",
    "ActionDecisionService",
    "ApprovalOutcome",
    "ApprovedChangeExecutor",
    "DenialOutcome",
    "LockedActionRequest",
    "SQLActionDecisionRepository",
]
