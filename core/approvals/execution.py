"""Running a change an operator approved.

The gate that opens the request opens the question, the table's row *is* the authorization, the
decision endpoint answers it under a row lock and opens the `STARTED` `tool_runs` row inside the
decision's own transaction. This is the only thing that **executes** the change, and it runs
after that transaction committed (`core.approvals.decisions._hand_off` — the state lives in the
database, not in a connection).

**The authorization is re-read here, not trusted from the handoff.** `start` is called with
two identifiers, and identifiers are not permission. So the first thing an execution
does is load the row and refuse unless it is `APPROVED` **and** its `tool_run_id` is the run
it was handed. Two reasons that is not belt-and-braces: the handoff crosses a task boundary,
so the row can have moved by the time the task is scheduled; and a future caller — an
operator tool, a retry — would otherwise be a second door onto "run this change", on the far
side of the one the cookie/CSRF boundary closes.

**One transaction for the terminal write and the receipt.** An approved change owes three
artifacts: the run (what ran), the receipt (what it did), and the audit log. The run's
terminal status and the receipt land in one commit, because a `COMPLETED` run whose receipt
rolled back is run-plus-receipt asserted by prose and held by nothing (prose is not evidence a
control works). The audit log is the structured events below, the way the RBAC engine satisfies
the audit rule's "produce audit events".

**The receipt is two-part.** DECISIONS.md section 6.5 requires an operator to be able to read
back before-state and after-state separately rather than a single "done" — the release-and-allow
tool's two-part receipt. The before-state is the gate's own in-process preflight evidence, already
persisted on `approval_context` at gate time, so it is the state the operator authorised
against and not a second reading taken later. The after-state is what the runner answered, or
the named reason there is none. A failure gets a receipt too: a receipt with a before-state
and no after-state is the truthful record of a change that did not complete.

**What actually performs the change is a runner.** The suspend tool registered the first
(`whm_suspend_account`) and the unsuspend tool the second (`whm_unsuspend_account`); the CHANGE
tools after these two are still unbuilt, so an unknown tool name remains reachable and it fails
closed: the run goes `FAILED` with `change_runner_unavailable`, which is a named outcome an
operator can act on rather than a run that sits `STARTED` until the reaper takes it.

**The operator's reason is carried to the runner, and it is a value rather than a permission.**
The reason rule bars the *LLM* from authoring, relaying or seeing a reason; it does not bar NOA
from writing the operator's own words onto the system being changed, and WHM's `suspendacct` has
a suspension-note field that would otherwise hold a NOA-authored placeholder. So
`load_authorized` reads `action_requests.reason` and `ChangeExecutionRequest` carries it. Two
bounds travel with it: nothing here branches on the string (the authorization was decided by
`status = APPROVED` before it was read), and it must not come back in a runner's payload —
`result_summary` is derived from that payload and `noa_get_action_result` returns it to a model.

It is carried for *every* approved change and read by the runners that have somewhere to put it.
The unsuspend tool's `unsuspendacct` has no note field, so that runner never touches the value —
which is what "a value, not a permission" looks like from the other side.

Not through the receipt, which is the door it is tempting to name: `core.approvals.results`
leaves `include_receipt` at its default, so the action-result tool's reader never joins
`action_receipts`. The receipt is read by the approval card and the admin audit surface, both of
which are the operator's own. Naming the wrong door here would send the next runner's author to
guard the wrong field.

**Redacted arguments are refused, not executed.** `approval_context.arguments` is redacted at
gate time (`noa_api.mcp_tools.change_gate.build_approval_context`), and redaction is by key
name (`core.secrets.redaction`). Every CHANGE tool NOA plans generates its secrets
server-side, so no argument NOA needs is redacted today — but the day one is, this
refuses instead of running the change with `[redacted]` where a value belonged. Checked by key
name rather than by comparing values to the placeholder, so a legitimate argument whose value
happens to be the literal string is not a false refusal — and checked with the redactor's own
recursive walk (`core.secrets.redaction.sensitive_key_paths`), because a top-level-only scan
would pass `{"server": {"ssh_password": ...}}` straight through to a runner.

**What the runner answered is redacted before it is stored.** `tool_runs.result_summary` goes
through `core.audit.summaries`, which redacts; `action_receipts.receipt_data` is the same
payload and outlives the call in front of the same readers, so it goes through the same rule.
A per-writer exemption is how one of them eventually puts a credential in the audit trail.

**A runner may state a before→after delta, and it arrives beside the envelope rather than in
it.** The two halves meet only on identity — keys that carry the same value in both, because a
runner resolves its identity out of the evidence — and the field a change actually moved is never
addressable in both, so nothing downstream can diff them; the runner is the only party holding
both vocabularies, and `ChangeOutcome` is how it says so without touching the payload
(`core.approvals.delta` carries that argument in full, and
`apps/api/tests/test_change_receipt_halves.py` holds it). Two consequences
here. `after` stays byte-identical to the payload the runner returned, because the delta never
enters it — and so does `result_summary`, which is derived from the same payload and cut at
2000 characters. And the delta is redacted on its own line below rather than lifted from raw
payload the way `ok` and `error_code` are, because those two are read *unredacted* on purpose
and a delta must not be.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.context import arguments_from_context, evidence_from_context
from core.approvals.delta import ChangeDelta, ChangeOutcome
from core.audit.receipts import ActionReceiptRepository, SQLActionReceiptRepository
from core.audit.summaries import result_summary, status_for_payload
from core.audit.tool_runs import SQLToolRunRepository, ToolRunRepository
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from core.db.models import ActionRequest
from core.errors import NoaError
from core.secrets.redaction import redact_mapping, sensitive_key_paths

# The execution began. One event per authorised change actually starting to run, so "why did
# this account get suspended at 03:00" is answerable from the logs (the audit log, third of the
# three required artifacts).
LOG_EXECUTION_STARTED: Final = "approved_change_execution_started"

# It finished, either way. `status` says which; the summary is on the row, not in the log.
LOG_EXECUTION_FINISHED: Final = "approved_change_execution_finished"

# The handoff named a run this execution may not run: the row is not APPROVED, or the run is
# not the one that row authorises. Logged at error — reaching it means something asked NOA to
# run a change nothing authorised.
LOG_EXECUTION_UNAUTHORIZED: Final = "approved_change_execution_unauthorized"

# The change did not happen, and NOA knows why: no runner, unrunnable arguments, or a runner
# that raised. The operator-safe sentence goes on the row; the cause goes here.
LOG_EXECUTION_REFUSED: Final = "approved_change_execution_refused"

# No runner is registered for the tool this request names.
ERROR_RUNNER_UNAVAILABLE: Final = "change_runner_unavailable"

MESSAGE_RUNNER_UNAVAILABLE: Final = (
    "NOA cannot run this change: the tool that requested it is not available to execute. "
    "Contact an administrator."
)

# The persisted arguments carry a redacted key, so they are not the arguments the change needs.
ERROR_ARGUMENTS_REDACTED: Final = "change_arguments_redacted"

MESSAGE_ARGUMENTS_REDACTED: Final = (
    "NOA will not run this change because part of what was asked for was not recorded in a "
    "runnable form. Ask for the change again; contact an administrator if this continues."
)

# A runner raised instead of answering. Its own code when it is a `NoaError` (the
# sanitize-to-a-code rule, one boundary over); otherwise this, which names a NOA bug rather than
# blaming the operator.
ERROR_EXECUTION_FAILED: Final = "change_execution_failed"

MESSAGE_EXECUTION_FAILED: Final = (
    "The change failed while running. Check the target system before asking again; contact an "
    "administrator if this continues."
)

# Receipt keys. Constants because three readers are coming — the approval card, the
# action-result tool and the admin audit surface — and a misspelt key in JSONB reads as an
# absent one (one helper, not two — the same argument `core.approvals.context` makes for
# `approval_context`).
RECEIPT_OK_KEY: Final = "ok"
RECEIPT_BEFORE_KEY: Final = "before"
RECEIPT_AFTER_KEY: Final = "after"
RECEIPT_ERROR_CODE_KEY: Final = "error_code"

# The fifth key, and the only one that is sometimes not there at all. Absent means the runner
# measured nothing worth stating — which is a claim, not a gap (`core.approvals.delta`), and the
# one thing that separates an executor refusal from a runner failure, since both are `ok: False`.
RECEIPT_DELTA_KEY: Final = "delta"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AuthorizedChange:
    """An approved request and the run it authorised, as the executor may act on it.

    Returned only when the row said `APPROVED` and named this run, so holding one of these
    *is* the authorization — there is no field here for a caller to check afterwards and
    forget to.

    **`reason` is the operator's own words, and it is here on purpose**. The reason boundary
    is that the LLM never authors, relays or sees a reason; it does not say the reason may
    not reach the system being changed. WHM's `suspendacct` takes a suspension note, and the
    note an operator would want there is the one they typed on the card — so the field is
    read from `action_requests.reason` and handed to the runner. It is a *value* here, never
    an authorization: `load_authorized` already refused unless the row said `APPROVED`, and
    nothing below branches on this string.

    Non-optional, because the only rows that reach here are `APPROVED` and
    `ck_action_requests_decided_reason` makes a decided row's reason non-blank.
    """

    action_request_id: UUID
    tool_run_id: UUID
    tool_name: str
    arguments: dict[str, Any]
    evidence: dict[str, Any]
    reason: str
    conversation_ref: str | None


@dataclass(frozen=True)
class ChangeExecutionRequest:
    """What a runner is handed: the change to make, and the state it was authorised against.

    Deliberately not the ORM row and not `AuthorizedChange`: a runner is integration code, and
    handing it the authorization would let it decide for itself whether it may run.
    """

    action_request_id: UUID
    tool_run_id: UUID
    tool_name: str
    arguments: dict[str, Any]
    # The gate's in-process preflight — the before-state the operator approved
    # against. A runner reads it rather than re-gathering, so the receipt's two halves describe
    # one decision.
    evidence: dict[str, Any]
    # What the operator typed on the approval card, the one reason field the LLM never sees. A
    # runner uses it where the target system has a place for it — WHM's suspension note — and
    # nowhere else. Two rules a runner carries with it: it is not an authorization (that was
    # decided before this value was read), and it must not come back in the runner's payload,
    # which `result_summary` is derived from and `noa_get_action_result` hands to a model.
    reason: str


class ChangeRunner(Protocol):
    """Performs one CHANGE and answers with the ordinary tool envelope.

    `{"ok": True, ...}` or `{"ok": False, "error_code": ..., "message": ...}`, the shape
    `noa_api.mcp_tools.results` defines for every exposed tool — so `status_for_payload`
    classifies the run and `result_summary` bounds it without a second convention.

    **A runner should not raise.** CHANGE tool bodies carry `sanitize_tool_errors`, so a
    timeout or an SSH failure arrives here as `ok: False` with the code that names it. The
    executor still catches, because a contract held only by discipline is held by nothing —
    but what it can record then is coarser than what the runner knew.

    **A runner must not echo `request.reason` back in its payload.** `result_summary` is derived
    from that payload (`core.audit.summaries`) and `noa_get_action_result` returns the summary to
    a model, so an answer repeating the note it just wrote would hand the LLM the one field it
    never sees — through the tool-run trail's audit row rather than through any tool schema. The
    same fence holds on a delta and is checked there (`core.approvals.delta`), because a receipt
    key is that door one step over.

    **A runner may answer a `ChangeOutcome` instead of a bare envelope**, which is how it states
    a before→after delta beside the payload rather than inside it. The bare envelope is still a
    complete answer and means "nothing measured worth stating"; the union is what keeps a runner
    with no delta to publish from having to wrap one in an empty object.
    """

    async def __call__(self, request: ChangeExecutionRequest) -> dict[str, Any] | ChangeOutcome: ...


class ApprovedChangeExecutionRepository(Protocol):
    """What one execution needs: the authorization, the terminal write, the receipt."""

    async def load_authorized(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID,
    ) -> AuthorizedChange | None: ...

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None: ...

    async def record_receipt(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None: ...

    async def commit(self) -> None: ...


class SQLApprovedChangeExecutionRepository:
    """`ApprovedChangeExecutionRepository` over one `AsyncSession`.

    The session is the execution's own — one per approved change, opened and closed by
    `AsyncioApprovedChangeExecutor`, never a request's and never one held for the life of the
    process.

    Neither write is issued here directly. `SQLToolRunRepository` owns `tool_runs` and
    `SQLActionReceiptRepository` owns `action_receipts`, both constructed on *this* session,
    which is what makes the terminal status and the receipt one transaction (one writer
    per table, and the composition is what makes "same session" construction rather than an
    obligation).
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        runs: ToolRunRepository | None = None,
        receipts: ActionReceiptRepository | None = None,
    ) -> None:
        self._session = session
        self._runs = runs or SQLToolRunRepository(session)
        self._receipts = receipts or SQLActionReceiptRepository(session)

    async def load_authorized(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID,
    ) -> AuthorizedChange | None:
        """The authorization, or `None` if there is not one for this pair.

        Both halves of the predicate are in the statement rather than checked after the read:
        `status = APPROVED` and `tool_run_id = :run`. A row that is `DENIED`, `EXPIRED`, still
        `PENDING`, or linked to a different run is never fetched, so there is no later branch
        that could forget to drop it — the argument `core.approvals.reads` makes for putting
        the requester-match rule in the `WHERE`.

        No lock. The decision that produced this row is committed and the one-decision rule
        permits no second transition, so there is nothing left to serialize against; the
        terminal write below
        touches `tool_runs`, which only this execution and the reaper reach.
        """
        result = await self._session.execute(
            select(ActionRequest).where(
                ActionRequest.id == action_request_id,
                ActionRequest.status == ActionRequestStatus.APPROVED,
                ActionRequest.tool_run_id == tool_run_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        context = dict(row.approval_context or {})
        return AuthorizedChange(
            action_request_id=row.id,
            tool_run_id=tool_run_id,
            tool_name=row.tool_name,
            arguments=arguments_from_context(context),
            evidence=evidence_from_context(context),
            # Fetched deliberately, not incidentally: a runner that has to write the
            # operator's note onto the target system needs the value, and a projection that
            # merely happened to load it would be a separation held by nothing. `or ""` is a
            # type coercion and not a fallback — the row is `APPROVED`, so the table's
            # `ck_action_requests_decided_reason` has already refused a blank one.
            reason=row.reason or "",
            conversation_ref=row.conversation_ref,
        )

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None:
        """Move the run the decision endpoint opened to its terminal state."""
        await self._runs.finish_run(
            tool_run_id=tool_run_id,
            status=status,
            result_summary=result_summary,
        )

    async def record_receipt(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None:
        """Write the receipt, unless one is already there (the receipt table's UNIQUE
        constraint)."""
        return await self._receipts.create_if_missing(
            action_request_id=action_request_id,
            tool_run_id=tool_run_id,
            receipt_data=receipt_data,
        )

    async def commit(self) -> None:
        """Make the terminal status and the receipt durable together."""
        await self._session.commit()


class ApprovedChangeExecutionService:
    """Execute one approved change, and record what it did.

    The order lives here, in one place, rather than in the asyncio host that calls it: read
    the authorization, run the change, record both artifacts in one commit.
    """

    def __init__(
        self,
        *,
        repository: ApprovedChangeExecutionRepository,
        runners: Mapping[str, ChangeRunner],
    ) -> None:
        self._repository = repository
        self._runners = dict(runners)

    async def execute_approved_tool_run(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID,
    ) -> ToolRunStatus | None:
        """Run the change this pair authorises; return the run's terminal status.

        `None` means nothing was authorised and nothing was written — the refusal reading the
        verdict from `status` every time makes this method's first act. Every other path ends
        in a terminal `tool_runs` row and a
        receipt, so a caller polling the run always reaches an answer.
        """
        authorized = await self._repository.load_authorized(
            action_request_id=action_request_id,
            tool_run_id=tool_run_id,
        )
        if authorized is None:
            logger.error(
                LOG_EXECUTION_UNAUTHORIZED,
                action_request_id=str(action_request_id),
                tool_run_id=str(tool_run_id),
            )
            return None

        logger.info(
            LOG_EXECUTION_STARTED,
            action_request_id=str(authorized.action_request_id),
            tool=authorized.tool_name,
            tool_run_id=str(authorized.tool_run_id),
            conversation_ref=authorized.conversation_ref,
        )

        outcome = await self._run(authorized)
        return await self._record(authorized, outcome)

    # --- Internals ---

    async def _run(self, authorized: AuthorizedChange) -> ChangeOutcome:
        """The change itself, or the named reason it did not happen.

        Three refusals before a runner is reached or instead of reaching one, and each returns
        the same envelope a runner would so `_record` has one shape to classify:

        1. **arguments that were redacted** — see the module docstring. Refused before dispatch
           because the runner is what would act on the wrong values.
        2. **no runner for this tool** — still reachable today, for the names the remaining
           CHANGE tools own.
        3. **a runner that raised** — `NoaError` keeps its own `error_code`, the way the
           sanitize-to-a-code rule's boundary passes one through, so an integration refusal
           such as
           `ssh_host_key_not_validated` still names the thing an admin has to fix. Anything
           else is a NOA bug and says so. `BaseException` is not caught: a cancelled execution
           (app shutdown) has no answer to record, and the row it leaves `STARTED` is exactly
           what the reaper is for.

        **Every refusal here carries no delta, and that is the fact a reader acts on.** Nothing
        was measured on any of these three paths — no runner was reached, or one was reached and
        raised — so there is nothing to state, and `ChangeOutcome.delta is None` says so rather
        than shipping an object full of absences. A runner *failure* is the opposite case and
        looks nothing like this: it answers `ok: False` with a delta carrying what it did
        measure, which is why the two cannot be told apart by the envelope.

        A runner that builds an impossible delta lands on the third path: `ChangeDelta` refuses
        at construction (`core.approvals.delta`), the `ValueError` arrives here, and the run
        becomes a named terminal failure with a receipt rather than a half-written one.
        """
        redacted = sorted(sensitive_key_paths(authorized.arguments))
        if redacted:
            return _failure(
                ERROR_ARGUMENTS_REDACTED,
                MESSAGE_ARGUMENTS_REDACTED,
                log_detail=f"redacted argument key(s) {redacted}",
                tool_name=authorized.tool_name,
            )

        runner = self._runners.get(authorized.tool_name)
        if runner is None:
            return _failure(
                ERROR_RUNNER_UNAVAILABLE,
                MESSAGE_RUNNER_UNAVAILABLE,
                log_detail="no ChangeRunner registered",
                tool_name=authorized.tool_name,
            )

        request = ChangeExecutionRequest(
            action_request_id=authorized.action_request_id,
            tool_run_id=authorized.tool_run_id,
            tool_name=authorized.tool_name,
            arguments=dict(authorized.arguments),
            evidence=dict(authorized.evidence),
            reason=authorized.reason,
        )
        try:
            answered = await runner(request)
        except NoaError as exc:
            return _failure(
                exc.error_code,
                exc.message,
                log_detail=str(exc),
                tool_name=authorized.tool_name,
            )
        except Exception as exc:
            return _failure(
                ERROR_EXECUTION_FAILED,
                MESSAGE_EXECUTION_FAILED,
                log_detail=f"{type(exc).__name__}: {exc}",
                tool_name=authorized.tool_name,
            )
        # A runner with nothing to state answers the bare envelope, and normalising it here is
        # what keeps that from being seven authors' problem.
        return answered if isinstance(answered, ChangeOutcome) else ChangeOutcome(payload=answered)

    async def _record(
        self,
        authorized: AuthorizedChange,
        outcome: ChangeOutcome,
    ) -> ToolRunStatus:
        """The terminal run and the receipt, in one commit.

        The status is read off the envelope's `ok` (`core.audit.summaries`), the same rule the
        READ path records with — so a runner that answered a refusal is not recorded as a
        change that worked.

        The delta travels to `build_receipt` and nowhere else: `status_for_payload` and
        `result_summary` are handed the runner's payload exactly as it arrived, so both are what
        they were before a delta existed.
        """
        payload = outcome.payload
        status = status_for_payload(payload)
        await self._repository.finish_run(
            tool_run_id=authorized.tool_run_id,
            status=status,
            result_summary=result_summary(payload),
        )
        await self._repository.record_receipt(
            action_request_id=authorized.action_request_id,
            tool_run_id=authorized.tool_run_id,
            receipt_data=build_receipt(
                evidence=authorized.evidence,
                payload=payload,
                delta=outcome.delta,
            ),
        )
        await self._repository.commit()

        logger.info(
            LOG_EXECUTION_FINISHED,
            action_request_id=str(authorized.action_request_id),
            tool=authorized.tool_name,
            tool_run_id=str(authorized.tool_run_id),
            status=status.value,
            error_code=payload.get(RECEIPT_ERROR_CODE_KEY),
        )
        return status


def build_receipt(
    *,
    evidence: Mapping[str, Any],
    payload: Mapping[str, Any],
    delta: ChangeDelta | None = None,
) -> dict[str, Any]:
    """The two-part receipt the run-plus-receipt-plus-audit rule stores and the approval card
    renders (DECISIONS.md section 6.5, the release-and-allow tool).

    `before` is the gate-time preflight the operator authorised against; `after` is what the
    change answered. Both are present on a failure too, `after` carrying the refusal — the two
    halves are never collapsed into a single "done", which is the whole point of the shape.

    `ok` is lifted to the top level from the envelope rather than recomputed: one field, one
    source. `error_code` appears only when there is one, matching `tool_failure`'s rule that an
    absent field beats an empty one.

    `after` is redacted on the way in, the same rule `result_summary` applies to the same
    payload: this row outlives the call and is read by the approval card, the action-result tool
    and the admin audit surface, so a runner that answers with a `password` field must not leave
    one here. `before` is the gate's own `approval_context` evidence, copied rather than
    re-derived — whatever redaction it carries is the gate's own, and re-deciding it here would
    be a second answer.

    **`delta` is a fifth key and it is omitted entirely when there is none.** It arrives as its
    own parameter, so `before` and `after` are byte-for-byte what they were without it, and it
    goes through the redactor on its own line rather than inheriting the payload's pass — the two
    are separate values redacted separately, and a runner that puts a delivery URL in one and not
    the other still gets one policy. Absent rather than `null`, matching `error_code`'s rule
    directly above: an absent field beats an empty one, and here the absence is what says nothing
    was measured.

    Both lines call `redact_mapping`, which answers a mapping because it takes one. The general
    `redact_sensitive_data` answers `object`, and the two ways of narrowing that at a call site
    are a `cast` that verifies nothing and an `if isinstance(...) else {}` that cannot run — the
    second is what stood here, and an unreachable fallback to a benign value is the shape this
    module argues against one function down.
    """
    receipt: dict[str, Any] = {
        RECEIPT_OK_KEY: payload.get(RECEIPT_OK_KEY) is True,
        RECEIPT_BEFORE_KEY: dict(evidence),
        RECEIPT_AFTER_KEY: redact_mapping(payload),
    }
    error_code = payload.get(RECEIPT_ERROR_CODE_KEY)
    if error_code:
        receipt[RECEIPT_ERROR_CODE_KEY] = error_code
    if delta is not None:
        receipt[RECEIPT_DELTA_KEY] = redact_mapping(delta.as_payload())
    return receipt


def _failure(
    error_code: str,
    message: str,
    *,
    log_detail: str,
    tool_name: str,
) -> ChangeOutcome:
    """A refusal in the tool envelope's shape, with the cause logged and not returned.

    Same split `sanitize_tool_errors` makes and for the same reason: `message` is
    what an operator reads on the card and what a model may be told, while the detail — an
    exception's text, a host, an argument name — stays in the log.

    No delta, by construction rather than by omission: every caller of this reached it because
    nothing ran, and a refusal that nothing measured has nothing to state.
    """
    logger.warning(
        LOG_EXECUTION_REFUSED,
        tool=tool_name,
        error_code=error_code,
        detail=log_detail,
    )
    return ChangeOutcome(
        payload={"ok": False, RECEIPT_ERROR_CODE_KEY: error_code, "message": message}
    )


__all__ = [
    "ERROR_ARGUMENTS_REDACTED",
    "ERROR_EXECUTION_FAILED",
    "ERROR_RUNNER_UNAVAILABLE",
    "LOG_EXECUTION_FINISHED",
    "LOG_EXECUTION_REFUSED",
    "LOG_EXECUTION_STARTED",
    "LOG_EXECUTION_UNAUTHORIZED",
    "MESSAGE_ARGUMENTS_REDACTED",
    "MESSAGE_EXECUTION_FAILED",
    "MESSAGE_RUNNER_UNAVAILABLE",
    "RECEIPT_AFTER_KEY",
    "RECEIPT_BEFORE_KEY",
    "RECEIPT_DELTA_KEY",
    "RECEIPT_ERROR_CODE_KEY",
    "RECEIPT_OK_KEY",
    "ApprovedChangeExecutionRepository",
    "ApprovedChangeExecutionService",
    "AuthorizedChange",
    "ChangeExecutionRequest",
    "ChangeRunner",
    "SQLApprovedChangeExecutionRepository",
    "build_receipt",
]
