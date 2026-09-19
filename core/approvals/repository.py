"""SQL behind the CHANGE approval gate.

The schema landed and wrote nothing to it. This is the writer, and
`noa_api.mcp_tools.change_gate` is its only caller: one INSERT, one status, one moment.

**Only PENDING is writable from here.** `status` is not a parameter and there is no update method.
The verdict is read from `status` every time, and the one-decision rule permits exactly one `pending
→ decided` transition, under a row lock. That transition lives in a different class in a different
module — `core.approvals.decisions.SQLActionDecisionRepository`, reached only by a cookie POST from
a NOA-origin document. A repository that could write `APPROVED` would put a second door on the
authorization next to the one the cookie/CSRF boundary names, and the MCP path — the one an LLM can
reach — would be holding the key to it. The split is asserted, not just described:
`test_action_request_decisions_live.py` pins this class's public surface to `create_pending` and
`commit`.

**This repository owns its session and commits**, unlike `SQLServerRepository` and
`SQLAuthorizationRepository`, which flush into a caller's transaction. Same split and the
same reason as `SQLToolRunRepository`: the MCP tool path runs outside FastAPI's
dependency graph, so there is no request transaction to join, and a pending request that
rolls back with the call that opened it is a request an operator will never see. `commit()`
is on the Protocol for that reason, so a double has to acknowledge the boundary rather than
silently not have one.

Nothing here redacts and nothing here builds the context. `approval_context` arrives already
assembled and already redacted from the gate, which is the layer that knows what a tool
argument *is* (context persisted at gate time, never rebuilt from transcript) — the same division
`core.audit.tool_runs` keeps with `noa_api.mcp_audit`.

`expires_at` is a required parameter rather than a default computed here. The column is NOT
NULL by the table's design, and the TTL is configuration (`APPROVAL_PENDING_TTL_SECONDS`); a
repository that reached for settings on its own would be a second copy of the world (the reason
`redaction.py` was parked: a control with no caller is untested) and would make the deadline
unassertable from a test that did not also patch it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.db.lifecycle import ActionRequestStatus
from core.db.models import ActionRequest


class ActionRequestRepository(Protocol):
    """What the CHANGE gate needs to open a request."""

    async def create_pending(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        conversation_ref: str | None,
        approval_context: dict[str, Any],
        expires_at: datetime,
    ) -> UUID: ...

    async def commit(self) -> None: ...


class SQLActionRequestRepository:
    """`ActionRequestRepository` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        conversation_ref: str | None,
        approval_context: dict[str, Any],
        expires_at: datetime,
    ) -> UUID:
        """Insert the PENDING row and return its id.

        `status` is passed explicitly even though the column defaults to PENDING. The
        default is the schema's guarantee; this is the call site's, and a test that mutates
        one still fails against the other.

        `reason`, `decided_at` and `tool_run_id` are not set, here or anywhere in this class.
        They are what a *decision* writes, and the gate has not got one:
        the reason is typed by an operator on the approval card and is born at approve time,
        never at call time.

        Flushed rather than committed, so `commit()` stays the caller's decision — the gate
        makes that decision the point at which it is allowed to answer the model at all.
        """
        request = ActionRequest(
            tool_name=tool_name,
            requested_by_user_id=requested_by_user_id,
            status=ActionRequestStatus.PENDING,
            conversation_ref=conversation_ref,
            approval_context=approval_context,
            expires_at=expires_at,
        )
        self._session.add(request)
        await self._session.flush()
        return request.id

    async def commit(self) -> None:
        """Make the pending INSERT durable. See the module docstring."""
        await self._session.commit()
