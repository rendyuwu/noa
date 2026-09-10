"""Doubles for reading an approval request back.

`test_action_results_live.py` runs `SQLActionResultRepository` against a real Postgres,
because the states a requester-match has to fail closed on are the database's: a NULL
`requested_by_user_id` left behind by deleting an operator (the FK is `SET NULL`, T34), and a
row that three different writers in `core.approvals` have touched.

What this double buys is the other half: the *order* `ActionResultService` does things in,
and the tool's own shape over `build_tool_context`. Neither needs a database.

**The journal is the point**, as in `support.action_decisions` and `support.action_expiry`.
The repository appends `"read"` and `support.action_expiry.FakeActionRequestExpiryRepository`
appends `"expire"`/`"commit"` to the same list, so a test can assert that a request which is
not the caller's produces `["read"]` and nothing else — the property that keeps a
prompt-injected id from making NOA write to a stranger's row.

The stored value is an `ActionResultView`, not an `ActionRequest`: an ORM instance would carry
its server-defaulted columns as `None` until a flush, so an assertion on `status` would be
asserting against the double's gaps. Same reason `support.action_decisions` keeps dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from core.approvals.results import ActionResultView, ActionRunView
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus

# A CHANGE tool. Named rather than built: these tests are about the read path.
CHANGE_TOOL = "whm_suspend_account"

# The gate's `approval_context` shape (T33's `build_approval_context`), already redacted.
ARGUMENTS: dict[str, Any] = {"server_ref": "alpha", "account": "acmeco"}

# What an operator typed on the card and what the preflight found. Neither may
# reach the model, and both are here so a test can assert their absence against a real value
# rather than against nothing.
REASON = "Customer confirmed the account is compromised; suspending per ticket NOC-4471."
EVIDENCE: dict[str, Any] = {"account": "acmeco", "suspended": False, "domain": "acme.example"}

CREATED_AT = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)


def approval_context(
    *,
    arguments: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    requester_email: str = "operator@example.com",
) -> dict[str, Any]:
    """The JSONB payload the gate persists, shaped as `build_approval_context` shapes it."""
    return {
        "arguments": ARGUMENTS if arguments is None else arguments,
        "requester": {"email": requester_email, "librechat_user_id": "librechat-user-1"},
        "evidence": EVIDENCE if evidence is None else evidence,
    }


def run_view(
    *,
    tool_run_id: UUID | None = None,
    status: ToolRunStatus = ToolRunStatus.STARTED,
    result_summary: str | None = None,
    completed_at: datetime | None = None,
) -> ActionRunView:
    """The execution an approval started, as `SQLActionResultRepository` would report it."""
    return ActionRunView(
        tool_run_id=tool_run_id or uuid4(),
        status=status,
        result_summary=result_summary,
        created_at=CREATED_AT,
        completed_at=completed_at,
    )


def result_view(
    *,
    action_request_id: UUID | None = None,
    tool_name: str = CHANGE_TOOL,
    status: ActionRequestStatus = ActionRequestStatus.PENDING,
    arguments: dict[str, Any] | None = None,
    expires_in_seconds: float = 3600,
    now: datetime | None = None,
    decided_at: datetime | None = None,
    run: ActionRunView | None = None,
) -> ActionResultView:
    """One request as the reader returns it. `expires_in_seconds` may be negative.

    `created_at` is the fixed stamp — nothing judges it, so pinning it keeps equality
    assertions on the payload exact. The **deadline** is offset from the real clock,
    because that is the one field something compares: a fixed `expires_at` would be in the
    past by the time the suite runs and every PENDING request would read `EXPIRED`. Same
    construction as `support.action_expiry.pending_row`.
    """
    moment = now or datetime.now(UTC)
    return ActionResultView(
        action_request_id=action_request_id or uuid4(),
        tool_name=tool_name,
        status=status,
        arguments=ARGUMENTS if arguments is None else arguments,
        created_at=CREATED_AT,
        expires_at=moment + timedelta(seconds=expires_in_seconds),
        decided_at=decided_at,
        run=run,
    )


@dataclass
class StoredResult:
    """A view plus the operator it belongs to. `None` stands for a deleted requester."""

    view: ActionResultView
    requester_user_id: UUID | None


class FakeActionResultRepository:
    """In-memory `ActionResultRepository`, with the read breakable.

    `fail` exists because V19's guarantee is that *no* raw exception reaches the model, and
    the read is the one place on this tool's path that can raise for reasons nobody predicted.
    """

    def __init__(self, journal: list[str] | None = None) -> None:
        self.rows: dict[UUID, StoredResult] = {}
        self.journal = journal if journal is not None else []
        # One entry per call, so a test can assert *which* requester was asked about — the
        # token's caller, never an argument.
        self.lookups: list[tuple[UUID, UUID]] = []
        self.fail: BaseException | None = None

    def add(
        self,
        view: ActionResultView,
        *,
        requester_user_id: UUID | None,
    ) -> ActionResultView:
        self.rows[view.action_request_id] = StoredResult(
            view=view, requester_user_id=requester_user_id
        )
        return view

    async def get_for_requester(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
    ) -> ActionResultView | None:
        self.journal.append("read")
        self.lookups.append((action_request_id, requester_user_id))
        if self.fail is not None:
            raise self.fail

        stored = self.rows.get(action_request_id)
        if stored is None or stored.requester_user_id != requester_user_id:
            # Absent, foreign, or its requester was deleted — one answer for all three, the
            # way the production `WHERE` gives one. Whether a real row behaves this way
            # is `test_action_results_live.py`'s claim, not this double's.
            return None
        return stored.view


__all__ = [
    "ARGUMENTS",
    "CHANGE_TOOL",
    "CREATED_AT",
    "EVIDENCE",
    "REASON",
    "FakeActionResultRepository",
    "StoredResult",
    "approval_context",
    "result_view",
    "run_view",
]
