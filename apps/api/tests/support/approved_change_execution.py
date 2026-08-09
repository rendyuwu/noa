"""Doubles for the post-approval executor and the reaper (T38).

Same split as `support.action_decisions` beside `test_action_request_decisions_live.py`, and for
the same reason: `test_approved_change_execution_live.py` and `test_stranded_run_reaper_live.py`
run the real SQL against a scratch Postgres, because "one receipt per request" and "a row
exactly on its cutoff is reaped" are claims *about the database*. What these doubles serve is
everything around it — the order the service does things in, what a runner is handed, what a
failure records, and the host's one-task-one-session rule — none of which needs Docker.

**The journal is the point**, as everywhere else here. `["load", "run", "finish:FAILED",
"receipt", "commit"]` pins four separate decisions at once: the authorization is read before
anything happens (V23), the runner is dispatched after it, the terminal status and the receipt
are both written before the single commit (V46), and there is exactly one commit — a receipt
that committed separately from its run could survive a rollback of the status it describes.

`RecordingSessionFactory` and `FakeSession` are imported from `support.action_expiry` rather
than re-declared: T39's sweeper needed the same "a fresh session per unit of work, and it was
closed" instrument, and two copies of it are two things that can silently stop agreeing (V66).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from core.approvals.execution import (
    AuthorizedChange,
    ChangeExecutionRequest,
)
from core.approvals.reaper import StrandedRun
from core.db.lifecycle import ToolRunStatus
from support.action_decisions import APPROVAL_CONTEXT, CHANGE_TOOL, CONVERSATION_ID

# The gate-time preflight as `APPROVAL_CONTEXT` carries it (T33's `build_approval_context`), so
# a receipt's before-state is assertable against the same payload the card would show.
EVIDENCE: dict[str, Any] = dict(APPROVAL_CONTEXT["evidence"])

# The arguments the gate recorded, redacted (they carry no sensitive key, which is the normal
# case — C15/V49 generate secrets server-side).
ARGUMENTS: dict[str, Any] = dict(APPROVAL_CONTEXT["arguments"])

# What a runner answers on success. The ordinary tool envelope (`noa_api.mcp_tools.results`),
# because that is what the executor classifies and bounds.
RUNNER_OK: dict[str, Any] = {"ok": True, "account": "acmeco", "suspended": True}


def authorized_change(
    *,
    action_request_id: UUID | None = None,
    tool_run_id: UUID | None = None,
    tool_name: str = CHANGE_TOOL,
    arguments: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> AuthorizedChange:
    """An approved request as `load_authorized` would return it."""
    return AuthorizedChange(
        action_request_id=action_request_id or uuid4(),
        tool_run_id=tool_run_id or uuid4(),
        tool_name=tool_name,
        arguments=ARGUMENTS if arguments is None else arguments,
        evidence=EVIDENCE if evidence is None else evidence,
        conversation_ref=CONVERSATION_ID,
    )


@dataclass
class RecordedFinish:
    """One terminal `tool_runs` write as the service asked for it (V20, V47)."""

    tool_run_id: UUID
    status: ToolRunStatus
    result_summary: str | None


@dataclass
class RecordedReceipt:
    """One `action_receipts` write (V46)."""

    action_request_id: UUID
    tool_run_id: UUID | None
    receipt_data: dict[str, Any]


class FakeApprovedChangeExecutionRepository:
    """In-memory `ApprovedChangeExecutionRepository` over one seeded authorization.

    `authorized` is what `load_authorized` answers with, and `None` is how a test reaches V23's
    refusal — the same shape the SQL produces when the row is not APPROVED or names a different
    run, which is why the refusal is expressible here at all.
    """

    def __init__(
        self,
        journal: list[str] | None = None,
        *,
        authorized: AuthorizedChange | None = None,
    ) -> None:
        self.authorized = authorized
        self.loads: list[tuple[UUID, UUID]] = []
        self.finishes: list[RecordedFinish] = []
        self.receipts: list[RecordedReceipt] = []
        self.commits = 0
        self.journal = journal if journal is not None else []
        # Set to make the receipt insert report "already there", which is what the real one does
        # when the reaper got to a finished run first (T36's UNIQUE).
        self.receipt_exists = False

    async def load_authorized(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID,
    ) -> AuthorizedChange | None:
        self.journal.append("load")
        self.loads.append((action_request_id, tool_run_id))
        return self.authorized

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None:
        self.journal.append(f"finish:{status.value}")
        self.finishes.append(
            RecordedFinish(
                tool_run_id=tool_run_id,
                status=status,
                result_summary=result_summary,
            )
        )

    async def record_receipt(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None:
        self.journal.append("receipt")
        self.receipts.append(
            RecordedReceipt(
                action_request_id=action_request_id,
                tool_run_id=tool_run_id,
                receipt_data=receipt_data,
            )
        )
        return None if self.receipt_exists else uuid4()

    async def commit(self) -> None:
        self.journal.append("commit")
        self.commits += 1

    # --- Assertions ---

    @property
    def only_finish(self) -> RecordedFinish:
        assert len(self.finishes) == 1, f"expected one terminal write, got {len(self.finishes)}"
        return self.finishes[0]

    @property
    def only_receipt(self) -> RecordedReceipt:
        assert len(self.receipts) == 1, f"expected one receipt, got {len(self.receipts)}"
        return self.receipts[0]


class RecordingChangeRunner:
    """A `ChangeRunner` that records what it was handed and answers what it was told to.

    `fail` makes it raise, which is the contract-breach path: a runner is expected to carry
    `sanitize_tool_errors` and answer `ok: False` instead (V19), and the executor's backstop is
    what stops a raise from leaving a run STARTED forever.

    `block` holds the change open until the event is set, which is how "the handoff returned
    before the change finished" and "shutdown cancels work in flight" are expressible at all —
    a runner that answers immediately makes both of those a race with the scheduler rather than
    an assertion.
    """

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        journal: list[str] | None = None,
    ) -> None:
        self.payload = RUNNER_OK if payload is None else payload
        self.calls: list[ChangeExecutionRequest] = []
        self.fail: BaseException | None = None
        self.block: asyncio.Event | None = None
        self.journal = journal if journal is not None else []

    async def __call__(self, request: ChangeExecutionRequest) -> dict[str, Any]:
        self.journal.append("run")
        self.calls.append(request)
        if self.block is not None:
            await self.block.wait()
        if self.fail is not None:
            raise self.fail
        return dict(self.payload)

    @property
    def only_call(self) -> ChangeExecutionRequest:
        assert len(self.calls) == 1, f"expected one runner call, got {len(self.calls)}"
        return self.calls[0]


def stranded_run(
    *,
    tool_run_id: UUID | None = None,
    tool_name: str = CHANGE_TOOL,
    action_request_id: UUID | None = None,
    evidence: dict[str, Any] | None = None,
) -> StrandedRun:
    """A run the reaper found still `STARTED` past its deadline.

    `action_request_id=None` is a READ run (or a change whose request row was deleted), which is
    the case that owes no receipt — `action_receipts.action_request_id` is NOT NULL (T36).
    """
    return StrandedRun(
        tool_run_id=tool_run_id or uuid4(),
        tool_name=tool_name,
        action_request_id=action_request_id,
        evidence=EVIDENCE if evidence is None else evidence,
    )


@dataclass
class FakeStrandedRunRepository:
    """In-memory `StrandedRunRepository` over rows a test seeds.

    The predicates are *not* here: the live file asserts the SQL. What this buys is a repository
    the service and its loop can be driven against — what it writes per stranded run, which runs
    owe a receipt, and that a pass is one transaction.
    """

    journal: list[str] = field(default_factory=list)
    runs: list[StrandedRun] = field(default_factory=list)
    orphans: list[UUID] = field(default_factory=list)
    finishes: list[RecordedFinish] = field(default_factory=list)
    receipts: list[RecordedReceipt] = field(default_factory=list)
    commits: int = 0
    fail: BaseException | None = None
    # The cutoffs the service judged each population against, so a test can assert one clock
    # read per pass rather than two that nearly agree.
    cutoffs: list[Any] = field(default_factory=list)

    async def stranded_runs(self, *, cutoff: Any) -> tuple[StrandedRun, ...]:
        self.journal.append("stranded")
        self.cutoffs.append(cutoff)
        if self.fail is not None:
            raise self.fail
        return tuple(self.runs)

    async def approved_without_run(self, *, cutoff: Any) -> tuple[UUID, ...]:
        self.journal.append("orphans")
        self.cutoffs.append(cutoff)
        return tuple(self.orphans)

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None:
        self.journal.append(f"finish:{status.value}")
        self.finishes.append(
            RecordedFinish(
                tool_run_id=tool_run_id,
                status=status,
                result_summary=result_summary,
            )
        )

    async def record_receipt(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None:
        self.journal.append("receipt")
        self.receipts.append(
            RecordedReceipt(
                action_request_id=action_request_id,
                tool_run_id=tool_run_id,
                receipt_data=receipt_data,
            )
        )
        return uuid4()

    async def commit(self) -> None:
        self.journal.append("commit")
        self.commits += 1


__all__ = [
    "ARGUMENTS",
    "EVIDENCE",
    "RUNNER_OK",
    "FakeApprovedChangeExecutionRepository",
    "FakeStrandedRunRepository",
    "RecordedFinish",
    "RecordedReceipt",
    "RecordingChangeRunner",
    "authorized_change",
    "stranded_run",
]
