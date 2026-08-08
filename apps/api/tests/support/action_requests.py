"""In-memory `action_requests` writer for the CHANGE gate tests (T33).

The double records the row a real `SQLActionRequestRepository` would have written, so a test
can assert on the pending request without Postgres — and can make the write fail on demand.
That failure is a behaviour V23 pins and it is not reachable against a healthy database: no
row means no authorization to read, so the gate refuses the change rather than letting it run
unrecorded.

`SQLActionRequestRepository` is not doubled away entirely — `test_mcp_change_gate.py` runs it
against a scratch Postgres, because "the gate called a repository" and "a PENDING row exists
in `action_requests`" are different claims and only the second one is what V23 reads. Same
split as `support.tool_runs`, `support.servers` and `support.rbac`.

The recorded row is a dataclass rather than an `ActionRequest` instance, for the reason
`support.tool_runs` gives: the ORM object carries its server-defaulted columns as `None`
until a flush, so an assertion on `status` would be asserting against the double's own gaps
rather than against what the caller asked for. The three columns a decision writes —
`reason`, `decided_at`, `tool_run_id` — are absent from this class on purpose: the gate has
no way to set them (`ActionRequestRepository` exposes none), and a double that offered a slot
for them would make the V22 assertion "the gate wrote no decision" pass by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from core.db.lifecycle import ActionRequestStatus


@dataclass
class RecordedRequest:
    """One `action_requests` row as the gate asked for it."""

    action_request_id: UUID
    tool_name: str
    requested_by_user_id: UUID
    status: ActionRequestStatus
    conversation_ref: str | None
    approval_context: dict[str, Any]
    expires_at: datetime
    committed: int = 0


class FakeActionRequestRepository:
    """In-memory `ActionRequestRepository`, with the write breakable."""

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        # Set to raise from `create_pending`. `RuntimeError` rather than a driver error: the
        # gate catches `Exception` on purpose (every way the INSERT fails ends in "no
        # authorization row"), and a test using asyncpg's own class would be asserting
        # against the driver rather than against that decision.
        self.fail_create: BaseException | None = None
        # One entry per committed statement, holding the status it made durable. A gate that
        # flushed without committing would leave this empty while `requests` looked right.
        self.commits: list[str] = []
        self._pending: list[RecordedRequest] = []

    # --- `ActionRequestRepository` ---

    async def create_pending(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        conversation_ref: str | None,
        approval_context: dict[str, Any],
        expires_at: datetime,
    ) -> UUID:
        if self.fail_create is not None:
            raise self.fail_create
        request = RecordedRequest(
            action_request_id=uuid4(),
            tool_name=tool_name,
            requested_by_user_id=requested_by_user_id,
            # Mirrors the production repository, which passes PENDING explicitly rather than
            # leaning on the column default (T34). A double that hardcoded some other status
            # would let a gate test pass against a row the schema would never hold.
            status=ActionRequestStatus.PENDING,
            conversation_ref=conversation_ref,
            approval_context=approval_context,
            expires_at=expires_at,
        )
        self.requests.append(request)
        self._pending.append(request)
        return request.action_request_id

    async def commit(self) -> None:
        for request in self._pending:
            request.committed += 1
            self.commits.append(request.status.value)
        self._pending.clear()

    # --- Assertions ---

    @property
    def only(self) -> RecordedRequest:
        """The single recorded request, asserting there is exactly one."""
        assert len(self.requests) == 1, f"expected one recorded request, got {len(self.requests)}"
        return self.requests[0]


__all__ = [
    "FakeActionRequestRepository",
    "RecordedRequest",
]
