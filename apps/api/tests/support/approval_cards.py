"""Doubles and an app builder for the approval card's GET (T41).

Postgres is not required for a route test here, and the split is the one
`support.action_decisions` draws next door: `card_harness` builds an app with the
`/action-requests` router, points `get_approval_card_service` at an in-memory repository plus
`support.action_expiry`'s fake expiry writer, and leaves everything else — the router, the error
handler, the real `JWTService`, the real `AuthService` behind `require_session_user`, the real
`ApprovalCardService`, and the real CSRF **mint and verify** — as production code.

`SQLApprovalCardRepository` is not doubled away entirely: `test_approval_cards_live.py` runs it
against a scratch Postgres, because "the service called a repository" and "a row belonging to
another operator is never fetched, and one whose requester was deleted matches nobody" are
different claims and only the second is V27.

**The journal is the point.** The card repository appends `"read"` and the expiry double appends
`"expire"`/`"commit"` to one shared list, so a test can assert that a request which is not the
caller's produces `["read"]` and nothing else — the property that keeps an id an operator was
handed by a model (V26) from making NOA write to a stranger's row (V27, V32).

Stored values are `ApprovalCardView`s rather than ORM instances, for the reason
`support.action_decisions` gives: an `ActionRequest` carries its server-defaulted columns as
`None` until a flush, so an assertion on `status` would be asserting against the double's gaps.
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

from core.approvals.card import (
    ApprovalCardReceipt,
    ApprovalCardRequester,
    ApprovalCardService,
    ApprovalCardView,
)
from core.approvals.expiry import ActionRequestExpiryService
from core.approvals.reads import ActionRunView
from core.auth.jwt_service import JWTService
from core.config import Settings
from core.db.lifecycle import ActionRequestStatus, ToolRunStatus
from noa_api.api.deps import (
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
    get_approval_card_service,
    get_auth_service,
)
from noa_api.api.errors import install_error_handling
from noa_api.api.routes.action_requests import router as action_requests_router
from support.action_expiry import FakeActionRequestExpiryRepository, FakeActionRequestRow
from support.auth import (
    COOKIE_NAME,
    OPERATOR_EMAIL,
    FakeAuthRepository,
    FakeUserRow,
    build_settings,
    override_auth_service_factory,
)

# The first CHANGE tool (T22). Named rather than built: this is about the card.
CHANGE_TOOL = "whm_suspend_account"

# What LibreChat fills from `{{LIBRECHAT_BODY_CONVERSATIONID}}` (T57, R28).
CONVERSATION_ID = "1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12"

# The gate's redacted arguments (T33's `build_approval_context`).
ARGUMENTS: dict[str, Any] = {"server_ref": "alpha", "account": "acmeco"}

# The in-process preflight the CHANGE tool ran (C9, V17). The card shows this; the model never
# does (`core.approvals.results`), so it is a real value here rather than an empty dict — an
# assertion about absence needs something present to be absent.
EVIDENCE: dict[str, Any] = {"account": "acmeco", "suspended": False, "domain": "acme.example"}

# The after-state half of a receipt: what the runner answered, already redacted by the writer
# (T38's `build_receipt`). Deliberately shares no *value* with `EVIDENCE` above — the claim these
# files make is that the card carries *both* halves, and a fixture whose halves held the same
# values could not tell a card showing two from one showing the same one twice (V87). It does
# share the key `suspended`, and reads `False` on one side and `True` on the other.
RECEIPT_AFTER: dict[str, Any] = {"suspended": True, "suspended_at": "2026-08-08T09:31:00+00:00"}

# The delta the runner published beside that envelope, as T38's writer stored it. A third value
# with its own keys, because the claim the card makes is that all three travel: a body carrying
# `before` and `after` alone would satisfy "the receipt renders" and none of what the delta is
# for. Shaped as `core.approvals.delta.ChangeDelta.as_payload` writes it, absent facets omitted.
#
# What this fixture does **not** hold is why the delta has to exist. That is a property of the
# seven real tools' two vocabularies — the keys they share are identity and carry equal values,
# and the field that moved is never addressable in both — and it is measured against the tools
# themselves in `apps/api/tests/test_change_receipt_halves.py`. These two literals are a card
# rendering fixture and were never in the shape a gate writes: a real WHM before-state nests the
# account (`{"account": {"user": ..., "suspended": ...}}`), which is exactly the non-alignment
# the real claim is about.
RECEIPT_DELTA: dict[str, Any] = {
    "identity": {"server": "alpha", "username": "acmeco"},
    "verification": "verified",
    "changed_fields": [{"field": "suspended", "old": False, "new": True}],
}

LIBRECHAT_USER_ID = "librechat-user-1"

# Fixed, because nothing judges it: pinning it keeps payload equality exact (V87). The
# *deadline* is always offset from the real clock — see `card_view`.
CREATED_AT = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)


def run_view(
    *,
    tool_run_id: UUID | None = None,
    status: ToolRunStatus = ToolRunStatus.STARTED,
    result_summary: str | None = None,
    completed_at: datetime | None = None,
) -> ActionRunView:
    """The execution an approval started, as the repository would report it."""
    return ActionRunView(
        tool_run_id=tool_run_id or uuid4(),
        status=status,
        result_summary=result_summary,
        created_at=CREATED_AT,
        completed_at=completed_at,
    )


def receipt_view(
    *,
    ok: bool = True,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    error_code: str | None = None,
    delta: dict[str, Any] | None = None,
) -> ApprovalCardReceipt:
    """What T38's writer recorded, as the card reader returns it (V46).

    `before` defaults to the same `EVIDENCE` the gate persisted, because that is what the
    production writer copies onto the receipt — a fixture with a different before-state would
    describe a receipt neither of T38's two writers can produce.

    `delta` defaults to **absent**, and that is the writer's own default rather than a shortcut:
    the receipt key is omitted when the runner stated nothing, so a fixture that supplied one
    unasked would make every card test assert against a receipt no refusal can produce. Pass
    `RECEIPT_DELTA` for the case where a runner did state one.
    """
    return ApprovalCardReceipt(
        ok=ok,
        before=EVIDENCE if before is None else before,
        after=RECEIPT_AFTER if after is None else after,
        error_code=error_code,
        delta=delta,
    )


def card_view(
    *,
    action_request_id: UUID | None = None,
    tool_name: str = CHANGE_TOOL,
    status: ActionRequestStatus = ActionRequestStatus.PENDING,
    conversation_ref: str | None = CONVERSATION_ID,
    requester_email: str = OPERATOR_EMAIL,
    librechat_user_id: str = LIBRECHAT_USER_ID,
    arguments: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    expires_in_seconds: float = 3600,
    now: datetime | None = None,
    decided_at: datetime | None = None,
    run: ActionRunView | None = None,
    receipt: ApprovalCardReceipt | None = None,
) -> ApprovalCardView:
    """One request as the card reader returns it. `expires_in_seconds` may be negative.

    Same construction as `support.action_results.result_view` and
    `support.action_expiry.pending_row`: a fixed `created_at`, a clock-relative deadline. A
    fixed `expires_at` would be in the past by the time the suite runs, and every PENDING
    request would read `EXPIRED`.
    """
    moment = now or datetime.now(UTC)
    return ApprovalCardView(
        action_request_id=action_request_id or uuid4(),
        tool_name=tool_name,
        status=status,
        conversation_ref=conversation_ref,
        requester=ApprovalCardRequester(
            email=requester_email,
            librechat_user_id=librechat_user_id,
        ),
        arguments=ARGUMENTS if arguments is None else arguments,
        evidence=EVIDENCE if evidence is None else evidence,
        created_at=CREATED_AT,
        expires_at=moment + timedelta(seconds=expires_in_seconds),
        decided_at=decided_at,
        run=run,
        receipt=receipt,
    )


@dataclass
class StoredCard:
    """A view plus the operator it belongs to. `None` stands for a deleted requester."""

    view: ApprovalCardView
    requester_user_id: UUID | None


class FakeApprovalCardRepository:
    """In-memory `ApprovalCardRepository`, with the read breakable.

    `fail` exists because the read is the one place on this route's path that can raise for
    reasons nobody predicted, and V73 says what an operator gets then is the shared envelope
    rather than a stack trace.
    """

    def __init__(self, journal: list[str] | None = None) -> None:
        self.rows: dict[UUID, StoredCard] = {}
        self.journal = journal if journal is not None else []
        # One entry per call, so a test can assert *which* requester was asked about — the
        # cookie's identity, never anything from the request (V27).
        self.lookups: list[tuple[UUID, UUID]] = []
        self.fail: BaseException | None = None

    def add(
        self,
        view: ApprovalCardView,
        *,
        requester_user_id: UUID | None,
    ) -> ApprovalCardView:
        self.rows[view.action_request_id] = StoredCard(
            view=view, requester_user_id=requester_user_id
        )
        return view

    async def get_for_requester(
        self,
        *,
        action_request_id: UUID,
        requester_user_id: UUID,
    ) -> ApprovalCardView | None:
        self.journal.append("read")
        self.lookups.append((action_request_id, requester_user_id))
        if self.fail is not None:
            raise self.fail

        stored = self.rows.get(action_request_id)
        if stored is None or stored.requester_user_id != requester_user_id:
            # Absent, foreign, or its requester was deleted — one answer for all three, the way
            # the production `WHERE` gives one (V27). Whether a real row behaves this way is
            # `test_approval_cards_live.py`'s claim, not this double's.
            return None
        return stored.view


@dataclass
class CardHarness:
    """Everything a card-route test pokes at, so assertions read off one object."""

    client: TestClient
    app: FastAPI
    settings: Settings
    jwt_service: JWTService
    repository: FakeApprovalCardRepository
    expiry_repository: FakeActionRequestExpiryRepository
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

    def add_card(self, **overrides: Any) -> ApprovalCardView:
        """Seed one card owned by the signed-in operator."""
        return self.add_card_for(self.operator.id, **overrides)

    def add_card_for(
        self,
        requester_user_id: UUID | None,
        **overrides: Any,
    ) -> ApprovalCardView:
        """Seed one card for a given requester. `None` = a deleted one (T34's `SET NULL`).

        `None` is spelled as an argument rather than as a default, because "owned by nobody"
        and "owned by whoever is signed in" are the two cases V27 has to tell apart, and one
        default standing for both is how a test ends up asserting the wrong one.

        The expiry double is seeded from the same view, so a deadline in the past reaches
        `expire_if_due` as a due row rather than being invisible to it.
        """
        view = card_view(**overrides)
        self.repository.add(view, requester_user_id=requester_user_id)
        self.expiry_repository.add(
            FakeActionRequestRow(
                action_request_id=view.action_request_id,
                expires_at=view.expires_at,
                status=view.status,
            )
        )
        return view

    def get_card(self, action_request_id: UUID) -> Response:
        return self.client.get(f"/action-requests/{action_request_id}")

    @contextmanager
    def client_that_reports_server_errors(self) -> Iterator[TestClient]:
        """A signed-in client that returns a 500 instead of re-raising.

        Starlette's `ServerErrorMiddleware` re-raises for `TestClient` unless told not to, so
        the shape of an unhandled failure is unassertable through the default client — the same
        reason `test_error_envelope.py` builds its own.
        """
        with TestClient(self.app, raise_server_exceptions=False) as client:
            issued = self.jwt_service.create_access_token(
                email=self.operator.email,
                user_id=self.operator.id,
            )
            client.cookies.set(COOKIE_NAME, issued.token)
            yield client


@contextmanager
def card_harness(*, settings: Settings | None = None) -> Iterator[CardHarness]:
    """An app with the `/action-requests` router, wired to in-memory doubles.

    One dependency override beyond auth: the card service. `AuthService` is overridden with
    `support.auth`'s own factory over a *real* `AuthService`, so the `users.is_active` re-read
    behind `require_session_user` is production code here (V6) and the route's 401 path is real.

    The real `ApprovalCardService` sits over the doubles, so the ordering V32 depends on — the
    requester-matched read first, the expiry second — is the shipped ordering.
    """
    resolved_settings = settings or build_settings()
    jwt_service = JWTService(resolved_settings)

    journal: list[str] = []
    repository = FakeApprovalCardRepository(journal)
    expiry_repository = FakeActionRequestExpiryRepository(journal)

    auth_repository = FakeAuthRepository()
    operator = auth_repository.add_active_user(OPERATOR_EMAIL)

    app = FastAPI()
    install_error_handling(app)
    app.include_router(action_requests_router)

    # The same attributes `noa_api.main.lifespan` writes, minus what no test here needs.
    setattr(app.state, STATE_SETTINGS, resolved_settings)
    setattr(app.state, STATE_JWT_SERVICE, jwt_service)
    setattr(app.state, STATE_LDAP_SERVICE, None)
    setattr(app.state, STATE_SESSION_FACTORY, None)

    app.dependency_overrides[get_auth_service] = override_auth_service_factory(
        settings=resolved_settings,
        repository=auth_repository,
        jwt_service=jwt_service,
    )
    app.dependency_overrides[get_approval_card_service] = lambda: ApprovalCardService(
        repository=repository,
        expiry=ActionRequestExpiryService(expiry_repository),
    )

    with TestClient(app) as client:
        yield CardHarness(
            client=client,
            app=app,
            settings=resolved_settings,
            jwt_service=jwt_service,
            repository=repository,
            expiry_repository=expiry_repository,
            auth_repository=auth_repository,
            operator=operator,
            journal=journal,
        )


__all__ = [
    "ARGUMENTS",
    "CHANGE_TOOL",
    "CONVERSATION_ID",
    "CREATED_AT",
    "EVIDENCE",
    "LIBRECHAT_USER_ID",
    "RECEIPT_AFTER",
    "RECEIPT_DELTA",
    "CardHarness",
    "FakeApprovalCardRepository",
    "StoredCard",
    "card_harness",
    "card_view",
    "receipt_view",
    "run_view",
]
