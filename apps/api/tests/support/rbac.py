"""Doubles and a probe app for the RBAC engine (T9).

Same split as `support.auth`: the in-memory repository covers policy, and
`SQLAuthorizationRepository` gets its own coverage against a live scratch database in
`test_rbac_repository.py`. The *real* `AuthorizationService` runs against these doubles, so
the admin bypass (V10), the disabled-user rule (V11), the V12 guards and the internal-role
rules (V13) are all exercised for real — only the SQL is faked.

`FakeAuthorizationRepository` counts reads. Two invariants are about *when* the database is
consulted rather than what it answers: V6 (permission resolution reads the row, never the
cookie's claims) and V14 (permission updates take effect immediately). A double that only
returned the right answer could not tell a fresh read from a cached one, so the counters are
the assertion surface.

`RecordingAuditSink` keeps every event, so V14's "admin changes produce audit events" is
asserted per operation instead of inferred from a log line nobody parses.

`admin_probe_app` mounts one throwaway route behind `require_admin`. T9 ships no `/admin`
routes — T51-T55 own those — but V13's "non-admin users → 403 on admin endpoints" is a
property of the dependency, and this is the smallest thing that proves the dependency
enforces it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.audit.admin_events import AdminAuditEvent
from core.auth.authorization_service import AuthorizationService
from core.auth.jwt_service import JWTService
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.models import ADMIN_ROLE_NAME, INTERNAL_ROLE_PREFIX, is_internal_role
from noa_api.api.deps import (
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
    AdminUserDep,
    get_auth_service,
)
from noa_api.api.errors import install_error_handler
from support.auth import (
    COOKIE_NAME,
    FakeAuthRepository,
    FakeUserRow,
    build_settings,
    override_auth_service_factory,
)

# Two real catalog names, so a test grant is indistinguishable from a production one.
TOOL_READ = "whm_list_accounts"
TOOL_CHANGE = "whm_suspend_account"

# Never in the catalog: what a stale grant or an injected tool name looks like (V10).
TOOL_UNKNOWN = "whm_delete_everything"

ROLE_SUPPORT = "support"
ROLE_NOC = "noc"

# An internal role, spelled the way NOA spells them (V13, V75).
INTERNAL_ROLE = f"{INTERNAL_ROLE_PREFIX}legacy"

PROBE_PATH = "/admin/probe"


@dataclass
class FakeUserRecord:
    """Stands in for a `users` row. Mutable, like the ORM object it replaces."""

    id: UUID
    email: str
    display_name: str | None = None
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime(2026, 8, 6, tzinfo=UTC))
    last_login_at: datetime | None = None


class FakeAuthorizationRepository:
    """In-memory `AuthorizationRepository`.

    Stores what the schema stores, not what the answers are: `role_tools` is keyed by role
    name and `user_roles` by user id, so a test that revokes a grant has to go through the
    same shape the SQL does. Deriving the answers instead would let the double agree with
    a service bug.
    """

    def __init__(self) -> None:
        self.users: dict[UUID, FakeUserRecord] = {}
        self.roles: set[str] = set()
        self.role_tools: dict[str, set[str]] = {}
        self.user_roles: dict[UUID, set[str]] = {}
        # How many MCP tokens each user holds (T11, V4). A count rather than rows: the
        # service only decides *whether* to revoke and reports how many went, and the SQL
        # that proves the rows really disappear has its own test.
        self.mcp_tokens: dict[UUID, int] = {}
        # Read counters — see the module docstring (V6, V14).
        self.user_reads = 0
        self.grant_reads = 0

    # --- Reads on the permission path ---

    async def get_user_by_id(self, user_id: UUID) -> FakeUserRecord | None:
        self.user_reads += 1
        return self.users.get(user_id)

    async def get_role_names(self, user_id: UUID) -> list[str]:
        return sorted(self.user_roles.get(user_id, set()))

    async def get_role_tool_names(self, role_names: list[str]) -> list[str]:
        self.grant_reads += 1
        granted: set[str] = set()
        for role_name in role_names:
            granted |= self.role_tools.get(role_name, set())
        return sorted(granted)

    async def list_users(self) -> list[FakeUserRecord]:
        return sorted(self.users.values(), key=lambda user: user.email)

    # --- Roles and grants ---

    async def list_assignable_role_names(self) -> list[str]:
        return sorted(name for name in self.roles if not is_internal_role(name))

    async def role_exists(self, role_name: str) -> bool:
        return role_name in self.roles

    async def ensure_role(self, role_name: str) -> str:
        self.roles.add(role_name)
        return role_name

    async def delete_role(self, role_name: str) -> bool:
        if role_name not in self.roles:
            return False
        self.roles.discard(role_name)
        self.role_tools.pop(role_name, None)
        for assigned in self.user_roles.values():
            assigned.discard(role_name)
        return True

    async def list_existing_role_names(self, role_names: list[str]) -> list[str]:
        return sorted({name for name in role_names if name in self.roles})

    async def get_role_tool_names_for_role(self, role_name: str) -> list[str]:
        return sorted(self.role_tools.get(role_name, set()))

    async def replace_role_tool_permissions(self, role_name: str, tool_names: list[str]) -> None:
        if role_name not in self.roles:
            return
        self.role_tools[role_name] = set(tool_names)

    async def replace_user_assignable_roles(self, user_id: UUID, role_names: list[str]) -> None:
        # Mirrors the SQL: internal roles survive replacement (V13, V75).
        kept = {name for name in self.user_roles.get(user_id, set()) if is_internal_role(name)}
        self.user_roles[user_id] = kept | {name for name in role_names if name in self.roles}

    # --- User administration ---

    async def update_user_active(self, user_id: UUID, *, is_active: bool) -> FakeUserRecord | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        user.is_active = is_active
        return user

    async def count_active_admin_users(self) -> int:
        return sum(
            1
            for user_id, roles in self.user_roles.items()
            if ADMIN_ROLE_NAME in roles
            and (user := self.users.get(user_id)) is not None
            and user.is_active
        )

    async def delete_user(self, user_id: UUID) -> bool:
        if self.users.pop(user_id, None) is None:
            return False
        self.user_roles.pop(user_id, None)
        self.mcp_tokens.pop(user_id, None)
        return True

    async def delete_mcp_tokens_for_user(self, user_id: UUID) -> int:
        """V4's cascade revoke (T11). Token *count* per user, since that is all the
        service asserts on — the SQL that proves rows really go is in
        `test_rbac_repository.py`."""
        return self.mcp_tokens.pop(user_id, 0)

    # --- Test helpers ---

    def add_user(
        self,
        email: str,
        *,
        is_active: bool = True,
        roles: tuple[str, ...] = (),
        mcp_tokens: int = 0,
    ) -> FakeUserRecord:
        user = FakeUserRecord(id=uuid4(), email=email, display_name=email, is_active=is_active)
        self.users[user.id] = user
        for role in roles:
            self.roles.add(role)
            self.user_roles.setdefault(user.id, set()).add(role)
        if mcp_tokens:
            self.mcp_tokens[user.id] = mcp_tokens
        return user

    def grant(self, role_name: str, *tool_names: str) -> None:
        self.roles.add(role_name)
        self.role_tools.setdefault(role_name, set()).update(tool_names)

    def assign_internal_role(self, user_id: UUID, role_name: str) -> None:
        """Assign a `user:`-prefixed role the way NOA itself would (V13, V75)."""
        self.roles.add(role_name)
        self.user_roles.setdefault(user_id, set()).add(role_name)


class RecordingAuditSink:
    """`AdminAuditSink` that keeps every event for assertion (V14)."""

    def __init__(self) -> None:
        self.events: list[AdminAuditEvent] = []

    async def record(self, event: AdminAuditEvent) -> None:
        self.events.append(event)

    @property
    def event_types(self) -> list[str]:
        return [event.event_type for event in self.events]


@dataclass
class RbacFixture:
    """The service under test plus the doubles behind it."""

    service: AuthorizationService
    repository: FakeAuthorizationRepository
    audit: RecordingAuditSink


def build_service(
    *,
    repository: FakeAuthorizationRepository | None = None,
    audit: RecordingAuditSink | None = None,
    known_tools: frozenset[str] = TOOL_CATALOG,
) -> RbacFixture:
    """A real `AuthorizationService` over in-memory doubles."""
    resolved_repository = repository or FakeAuthorizationRepository()
    resolved_audit = audit or RecordingAuditSink()
    return RbacFixture(
        service=AuthorizationService(
            repository=resolved_repository,
            audit_sink=resolved_audit,
            known_tools=known_tools,
        ),
        repository=resolved_repository,
        audit=resolved_audit,
    )


@dataclass
class AdminProbeHarness:
    """A client plus the auth double behind it, for `require_admin` tests."""

    client: TestClient
    repository: FakeAuthRepository
    jwt_service: JWTService

    def sign_in(self, email: str, *, roles: tuple[str, ...] = ()) -> FakeUserRow:
        """Add an active user with `roles` and put their session cookie on the client."""
        user = self.repository.add_active_user(email, roles=roles)
        issued = self.jwt_service.create_access_token(email=email, user_id=user.id)
        self.client.cookies.set(COOKIE_NAME, issued.token)
        return user


@contextmanager
def admin_probe_app() -> Iterator[AdminProbeHarness]:
    """An app whose only route sits behind `require_admin` (V13).

    Everything on the path is production code — `require_session_user`, `AuthService`, the
    real `JWTService`, the shared error handler — so a 403 here is the same 403 the `/admin`
    routes will return in T51.
    """
    settings = build_settings()
    repository = FakeAuthRepository()
    jwt_service = JWTService(settings)

    app = FastAPI()
    install_error_handler(app)

    @app.get(PROBE_PATH)
    async def probe(admin_user: AdminUserDep) -> dict[str, str]:
        return {"email": admin_user.email}

    setattr(app.state, STATE_SETTINGS, settings)
    setattr(app.state, STATE_JWT_SERVICE, jwt_service)
    setattr(app.state, STATE_LDAP_SERVICE, None)
    setattr(app.state, STATE_SESSION_FACTORY, None)

    app.dependency_overrides[get_auth_service] = override_auth_service_factory(
        settings=settings, repository=repository, jwt_service=jwt_service
    )

    with TestClient(app) as client:
        yield AdminProbeHarness(client=client, repository=repository, jwt_service=jwt_service)


__all__ = [
    "INTERNAL_ROLE",
    "PROBE_PATH",
    "ROLE_NOC",
    "ROLE_SUPPORT",
    "TOOL_CHANGE",
    "TOOL_READ",
    "TOOL_UNKNOWN",
    "AdminProbeHarness",
    "FakeAuthorizationRepository",
    "FakeUserRecord",
    "RbacFixture",
    "RecordingAuditSink",
    "admin_probe_app",
    "build_service",
]
