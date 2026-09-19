"""SQL behind the `tool_runs` audit trail.

The schema landed and wrote nothing to it. This is the writer, and it has four callers:
`noa_api.mcp_audit` on the READ path; `core.approvals.decisions`, opening the `STARTED` row an
approval authorises inside the decision's own transaction; `core.approvals.execution`,
moving that row to a terminal state once the change has run; and `core.approvals.reaper`,
moving one nobody ever finished. Each composes this class on its own session — one writer per
table, four transactions, because they are four different moments.

**Two statements, two transactions, on purpose.** `start_run` inserts a `STARTED` row and
commits *before* the tool body runs; `finish_run` moves it to `COMPLETED` or `FAILED` after.
One transaction spanning the call would make the row appear only once the call ended, so a
process that died mid-call would leave nothing — which is the exact case `STARTED` exists
for (the schema's model docstring; the reaper sweeps them).

**This repository owns its session and commits**, unlike `SQLServerRepository` and
`SQLAuthorizationRepository`, which flush into a caller's transaction. Same split, same
reason, as `SQLMcpIdentityRepository`: the MCP tool path runs outside FastAPI's
dependency graph, so there is no request transaction to join, and an audit row that rolls
back with the thing it was recording is not an audit row. `commit()` is on the Protocol for
that reason, so a double has to acknowledge the boundary rather than silently not have one.

Nothing here redacts. `args` and `result_summary` arrive already redacted from
`noa_api.mcp_audit`, which is the layer that knows what a tool argument *is* — a redactor
sitting under the SQL would be a second, quieter place for the rule to live and the
one an audit reader would have to trust without seeing.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.clock import now_utc
from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.db.models import ToolRun


class ToolRunRepository(Protocol):
    """What the tool path needs to record a run."""

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
        """Insert the `STARTED` row and return its id.

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
        """Move a run to its terminal state and stamp `completed_at`.

        A bare `UPDATE` rather than load-mutate-save: the row was written by this same
        request moments ago and nothing else touches it, so reading it back only to write
        it again would be a round trip that can fail on its own. `completed_at` comes from
        `core.clock.now_utc`, the same clock the `created_at` column default reads, so the
        difference between the two is a duration rather than a duration plus whatever the API
        host and the database host disagree by; unlike a second SQL `now()` it is also pinnable
        in a test.

        **`status = STARTED` is in the predicate: the first terminal write wins.** Two of
        the four callers can reach one run — the approved-change executor, finishing a change
        that ran long, and the reaper, calling the same run abandoned past its deadline — and
        without this the second one silently overwrites the first. Both directions produce a
        lie: a change that completed re-written as "outcome never observed", or a reaped run
        flipped to `COMPLETED` beside the abandonment receipt `create_if_missing` already
        made permanent. The predicate is re-evaluated by the `UPDATE` against the newest
        committed row version, so it closes the window between the reaper's `SELECT` and its
        write as well as the plain interleaving.
        """
        await self._session.execute(
            update(ToolRun)
            .where(ToolRun.id == tool_run_id, ToolRun.status == ToolRunStatus.STARTED)
            .values(
                status=status,
                result_summary=result_summary,
                completed_at=now_utc(),
            )
        )

    async def commit(self) -> None:
        """Make the pending statement durable. See the module docstring."""
        await self._session.commit()
