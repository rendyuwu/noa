"""Doubles and an app builder for the decision routes (T37).

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
decisions at once: the lock precedes every guard (V28), the run is inserted before the
decision commits so both land together (V29, V46), and the executor is handed the run only
after that commit — a handoff before it could start a change whose authorization then rolled
back.

Rows are dataclasses, not ORM instances, for the reason `support.tool_runs` gives: an
`ActionRequest` would carry its server-defaulted columns as `None` until a flush, so an
assertion on `status` would be asserting against the double's own gaps rather than against
what the caller asked for.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from core.approvals.csrf import mint_decision_csrf_token
from core.approvals.decisions import (
    ActionDecisionService,
    LockedActionRequest,
)
from core.auth.jwt_service import JWTService
from core.config import Settings
from core.db.lifecycle import ActionRequestStatus
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

# The first CHANGE tool (T22). Named rather than built: these tests are about the decision,
# and a tool would only be a second thing that could be wrong.
CHANGE_TOOL = "whm_suspend_account"

# What an operator types into the card's one reason box (C8, V15).
REASON = "Customer confirmed the account is compromised; suspending per ticket NOC-4471."

# What LibreChat fills from `{{LIBRECHAT_BODY_CONVERSATIONID}}` (T57, R28).
CONVERSATION_ID = "1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12"

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
    """The `tool_runs` row an approval started (V46, V47)."""

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

    def add(self, request: LockedActionRequest) -> LockedActionRequest:
        self.rows[request.action_request_id] = request
        return request

    # --- `ActionDecisionRepository` ---

    async def lock_for_decision(self, *, action_request_id: UUID) -> LockedActionRequest | None:
        self.locks.append(action_request_id)
        self.journal.append("lock")
        return self.rows.get(action_request_id)

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
    """One handoff to the executor (V29)."""

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
        """A second active operator, for the requester-match cases (V27)."""
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
    `users.is_active` re-read behind `require_session_user` is production code here (V6), and
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
    # the error classes are all production code.
    app.dependency_overrides[get_action_decision_service] = lambda: ActionDecisionService(
        repository=repository,
        executor=executor,
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


__all__ = [
    "APPROVAL_CONTEXT",
    "CHANGE_TOOL",
    "CONVERSATION_ID",
    "REASON",
    "DecisionHarness",
    "FakeActionDecisionRepository",
    "RecordedChangeRun",
    "RecordedDecision",
    "RecordingApprovedChangeExecutor",
    "StartedExecution",
    "decision_harness",
    "locked_request",
]
