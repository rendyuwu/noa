"""Doubles and an app builder for the decision routes.

Postgres is not required for a route test here: `decision_harness` builds an app with the
`/action-requests` router, points `get_action_decision_service` at an in-memory repository and
`get_approved_change_executor` at a recorder, and leaves everything else — the router, the
error handler, the real `JWTService`, the real `AuthService` behind `require_session_user`,
the real `ActionDecisionService`, and the real CSRF mint and verify — as production code.

`SQLActionDecisionRepository` is not doubled away entirely. `test_action_request_decisions_live.py`
runs it against a scratch Postgres, because "the service called a repository" and "exactly one
`pending → decided` transition happened under a row lock" are different claims and only the
second one is V28. Same split as `support.action_requests`, `support.tool_runs` and
`support.rbac`.

**The journal is the point of this module.** Every double appends to one shared list, so a
test can assert the *order* the service did things in rather than only the fact that it did
them. `["lock", "run", "decision:APPROVED", "commit", "execute"]` is what pins three separate
decisions at once: the lock precedes every guard, the run is inserted before the
decision commits so both land together, and the executor is handed the run only
after that commit — a handoff before it could start a change whose authorization then rolled
back.

Rows are dataclasses, not ORM instances, for the reason `support.tool_runs` gives: an
`ActionRequest` would carry its server-defaulted columns as `None` until a flush, so an
assertion on `status` would be asserting against the double's own gaps rather than against
what the caller asked for.

**The live helpers at the bottom are not doubles.** `insert_user`, `open_request`,
`read_request`, `read_runs` and `ObservedDecisionRepository` run against a real Postgres and
live here because several files need them: the three the decision's live coverage is
split across — `test_action_request_decisions_live.py` (T37, the row lock and the refusals),
`test_action_request_decision_records_live.py` (what a decision writes) and
`test_action_request_change_cap_live.py` — plus `test_action_request_expiry_live.py`
(T39). That last one races a sweep against an approval, which needs the same "hold the
transaction open between its locked read and its commit" instrument the others use — and a
second copy of that instrument is a second thing that can silently stop overlapping, which is
exactly the failure V89 exists to name.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.approvals.csrf import mint_decision_csrf_token
from core.approvals.decisions import (
    ActionDecisionRepository,
    ActionDecisionService,
    ApprovedChangeExecutor,
    LockedActionRequest,
    SQLActionDecisionRepository,
)
from core.approvals.repository import SQLActionRequestRepository
from core.auth.jwt_service import JWTService
from core.config import Settings
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import ActionReceipt, ActionRequest, ToolRun, User
from noa_api.api.deps import (
    STATE_APPROVED_CHANGE_EXECUTOR,
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
    get_action_decision_service,
    get_auth_service,
)
from noa_api.api.errors import install_error_handling
from noa_api.api.routes.action_requests import router as action_requests_router
from support.auth import (
    COOKIE_NAME,
    OPERATOR_EMAIL,
    FakeAuthRepository,
    FakeUserRow,
    build_settings,
    override_auth_service_factory,
)

# The first CHANGE tool. Named rather than built: these tests are about the decision,
# and a tool would only be a second thing that could be wrong.
CHANGE_TOOL = "whm_suspend_account"

# What an operator types into the card's one reason box.
REASON = "Customer confirmed the account is compromised; suspending per ticket NOC-4471."

# What LibreChat fills from `{{LIBRECHAT_BODY_CONVERSATIONID}}`.
CONVERSATION_ID = "1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12"

# V31's cap for a support-built service. Deliberately not `Settings`' own default of 1:
# four live files approve a request or two and none of them is about the cap, so a helper
# carrying the production number would make every one of them a cap test by accident — and a
# helper that agreed with the default would be unseparable from one that hardcoded it (T33(e)'s
# argument for a 900-second test TTL). A test that *is* about the cap passes its own number.
SUPPORT_MAX_INFLIGHT_PER_USER = 8

# The gate's `approval_context` shape (T33's `build_approval_context`), already redacted.
APPROVAL_CONTEXT: dict[str, Any] = {
    "arguments": {"server_ref": "alpha", "account": "acmeco"},
    "requester": {"email": OPERATOR_EMAIL, "librechat_user_id": "librechat-user-1"},
    "evidence": {"account": "acmeco", "suspended": False, "domain": "acme.example"},
}


def locked_request(
    *,
    action_request_id: UUID | None = None,
    requested_by_user_id: UUID,
    status: ActionRequestStatus = ActionRequestStatus.PENDING,
    expires_in_seconds: int = 3600,
    now: datetime | None = None,
    approval_context: dict[str, Any] | None = None,
) -> LockedActionRequest:
    """A pending request as `lock_for_decision` would return it.

    `expires_in_seconds` may be negative — that is how a test reaches V32's check-on-read
    without waiting for a TTL.
    """
    moment = now or datetime.now(UTC)
    return LockedActionRequest(
        action_request_id=action_request_id or uuid4(),
        requested_by_user_id=requested_by_user_id,
        status=status,
        tool_name=CHANGE_TOOL,
        conversation_ref=CONVERSATION_ID,
        approval_context=APPROVAL_CONTEXT if approval_context is None else approval_context,
        expires_at=moment + timedelta(seconds=expires_in_seconds),
    )


@dataclass
class RecordedDecision:
    """One terminal write as the service asked for it."""

    action_request_id: UUID
    status: ActionRequestStatus
    reason: str | None
    decided_at: datetime
    tool_run_id: UUID | None
    committed: int = 0


@dataclass
class RecordedChangeRun:
    """The `tool_runs` row an approval started."""

    tool_run_id: UUID
    tool_name: str
    requested_by_user_id: UUID
    conversation_ref: str | None
    args: dict[str, Any]


class FakeActionDecisionRepository:
    """In-memory `ActionDecisionRepository` over rows the test seeds."""

    def __init__(self, journal: list[str] | None = None) -> None:
        self.rows: dict[UUID, LockedActionRequest] = {}
        self.locks: list[UUID] = []
        self.runs: list[RecordedChangeRun] = []
        self.decisions: list[RecordedDecision] = []
        # One entry per committed statement, holding the status it made durable. A service
        # that wrote without committing would leave this empty while `decisions` looked right.
        self.commits: list[str] = []
        self.journal = journal if journal is not None else []
        self._pending: list[RecordedDecision] = []
        # V31's count, as this double reports it. Per user rather than a single number,
        # so a cap test can put one operator at their limit and leave another free — which is
        # what proves the count is scoped by requester and not global.
        self.inflight_by_user: dict[UUID, int] = {}

    def add(self, request: LockedActionRequest) -> LockedActionRequest:
        self.rows[request.action_request_id] = request
        return request

    def set_inflight(self, user_id: UUID, count: int) -> None:
        """Report `count` in-flight CHANGE runs for `user_id`."""
        self.inflight_by_user[user_id] = count

    # --- `ActionDecisionRepository` ---

    async def lock_for_decision(self, *, action_request_id: UUID) -> LockedActionRequest | None:
        self.locks.append(action_request_id)
        self.journal.append("lock")
        return self.rows.get(action_request_id)

    async def inflight_changes_under_user_lock(self, *, requested_by_user_id: UUID) -> int:
        # Journalled like every other call, because *when* the count is taken is the claim:
        # after the lock and the guards, and before the run that would change the answer.
        self.journal.append("inflight")
        return self.inflight_by_user.get(requested_by_user_id, 0)

    async def start_change_run(
        self,
        *,
        tool_name: str,
        requested_by_user_id: UUID,
        conversation_ref: str | None,
        args: dict[str, Any],
    ) -> UUID:
        self.journal.append("run")
        run = RecordedChangeRun(
            tool_run_id=uuid4(),
            tool_name=tool_name,
            requested_by_user_id=requested_by_user_id,
            conversation_ref=conversation_ref,
            args=args,
        )
        self.runs.append(run)
        return run.tool_run_id

    async def write_decision(
        self,
        *,
        action_request_id: UUID,
        status: ActionRequestStatus,
        reason: str | None,
        decided_at: datetime,
        tool_run_id: UUID | None,
    ) -> None:
        self.journal.append(f"decision:{status.value}")
        decision = RecordedDecision(
            action_request_id=action_request_id,
            status=status,
            reason=reason,
            decided_at=decided_at,
            tool_run_id=tool_run_id,
        )
        self.decisions.append(decision)
        self._pending.append(decision)

    async def commit(self) -> None:
        self.journal.append("commit")
        for decision in self._pending:
            decision.committed += 1
            self.commits.append(decision.status.value)
        self._pending.clear()

    # --- Assertions ---

    @property
    def only_decision(self) -> RecordedDecision:
        assert len(self.decisions) == 1, f"expected one decision, got {len(self.decisions)}"
        return self.decisions[0]

    @property
    def only_run(self) -> RecordedChangeRun:
        assert len(self.runs) == 1, f"expected one started run, got {len(self.runs)}"
        return self.runs[0]


@dataclass
class StartedExecution:
    """One handoff to the executor."""

    tool_run_id: UUID
    action_request_id: UUID


class RecordingApprovedChangeExecutor:
    """In-memory `ApprovedChangeExecutor`, with the handoff breakable.

    `fail` exists because the swallow is a decision, not an oversight: the approval is
    already committed by the time `start` runs, so raising would answer 500 for a change that
    *is* authorised and recorded (see `ActionDecisionService._hand_off`).
    """

    def __init__(self, journal: list[str] | None = None) -> None:
        self.started: list[StartedExecution] = []
        self.fail: BaseException | None = None
        self.journal = journal if journal is not None else []

    async def start(self, *, tool_run_id: UUID, action_request_id: UUID) -> None:
        self.journal.append("execute")
        self.started.append(
            StartedExecution(tool_run_id=tool_run_id, action_request_id=action_request_id)
        )
        if self.fail is not None:
            raise self.fail

    @property
    def only(self) -> StartedExecution:
        assert len(self.started) == 1, f"expected one handoff, got {len(self.started)}"
        return self.started[0]


def build_decision_service(
    repository: ActionDecisionRepository,
    *,
    executor: ApprovedChangeExecutor | None = None,
    max_inflight_per_user: int = SUPPORT_MAX_INFLIGHT_PER_USER,
) -> ActionDecisionService:
    """The production service over whatever repository a test hands it.

    One construction site for nine call sites. It exists because T38 made V31's cap a
    required argument: nine copies of the constructor meant nine places to decide what the cap
    is, and eight of them are in files that have nothing to say about it.

    `executor` defaults to a fresh recorder — a service built to *deny* still needs one, and a
    test that never inspects it should not have to name it.
    """
    return ActionDecisionService(
        repository=repository,
        executor=executor or RecordingApprovedChangeExecutor(),
        max_inflight_per_user=max_inflight_per_user,
    )


def build_live_decision_service(
    session: AsyncSession,
    *,
    executor: RecordingApprovedChangeExecutor | None = None,
    max_inflight_per_user: int = SUPPORT_MAX_INFLIGHT_PER_USER,
) -> tuple[ActionDecisionService, RecordingApprovedChangeExecutor]:
    """The production service over the production repository, and the recorder it hands off to.

    Only the executor is doubled: the SQL, the lock, the guards, the ordering and the error
    classes are all production code — which is what makes these files claims about V28 and V29
    rather than about a service calling a repository.

    Both `*_live` files that race the two writers had this verbatim; it landed here when
    T38's cap argument made it two places to decide what a cap is.
    """
    recorder = executor or RecordingApprovedChangeExecutor()
    return (
        build_decision_service(
            SQLActionDecisionRepository(session),
            executor=recorder,
            max_inflight_per_user=max_inflight_per_user,
        ),
        recorder,
    )


@dataclass
class DecisionHarness:
    """Everything a decision-route test pokes at, so assertions read off one object."""

    client: TestClient
    app: FastAPI
    settings: Settings
    jwt_service: JWTService
    repository: FakeActionDecisionRepository
    executor: RecordingApprovedChangeExecutor
    auth_repository: FakeAuthRepository
    operator: FakeUserRow
    journal: list[str] = field(default_factory=list)

    # --- Session ---

    def sign_in(self, user: FakeUserRow | None = None) -> None:
        """Put a valid session cookie on the client, without going through login."""
        subject = user or self.operator
        issued = self.jwt_service.create_access_token(email=subject.email, user_id=subject.id)
        self.client.cookies.set(COOKIE_NAME, issued.token)

    def sign_out(self) -> None:
        self.client.cookies.delete(COOKIE_NAME)

    def add_operator(self, email: str) -> FakeUserRow:
        """A second active operator, for the requester-match cases."""
        return self.auth_repository.add_active_user(email)

    # --- Requests ---

    def csrf_for(
        self,
        action_request_id: UUID,
        *,
        user_id: UUID | None = None,
        issued_at: datetime | None = None,
    ) -> str:
        """A token the production minter produced — never a hand-built string."""
        return mint_decision_csrf_token(
            settings=self.settings,
            user_id=user_id or self.operator.id,
            action_request_id=action_request_id,
            issued_at=issued_at,
        )

    def approve(
        self,
        action_request_id: UUID,
        *,
        reason: str = REASON,
        csrf: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> Response:
        return self._post("approve", action_request_id, reason=reason, csrf=csrf, body=body)

    def deny(
        self,
        action_request_id: UUID,
        *,
        reason: str = REASON,
        csrf: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> Response:
        return self._post("deny", action_request_id, reason=reason, csrf=csrf, body=body)

    def _post(
        self,
        action: str,
        action_request_id: UUID,
        *,
        reason: str,
        csrf: str | None,
        body: dict[str, Any] | None,
    ) -> Response:
        payload = (
            body
            if body is not None
            else {
                "reason": reason,
                "csrf": csrf if csrf is not None else self.csrf_for(action_request_id),
            }
        )
        return self.client.post(f"/action-requests/{action_request_id}/{action}", json=payload)


@contextmanager
def decision_harness(
    *,
    settings: Settings | None = None,
) -> Iterator[DecisionHarness]:
    """An app with only the decision routes, wired to in-memory doubles.

    Two dependency overrides, and no more: the repository and the executor. `AuthService` is
    overridden too, but with `support.auth`'s own factory over a real `AuthService` — the
    `users.is_active` re-read behind `require_session_user` is production code here, and
    the routes' 401 path depends on it.
    """
    resolved_settings = settings or build_settings()
    jwt_service = JWTService(resolved_settings)

    journal: list[str] = []
    repository = FakeActionDecisionRepository(journal)
    executor = RecordingApprovedChangeExecutor(journal)

    auth_repository = FakeAuthRepository()
    operator = auth_repository.add_active_user(OPERATOR_EMAIL)

    app = FastAPI()
    install_error_handling(app)
    app.include_router(action_requests_router)

    # The same attributes `noa_api.main.lifespan` writes, minus the engine no test here needs.
    setattr(app.state, STATE_SETTINGS, resolved_settings)
    setattr(app.state, STATE_JWT_SERVICE, jwt_service)
    setattr(app.state, STATE_LDAP_SERVICE, None)
    setattr(app.state, STATE_SESSION_FACTORY, None)
    setattr(app.state, STATE_APPROVED_CHANGE_EXECUTOR, executor)

    app.dependency_overrides[get_auth_service] = override_auth_service_factory(
        settings=resolved_settings,
        repository=auth_repository,
        jwt_service=jwt_service,
    )
    # The real `ActionDecisionService` over a fake repository: the ordering, the guards and
    # the error classes are all production code. V31's cap comes off *this harness's* settings,
    # the way `get_action_decision_service` reads it off the app's — so a cap test sets it with
    # `build_settings(approval_max_inflight_per_user=...)` and there is one source either way.
    app.dependency_overrides[get_action_decision_service] = lambda: build_decision_service(
        repository,
        executor=executor,
        max_inflight_per_user=resolved_settings.approval_max_inflight_per_user,
    )

    with TestClient(app) as client:
        yield DecisionHarness(
            client=client,
            app=app,
            settings=resolved_settings,
            jwt_service=jwt_service,
            repository=repository,
            executor=executor,
            auth_repository=auth_repository,
            operator=operator,
            journal=journal,
        )


# --------------------------------------------------------------------------------------
# Live helpers — real Postgres, shared by the two `*_live` files
# --------------------------------------------------------------------------------------


async def insert_user(factory: async_sessionmaker[AsyncSession], email: str) -> UUID:
    """An active operator to hang requests off."""
    async with factory() as session:
        user = User(email=email, is_active=True)
        session.add(user)
        await session.commit()
        return user.id


async def open_request(
    factory: async_sessionmaker[AsyncSession],
    *,
    requested_by_user_id: UUID,
    expires_in_seconds: float = 3600,
    expires_at: datetime | None = None,
    approval_context: dict[str, Any] | None = None,
) -> UUID:
    """A PENDING row written by the *gate's* repository, not by hand.

    The row under test is the one the production writer produces, so a change to the gate's
    insert shows up in these files rather than being papered over by a fixture that agrees
    with the test instead of with the code.

    `expires_at` overrides `expires_in_seconds` and takes an absolute moment, which is how a
    test reaches the deadline *boundary* — "exactly now" is not expressible as an offset from
    a clock read that has already moved on.
    """
    deadline = expires_at or datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
    async with factory() as session:
        repository = SQLActionRequestRepository(session)
        action_request_id = await repository.create_pending(
            tool_name=CHANGE_TOOL,
            requested_by_user_id=requested_by_user_id,
            conversation_ref=CONVERSATION_ID,
            approval_context=APPROVAL_CONTEXT if approval_context is None else approval_context,
            expires_at=deadline,
        )
        await repository.commit()
        return action_request_id


async def read_request(
    factory: async_sessionmaker[AsyncSession], action_request_id: UUID
) -> ActionRequest:
    async with factory() as session:
        result = await session.execute(
            sa.select(ActionRequest).where(ActionRequest.id == action_request_id)
        )
        return result.scalar_one()


async def read_runs(factory: async_sessionmaker[AsyncSession]) -> list[ToolRun]:
    async with factory() as session:
        result = await session.execute(sa.select(ToolRun))
        return list(result.scalars())


async def read_receipts(factory: async_sessionmaker[AsyncSession]) -> list[ActionReceipt]:
    """Every `action_receipts` row.

    Unfiltered, like `read_runs` beside it: the claim these files make is usually about *how
    many* receipts exist, and a query that narrowed to one request could not see a second one
    written against another.
    """
    async with factory() as session:
        result = await session.execute(sa.select(ActionReceipt))
        return list(result.scalars())


class ObservedDecisionRepository(SQLActionDecisionRepository):
    """The production repository, with its two locks narrated and optionally held open.

    Four hooks and no substitutions: `before_read`/`after_read` fire around the row lock's
    `SELECT … FOR UPDATE`, `before_count`/`after_count` around V31's advisory lock and the
    count it holds open, and the journal records all of them plus the commit. The SQL under
    test is the real SQL — what is added is the ability to say *when* each statement happened
    relative to the other transaction's, which is the whole of V89's obligation (a): the window
    is held open on purpose rather than hoped for.

    Two locks and therefore two races, and they are different races. Two decisions on **one
    request** contend on the row; two approvals of **different requests by one operator** do not
    touch each other's rows at all, and contend only on the advisory key — which is why V31 needs
    its own overlap test rather than inheriting V28's.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        label: str,
        journal: list[str],
        before_read: Callable[[], Awaitable[None]] | None = None,
        after_read: Callable[[], Awaitable[None]] | None = None,
        before_count: Callable[[], Awaitable[None]] | None = None,
        after_count: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(session)
        self._label = label
        self._journal = journal
        self._before_read = before_read
        self._after_read = after_read
        self._before_count = before_count
        self._after_count = after_count

    async def lock_for_decision(self, *, action_request_id: UUID) -> LockedActionRequest | None:
        if self._before_read is not None:
            await self._before_read()
        self._journal.append(f"{self._label}:reading")
        result = await super().lock_for_decision(action_request_id=action_request_id)
        self._journal.append(f"{self._label}:read")
        if self._after_read is not None:
            await self._after_read()
        return result

    async def inflight_changes_under_user_lock(self, *, requested_by_user_id: UUID) -> int:
        if self._before_count is not None:
            await self._before_count()
        self._journal.append(f"{self._label}:counting")
        count = await super().inflight_changes_under_user_lock(
            requested_by_user_id=requested_by_user_id
        )
        self._journal.append(f"{self._label}:counted")
        if self._after_count is not None:
            await self._after_count()
        return count

    async def commit(self) -> None:
        await super().commit()
        self._journal.append(f"{self._label}:committed")


