"""In-memory `tool_runs` writer for the audit tests (T73).

The double records the rows a real `SQLToolRunRepository` would have written, so a test can
assert on the audit trail without Postgres — and, more usefully, can make either write fail
on demand. Both failure modes are behaviours V45 pins and neither is reachable against a
healthy database: the opening write failing means the call is refused, the closing one
failing means the row is left `STARTED` for T38's reaper.

`SQLToolRunRepository` is not doubled away entirely — `test_mcp_tool_audit.py` runs it
against a scratch Postgres, because "the middleware called a repository" and "a row exists
in `tool_runs`" are different claims and only the second one is V45. Same split as
`support.servers` and `support.rbac`.

Rows are a dataclass rather than `ToolRun` instances: the ORM object would carry
server-defaulted columns as `None` until a flush, so an assertion on `created_at` would be
asserting against the double's own gaps. What matters here is what the *caller* passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from core.db.lifecycle import ToolRisk, ToolRunStatus


@dataclass
class RecordedRun:
    """One row as the middleware asked for it."""

    tool_run_id: UUID
    tool_name: str
    requested_by_user_id: UUID
    risk: ToolRisk
    conversation_ref: str | None
    args: dict[str, Any]
    status: ToolRunStatus = ToolRunStatus.STARTED
    result_summary: str | None = None
    committed: int = 0


class FakeToolRunRepository:
    """In-memory `ToolRunRepository`, with both writes independently breakable."""

    def __init__(self) -> None:
        self.runs: list[RecordedRun] = []
        # Set to raise from the matching write. `RuntimeError` rather than a driver error:
        # the middleware catches `Exception` on purpose (every way a write fails ends in
        # "no audit row"), and a test that used asyncpg's own class would be asserting
        # against the driver rather than against that decision.
        self.fail_start: BaseException | None = None
        self.fail_finish: BaseException | None = None
        # One entry per committed statement, in order, holding the status it made durable.
        # `["STARTED", "COMPLETED"]` is the evidence that the opening row was committed
        # before the tool ran rather than batched with the terminal write.
        self.commits: list[str] = []
        self._pending: list[RecordedRun] = []

    # --- `ToolRunRepository` ---

    async def start_run(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        risk: ToolRisk,
        conversation_ref: str | None,
        args: dict[str, Any],
    ) -> UUID:
        if self.fail_start is not None:
            raise self.fail_start
        run = RecordedRun(
            tool_run_id=uuid4(),
            tool_name=tool_name,
            requested_by_user_id=requested_by_user_id,
            risk=risk,
            conversation_ref=conversation_ref,
            args=args,
        )
        self.runs.append(run)
        self._pending.append(run)
        return run.tool_run_id

    async def finish_run(
        self,
        *,
        tool_run_id: UUID,
        status: ToolRunStatus,
        result_summary: str | None,
    ) -> None:
        if self.fail_finish is not None:
            raise self.fail_finish
        run = self.get(tool_run_id)
        run.status = status
        run.result_summary = result_summary
        self._pending.append(run)

    async def commit(self) -> None:
        for run in self._pending:
            run.committed += 1
            self.commits.append(run.status.value)
        self._pending.clear()

    # --- Assertions ---

    @property
    def only(self) -> RecordedRun:
        """The single recorded run, asserting there is exactly one."""
        assert len(self.runs) == 1, f"expected one recorded run, got {len(self.runs)}"
        return self.runs[0]

    def get(self, tool_run_id: UUID) -> RecordedRun:
        for run in self.runs:
            if run.tool_run_id == tool_run_id:
                return run
        raise AssertionError(f"no recorded run {tool_run_id}")


__all__ = [
    "FakeToolRunRepository",
    "RecordedRun",
]
