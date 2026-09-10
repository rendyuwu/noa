"""Making a stale PENDING request terminal.

V32 wants two things, and T37 built one of them. The decision door already checks on read:
an operator who reaches a card past its deadline gets the row written `EXPIRED` under the
lock already held, then a 409. What is here is the other half — **the sweep**, which is
V32's terminality *without traffic*. Without it a request nobody ever opens stays `PENDING`
for as long as the database exists, and `action_requests.status` — the column V23 answers
"may this run?" from every time — has no truthful answer once the TTL has passed. And the
same mechanism serves the render paths, so a read cannot show a stale `PENDING` either: T63's
`noa_get_action_result` was its first live caller (`core.approvals.results`) and T41's approval
card is the second (`core.approvals.card`).

**Both render paths run this *after* their requester-matched read** (`core.approvals.reads`).
`expire_if_due` takes an id and no requester, so running it first would let an identifier the
caller cannot see be written to — and the ids reach an operator through a tool result that
persists in LibreChat's MongoDB, so "the caller supplied it" is not the same as "the
caller may see it". V32 allows a surface that resolves the row itself to call this first; T41's
card declined, and reading first costs only one poll of freshness (see `apply_due_expiry`).

**A third writer, and the reason is the same one that split the first two.**
`SQLActionRequestRepository` writes `PENDING` and nothing else, because the MCP tool
path holds it. `SQLActionDecisionRepository` writes `APPROVED`/`DENIED` and is reached
only by a cookie POST from a NOA-origin document. Both the sweeper and a render path
need a terminal write too, and neither may hold a writer that can set `APPROVED` — a loop
with no operator behind it, and a GET, are the last two places an authorization should be
grantable from. So this repository's only reachable terminal status is `EXPIRED`: the status
is not a parameter, and the predicate is part of the statement rather than the caller's to
supply.

**One predicate, one definition** — `status = PENDING AND expires_at <= now`. The sweep and
the check-on-read are the same `UPDATE` with an id added, because two spellings of "which
rows are due" is how a boundary ends up holding at one door and not the other.

**`<=`, matching the decision door.** `§T.39`'s line reads `expires_at < now()`; the door
compares `locked.expires_at <= decided_at`. A row exactly on its deadline has to be one
thing, and refusing it at the door while leaving it `PENDING` in the sweep is the shape of
disagreement V32 exists to remove.

**No `SELECT … FOR UPDATE` here, and that is not a weakening of V28.** A bare `UPDATE` takes
its own row locks, and under READ COMMITTED it re-evaluates its `WHERE` against the row
version that the transaction it waited for committed. So a sweep that collides with an
in-flight approval finds `status = APPROVED` when the lock is released and skips the row —
it cannot expire a change that has already been authorised and handed to an executor.
Adding an explicit lock would be a second mechanism to keep in step with the first for no
gain. `test_action_request_expiry_live.py` holds the approval open and issues the sweep
inside that window, with the negative control V89 requires.

**The loop must outlive its own failures.** `PendingExpirySweeper` catches per pass, because
a guarantee that ends the first time Postgres blinks is not a guarantee. It sleeps *before*
its first pass so that starting the app touches no database: `/health` has to answer with
Postgres down, and one interval of delay against an hour-long TTL costs nothing. Both
of those, and the two rules about stopping, now live in `core.tasks.periodic.PeriodicTask`
— hoisted at T38, whose reaper is the second component on the same loop, so the four
properties are proven once and inherited twice rather than copied.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.clock import now_utc
from core.db.lifecycle import ActionRequestStatus
from core.db.models import ActionRequest
from core.db.session import SessionFactory
from core.tasks.periodic import PeriodicTask

# The asyncio task's name, so a dump of running tasks says what this is.
SWEEP_TASK_NAME: Final = "noa-action-request-expiry-sweep"

# A pass that expired rows. Ids, not contents: what expired is an operator-facing fact that
# lives in the rows themselves (V8's spirit — the log names the request, never its context).
LOG_SWEEP_EXPIRED: Final = "action_requests_expired_by_sweep"

# A pass that raised. Logged and swallowed by the loop: the next pass is the remedy, and a
# sweeper that dies on a transient database error would leave V32 true only while nothing
# ever went wrong.
LOG_SWEEP_FAILED: Final = "action_request_expiry_sweep_failed"

# A render path found a request past its deadline and made it terminal (T63's result tool or
# T41's approval card).
LOG_EXPIRED_ON_READ: Final = "action_request_expired_on_render"

logger = structlog.get_logger(__name__)


class ActionRequestExpiryRepository(Protocol):
    """The one write this path may make, and the commit that makes it true."""

    async def expire_due(
        self,
        *,
        now: datetime,
        action_request_id: UUID | None = None,
    ) -> tuple[UUID, ...]: ...

    async def commit(self) -> None: ...


class SQLActionRequestExpiryRepository:
    """`ActionRequestExpiryRepository` over one `AsyncSession`.

    The session is the caller's: the sweeper opens one per pass and the render path joins the
    request's, exactly as `SQLActionDecisionRepository` does. `commit()` stays explicit for
    the same reason it is explicit there — the moment the expiry becomes durable is the moment
    it becomes the answer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def expire_due(
        self,
        *,
        now: datetime,
        action_request_id: UUID | None = None,
    ) -> tuple[UUID, ...]:
        """Move every due `PENDING` row to `EXPIRED`, and say which.

        `action_request_id` narrows the same statement to one row — that is the check-on-read
        the render path makes, and it is deliberately not a second query with a second
        spelling of the deadline.

        `reason` is written NULL rather than left alone. It is NULL on every `PENDING` row by
        construction today (only a decision writes one, and it writes a terminal status in the
        same statement), but T34's CHECK exempts `EXPIRED` precisely so an expiry *can* carry
        no reason — which means nothing at the database level would catch an `EXPIRED` row
        that carried one. This is what catches it. `tool_run_id` is left untouched by the same
        logic in reverse: no invariant says an expiry clears a link, and clearing one would be
        a destructive write with nothing behind it.

        `synchronize_session=False`: the rows are identified by the `RETURNING` clause, so
        there is nothing for the session's identity map to guess at.
        """
        statement = (
            update(ActionRequest)
            .where(
                ActionRequest.status == ActionRequestStatus.PENDING,
                ActionRequest.expires_at <= now,
            )
            .values(
                status=ActionRequestStatus.EXPIRED,
                decided_at=now,
                reason=None,
            )
            .returning(ActionRequest.id)
            .execution_options(synchronize_session=False)
        )
        if action_request_id is not None:
            statement = statement.where(ActionRequest.id == action_request_id)

        result = await self._session.execute(statement)
        return tuple(result.scalars())

    async def commit(self) -> None:
        """Make the expiries durable."""
        await self._session.commit()