class UnlockedCountDecisionRepository(ObservedDecisionRepository):
    """`ObservedDecisionRepository` with V31's advisory lock removed and nothing else changed.

    The negative control's instrument. "The second count landed after the first commit" is
    worthless as an assertion if *every* count would land there — if the harness simply never
    overlapped the two transactions. This runs the identical handshake with a plain count in
    place of the locked one, which is the same shape
    `test_an_unlocked_read_of_the_same_row_does_not_wait` uses for V28's row lock.
    """

    async def inflight_changes_under_user_lock(self, *, requested_by_user_id: UUID) -> int:
        if self._before_count is not None:
            await self._before_count()
        self._journal.append(f"{self._label}:counting")
        result = await self._session.execute(
            sa.select(sa.func.count())
            .select_from(ToolRun)
            .where(
                ToolRun.requested_by_user_id == requested_by_user_id,
                ToolRun.risk == ToolRisk.CHANGE,
                ToolRun.status == ToolRunStatus.STARTED,
            )
        )
        self._journal.append(f"{self._label}:counted")
        if self._after_count is not None:
            await self._after_count()
        return int(result.scalar_one())


# How long the first transaction holds its lock after the second party has issued its
# statement. Only the *absence* of the lock needs this: without it the second statement
# returns at once, and this is the window in which it would do so and be recorded ahead of
# the first commit.
HANDOVER_GRACE_SECONDS = 0.25


__all__ = [
    "APPROVAL_CONTEXT",
    "CHANGE_TOOL",
    "CONVERSATION_ID",
    "HANDOVER_GRACE_SECONDS",
    "REASON",
    "SUPPORT_MAX_INFLIGHT_PER_USER",
    "DecisionHarness",
    "FakeActionDecisionRepository",
    "ObservedDecisionRepository",
    "RecordedChangeRun",
    "RecordedDecision",
    "RecordingApprovedChangeExecutor",
    "StartedExecution",
    "UnlockedCountDecisionRepository",
    "build_decision_service",
    "build_live_decision_service",
    "decision_harness",
    "insert_user",
    "locked_request",
    "open_request",
    "read_receipts",
    "read_request",
    "read_runs",
]
