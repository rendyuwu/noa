"""What the two requester-matched readers actually ask the database for (T42(b) — V17, V66, V76).

`core.approvals.reads` builds one statement for two surfaces: T41's approval card and T63's
`noa_get_action_result`. They guard the row identically and they project it differently, and one
of those differences is now a *join* rather than a projection — the card asks for
`action_receipts` and the model-facing reader does not.

**This file exists because the payload tests cannot see that difference.** `ActionResultView` has
no `receipt` field, so adding `include_receipt=True` to `SQLActionResultRepository` leaves every
assertion in `test_action_results_live.py` and `test_noa_tools_action_result.py` green: the row
would be fetched into the process that answers a model and then dropped by a dataclass that has
nowhere to put it. That was measured, not assumed — the mutation was run and the suite stayed
green. Which makes "the receipt is never read on this path" a claim held by prose unless
something asserts the statement, and V69 is explicit about what prose is worth.

So the compiled SQL is the assertion. No database: a `Select` compiles without a connection, and
what is being claimed is about the statement NOA builds, not about what Postgres does with it —
`test_approval_cards_live.py` owns the second question.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.card import SQLApprovalCardRepository
from core.approvals.results import SQLActionResultRepository


class _NoRows:
    """A result with nothing in it. Both readers answer `None` and stop."""

    def first(self) -> None:
        return None


class RecordingSession:
    """An `AsyncSession` stand-in that keeps the statement instead of running it."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _NoRows:
        self.statements.append(statement)
        return _NoRows()


def compiled(session: RecordingSession) -> str:
    """The one statement this session was handed, as PostgreSQL SQL.

    Named dialect rather than the default: the joins are what is being read, and compiling
    against the dialect NOA ships (C12) means this is the text the database would see.
    """
    assert len(session.statements) == 1
    return str(session.statements[0].compile(dialect=postgresql.dialect()))


async def read_as_card() -> str:
    session = RecordingSession()
    await SQLApprovalCardRepository(cast("AsyncSession", session)).get_for_requester(
        action_request_id=uuid4(),
        requester_user_id=uuid4(),
    )
    return compiled(session)


async def read_as_result() -> str:
    session = RecordingSession()
    await SQLActionResultRepository(cast("AsyncSession", session)).get_for_requester(
        action_request_id=uuid4(),
        requester_user_id=uuid4(),
    )
    return compiled(session)


async def test_the_cards_read_joins_the_receipt() -> None:
    """T42(b): the card renders what the change did, so its statement has to fetch it (V46)."""
    sql = await read_as_card()

    assert "action_receipts" in sql
    assert "tool_runs" in sql


async def test_the_models_read_does_not_join_the_receipt() -> None:
    """V17, V76: the before-state is never loaded on the path that answers into a transcript.

    A receipt carries the gate's in-process preflight one table over (T38 copies it there), and
    `ActionResultView` having no field for it is not the control — it is what makes the absence
    unassertable anywhere else. Here it is asserted.

    The `tool_runs` join is checked too, so this cannot pass because the reader stopped joining
    anything (V87): what a model may be told about an approved change still includes its run.
    """
    sql = await read_as_result()

    assert "action_receipts" not in sql
    assert "tool_runs" in sql


async def test_both_readers_carry_the_requester_match_in_the_where() -> None:
    """V27, V66: one access control, one spelling, and the receipt join does not dilute it.

    The card's statement gained a join in T42(b); this is the check that it gained only that.
    """
    for sql in (await read_as_card(), await read_as_result()):
        where = sql.split("WHERE", 1)
        assert len(where) == 2, sql
        assert "requested_by_user_id" in where[1]