class ActionRequestExpiryService:
    """Expire what is due — as a sweep, or for one request being read.

    Two entry points over one repository call, because they are the same event seen from
    different sides: nobody answered in time. Neither can take a status, so neither is a way
    to decide anything.
    """

    def __init__(self, repository: ActionRequestExpiryRepository) -> None:
        self._repository = repository

    async def sweep(self, *, now: datetime | None = None) -> tuple[UUID, ...]:
        """Expire every due request. Returns the ids, so a caller can log or count them."""
        moment = now_utc(now)
        expired = await self._repository.expire_due(now=moment)
        await self._repository.commit()

        if expired:
            logger.info(
                LOG_SWEEP_EXPIRED,
                count=len(expired),
                action_request_ids=[str(request_id) for request_id in expired],
            )
        return expired

    async def expire_if_due(
        self,
        *,
        action_request_id: UUID,
        now: datetime | None = None,
    ) -> bool:
        """Make one request terminal if its deadline has passed; report whether it did.

        Called around a render path's read of the row (T41's card, T63's result tool), so what
        that path serves is the row's real state rather than a `PENDING` nobody may act on any
        more. `False` covers both "still live" and "already terminal" — a caller that needs to
        tell those apart reads `status`, which is where V23 says the answer lives; a caller
        that ran this *after* its own read (T63, see the module docstring) knows `True` means
        the row is `EXPIRED` as of the moment it passed in.

        The commit runs whether or not anything matched. An empty `UPDATE` commits an empty
        transaction, which costs a round trip and keeps this method's contract — "the row is
        durable either way" — free of a branch that could get it wrong.
        """
        moment = now_utc(now)
        expired = await self._repository.expire_due(
            now=moment,
            action_request_id=action_request_id,
        )
        await self._repository.commit()

        if expired:
            logger.info(LOG_EXPIRED_ON_READ, action_request_id=str(action_request_id))
        return bool(expired)


class PendingExpirySweeper:
    """The background half of V32: an asyncio task that expires what nobody answered.

    In-process rather than a cron entry or a separate worker, matching V30's shape for the
    async host T38 built next door: one task, its own session per pass, started and stopped
    by the app lifespan that owns the engine it draws from.

    A pass gets its **own session**, not a long-lived one. A session held for the life of the
    process pins a connection to it and would answer every later sweep through whatever state
    that connection was left in.

    The loop is `core.tasks.periodic.PeriodicTask`, held rather than reimplemented.
    This class stays the thing the lifespan starts and stops, and `run_once` stays here for
    the reason it always was: the loop is what swallows a failure, so a caller driving a
    single pass sees it.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        interval_seconds: float,
        repository_factory: Callable[
            [AsyncSession], ActionRequestExpiryRepository
        ] = SQLActionRequestExpiryRepository,
    ) -> None:
        self._session_factory = session_factory
        # The same seam `McpToolContext` uses for every repository it builds per call: the
        # production default is the real one, and a test can drive the loop — its sleep, its
        # error handling, its one-session-per-pass rule — without a database, while the SQL
        # itself stays pinned against a real one.
        self._repository_factory = repository_factory
        self._loop = PeriodicTask(
            task_name=SWEEP_TASK_NAME,
            interval_seconds=interval_seconds,
            run_pass=self.run_once,
            failure_event=LOG_SWEEP_FAILED,
        )

    @property
    def running(self) -> bool:
        """Whether a sweep task is currently owned by this sweeper."""
        return self._loop.running

    async def start(self) -> None:
        """Begin sweeping. Idempotent — see `PeriodicTask.start`."""
        await self._loop.start()

    async def stop(self) -> None:
        """Cancel the loop and wait for it, so no pass outlives the engine it draws from."""
        await self._loop.stop()

    async def run_once(self) -> tuple[UUID, ...]:
        """One pass, in its own session and its own transaction. Raises on failure.

        The loop is what swallows; this does not, so a caller driving a single pass (a test,
        or an operator tool later) sees what went wrong.
        """
        async with self._session_factory() as session:
            service = ActionRequestExpiryService(self._repository_factory(session))
            return await service.sweep()


__all__ = [
    "LOG_EXPIRED_ON_READ",
    "LOG_SWEEP_EXPIRED",
    "LOG_SWEEP_FAILED",
    "SWEEP_TASK_NAME",
    "ActionRequestExpiryRepository",
    "ActionRequestExpiryService",
    "PendingExpirySweeper",
    "SQLActionRequestExpiryRepository",
]
