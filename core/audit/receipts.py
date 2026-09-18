"""SQL behind `action_receipts` — what an approved CHANGE actually did.

The migration built the table and wrote nothing to it, naming this module as its only writer.
This is that writer, and it is one class for the whole table for the reason `core.audit.tool_runs`
gives: the audit trail has one writer per table, so a second insert written somewhere else
would be a second shape for the same record.

**Two callers, one row.** The executor writes the receipt for a change it ran; the stranded-run
reaper writes one for a change whose process died before it could. Both can reach the same
finished run — which is exactly why the table states `UNIQUE (action_request_id)` rather than
inheriting it
from `noa-old`'s primary key (`core.db.models.ActionReceipt`). `create_if_missing` is that
constraint used as the mechanism: `ON CONFLICT DO NOTHING` on the unique index, so whichever
writer arrives second is a no-op instead of a second answer to "what did this change do".

**It reports whether it wrote.** `None` means a receipt was already there. A caller that
cannot tell the difference would log "receipt written" for a row it did not write, and the
reaper's log line is the one an operator reads to find out what happened to a change nobody
was watching.

**No `commit`.** The session is the caller's, and both callers commit the receipt in the same
transaction as the terminal `tool_runs` write — a `COMPLETED` run whose receipt rolled back
would be a durable lie — a finished run with no record of what it did.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import ActionReceipt

# The unique index `create_if_missing` conflicts on. Named rather than inferred from a
# column list so a rename of the constraint fails here instead of silently turning the
# idempotent insert into a duplicate-key error at runtime.
RECEIPT_UNIQUE_CONSTRAINT = "uq_action_receipts_action_request_id"


class ActionReceiptRepository(Protocol):
    """The one write this table takes."""

    async def create_if_missing(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None: ...


class SQLActionReceiptRepository:
    """`ActionReceiptRepository` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_if_missing(
        self,
        *,
        action_request_id: UUID,
        tool_run_id: UUID | None,
        receipt_data: dict[str, Any],
    ) -> UUID | None:
        """Insert the receipt for this request, or leave the existing one alone.

        Returns the new row's id, or `None` when one was already recorded.

        `receipt_data` has no server default on the column, deliberately unlike
        `tool_runs.args` — an empty receipt is not a legitimate state, so a caller that
        omitted it would fail rather than record an outcome with nothing in it.
        """
        statement = (
            insert(ActionReceipt)
            .values(
                action_request_id=action_request_id,
                tool_run_id=tool_run_id,
                receipt_data=receipt_data,
            )
            .on_conflict_do_nothing(constraint=RECEIPT_UNIQUE_CONSTRAINT)
            .returning(ActionReceipt.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()
