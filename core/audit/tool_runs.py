"""SQL behind the `tool_runs` audit trail (T73 — V20, V45, V47).

T35 built the table and wrote nothing to it. This is the writer. `noa_api.mcp_audit` is its
only caller on the READ path; `core.approvals.decisions` is the second, opening the `STARTED`
row an approval authorises inside the decision's own transaction (T37, V46), and T38's
executor will be the third when it moves that row to a terminal state.

**Two statements, two transactions, on purpose.** `start_run` inserts a `STARTED` row and
commits *before* the tool body runs; `finish_run` moves it to `COMPLETED` or `FAILED` after.
One transaction spanning the call would make the row appear only once the call ended, so a
process that died mid-call would leave nothing — which is the exact case `STARTED` exists
for (T35's model docstring; T38's reaper sweeps them).

**This repository owns its session and commits**, unlike `SQLWHMServerRepository` and
`SQLAuthorizationRepository`, which flush into a caller's transaction. Same split, same
reason, as `SQLMcpIdentityRepository` (T11): the MCP tool path runs outside FastAPI's
dependency graph, so there is no request transaction to join, and an audit row that rolls
back with the thing it was recording is not an audit row. `commit()` is on the Protocol for
that reason, so a double has to acknowledge the boundary rather than silently not have one.

Nothing here redacts. `args` and `result_summary` arrive already redacted from
`noa_api.mcp_audit`, which is the layer that knows what a tool argument *is* — a redactor
sitting under the SQL would be a second, quieter place for the rule to live (V66) and the
one an audit reader would have to trust without seeing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.db.models import ToolRun


class ToolRunRepository(Protocol):
    """What the tool path needs to record a run (V45, V47)."""

    async def start_run(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        risk: ToolRisk,
        conversation_ref: str | None,
        args: dict[str, Any],
    ) -> UUID: ...

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None: ...

    async def commit(self) -> None: ...


class SQLToolRunRepository:
    """`ToolRunRepository` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_run(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        risk: ToolRisk,
        conversation_ref: str | None,
        args: dict[str, Any],
    ) -> UUID:
        """Insert the `STARTED` row and return its id (V45).

        Flushed rather than committed here: `commit()` is a separate call so the caller
        decides when the row becomes durable, and `noa_api.mcp_audit` makes that decision
        the point at which it is allowed to run the tool (see there — the write is
        fail-closed).
        """
        run = ToolRun(
            tool_name=tool_name,
            requested_by_user_id=requested_by_user_id,
            risk=risk,
            status=ToolRunStatus.STARTED,
            conversation_ref=conversation_ref,
            args=args,
        )
        self._session.add(run)
        await self._session.flush()
        return run.id

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None:
        """Move a run to its terminal state and stamp `completed_at` (V47 timing).

        A bare `UPDATE` rather than load-mutate-save: the row was written by this same
        request moments ago and nothing else touches it, so reading it back only to write
        it again would be a round trip that can fail on its own. `completed_at` is set from
        the application clock, matching `created_at`'s server default closely enough for a
        duration and, unlike a second `now()`, provable in a test.
        """
        await self._session.execute(
            update(ToolRun)
            .where(ToolRun.id == tool_run_id)
            .values(
                status=status,
                result_summary=result_summary,
                completed_at=datetime.now(UTC),
            )
        )

    async def commit(self) -> None:
        """Make the pending statement durable. See the module docstring."""
        await self._session.commit()


__all__ = [
    "SQLToolRunRepository",
    "ToolRunRepository",
]
