"""Doubles for the expiry sweep and the check-on-read.

`test_action_request_expiry_live.py` runs `SQLActionRequestExpiryRepository` against a real
Postgres, because the predicate, the `RETURNING` clause and the "an `UPDATE` re-checks its
`WHERE` after waiting for a lock" behaviour are all claims *about the database*. What these
doubles serve is the other half — the loop around it: that it sleeps before its first pass,
that a failing pass does not end it, that every pass gets its own session, and that stopping
the app stops it. None of that needs SQL, and a version that needed Docker would be a check
that quietly stops running.

Same split, and for the same reason, as `support.action_decisions` beside
`test_action_request_decisions_live.py`.

**The journal is the point**, again. Every double appends to one shared list, so a test can
assert the order things happened in — `["session:open", "expire", "commit", "session:close"]`
is what pins "a pass commits inside the session it opened" rather than merely "a pass
committed".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from core.db.lifecycle import ActionRequestStatus


@dataclass
class FakeActionRequestRow:
    """The columns an expiry reads and writes. Not an ORM instance, for the reason
    `support.action_decisions` gives: a real `ActionRequest` carries its server defaults as
    `None` until a flush, so an assertion on `status` would be testing the double's gaps."""

    action_request_id: UUID
    expires_at: datetime
    status: ActionRequestStatus = ActionRequestStatus.PENDING
    decided_at: datetime | None = None
    reason: str | None = None


def pending_row(
    *,
    expires_in_seconds: float = 3600,
    now: datetime | None = None,
    status: ActionRequestStatus = ActionRequestStatus.PENDING,
) -> FakeActionRequestRow:
    """A row due (`expires_in_seconds` negative) or not (positive)."""
    moment = now or datetime.now(UTC)
    return FakeActionRequestRow(
        action_request_id=uuid4(),
        expires_at=moment + timedelta(seconds=expires_in_seconds),
        status=status,
    )


class FakeActionRequestExpiryRepository:
    """In-memory `ActionRequestExpiryRepository`, applying the production predicate.

    The predicate is duplicated here on purpose and it is *not* the thing under test: the
    live file asserts the SQL. What this one buys is a repository the loop can drive without
    a database while still behaving like one — a pass that expires two rows must be able to
    report two ids.
    """

    def __init__(self, journal: list[str] | None = None) -> None:
        self.rows: dict[UUID, FakeActionRequestRow] = {}
        self.journal = journal if journal is not None else []
        # One entry per `expire_due`, holding the moment it judged rows against, so a test can
        # assert the service passed *its* clock read down rather than taking a second one.
        self.judged_at: list[datetime] = []
        self.commits = 0
        self.fail: BaseException | None = None

    def add(self, row: FakeActionRequestRow) -> FakeActionRequestRow:
        self.rows[row.action_request_id] = row
        return row

    async def expire_due(
        self,
        *,
        now: datetime,
        action_request_id: UUID | None = None,
    ) -> tuple[UUID, ...]:
        self.journal.append("expire")
        self.judged_at.append(now)
        if self.fail is not None:
            raise self.fail

        expired: list[UUID] = []
        for row in self.rows.values():
            if action_request_id is not None and row.action_request_id != action_request_id:
                continue
            if row.status is not ActionRequestStatus.PENDING or row.expires_at > now:
                continue
            row.status = ActionRequestStatus.EXPIRED
            row.decided_at = now
            row.reason = None
            expired.append(row.action_request_id)
        return tuple(expired)

    async def commit(self) -> None:
        self.journal.append("commit")
        self.commits += 1


@dataclass
class FakeSession:
    """Stands in for the `AsyncSession` a pass opens. Carries only its ordinal, which is
    what "a fresh one per pass" is asserted against."""

    ordinal: int


@dataclass
class RecordingSessionFactory:
    """A `SessionFactory` that hands out a new `FakeSession` per call and says so.

    `opened` counts calls; `closed` counts exits, so a pass that leaked its session is
    visible rather than merely absent from the journal.
    """

    journal: list[str] = field(default_factory=list)
    opened: list[FakeSession] = field(default_factory=list)
    closed: list[FakeSession] = field(default_factory=list)
    fail: BaseException | None = None

    def __call__(self) -> AbstractAsyncContextManager[FakeSession]:
        @asynccontextmanager
        async def _session() -> AsyncIterator[FakeSession]:
            if self.fail is not None:
                raise self.fail
            session = FakeSession(ordinal=len(self.opened))
            self.opened.append(session)
            self.journal.append("session:open")
            try:
                yield session
            finally:
                self.closed.append(session)
                self.journal.append("session:close")

        return _session()


__all__ = [
    "FakeActionRequestExpiryRepository",
    "FakeActionRequestRow",
    "FakeSession",
    "RecordingSessionFactory",
    "pending_row",
]
