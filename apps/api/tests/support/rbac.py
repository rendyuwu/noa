"""Doubles and a probe app for the RBAC engine.

Same split as `support.auth`: the in-memory repository covers policy, and
`SQLAuthorizationRepository` gets its own coverage against a live scratch database in
`test_rbac_repository.py`. The *real* `AuthorizationService` runs against these doubles, so
the admin bypass, the disabled-user rule, the last-admin guards and the internal-role
rules are all exercised for real — only the SQL is faked.

`FakeAuthorizationRepository` counts reads. Two invariants are about *when* the database is
consulted rather than what it answers: one (permission resolution reads the row, never the
cookie's claims) and the other (permission updates take effect immediately). A double that only
returned the right answer could not tell a fresh read from a cached one, so the counters are
the assertion surface.

`RecordingAuditSink` keeps every event, so "admin changes produce audit events" is
asserted per operation instead of inferred from a log line nobody parses.

`admin_probe_app` mounts one throwaway route behind `require_admin`, because "non-admin
users → 403 on admin endpoints" is a property of the *dependency* and this is the smallest
thing that proves the dependency enforces it. It stays after the user routes shipped the first
real `/admin` routes: the property should hold for a route nobody has written yet. The harness for
the shipped routes is `support.admin`, which puts this repository behind them.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from core.audit.admin_events import AdminAuditEvent
from core.auth.authorization_service import AuthorizationService
from core.auth.jwt_service import JWTService
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.models import ADMIN_ROLE_NAME, INTERNAL_ROLE_PREFIX, is_internal_role
from noa_api.api.deps import (
    AdminUserDep,
)
from support.auth import (
    COOKIE_NAME,
    FakeAuthRepository,
    FakeUserRow,
    build_settings,
    session_app,
)

# Two real catalog names, so a test grant is indistinguishable from a production one.
TOOL_READ = "whm_list_accounts"
TOOL_CHANGE = "whm_suspend_account"

# Never in the catalog: what a stale grant or an injected tool name looks like.
TOOL_UNKNOWN = "whm_delete_everything"

ROLE_SUPPORT = "support"
ROLE_NOC = "noc"

# An internal role, spelled the way NOA spells them.
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
        # How many MCP tokens each user holds. A count rather than rows: the
        # service only decides *whether* to revoke and reports how many went, and the SQL
        # that proves the rows really disappear has its own test.
        self.mcp_tokens: dict[UUID, int] = {}
        # Read counters — see the module docstring.
        self.user_reads = 0
        self.grant_reads = 0
        # An ordered log this repository shares with `RecordingToolListNotifier`. The
        # notification's *position* relative to the commit is the assertion, and two independent
        # counters cannot express an order.
        self.calls: list[str] = []
        # Commit counter and snapshot. Same trick as `FakeAuthRepository`: a double that
        # only mutated dicts cannot tell a written row from a committed one, and "a refused
        # disable persists nothing" is a claim about the second.
        self.commits = 0
        self.committed_users: dict[UUID, FakeUserRecord] = {}
        self.committed_user_roles: dict[UUID, set[str]] = {}
        # Role state is snapshotted too. The role routes' writes land here and nowhere
        # else, so without these a "the refused grant write persisted nothing" assertion could
        # only read the mutable dict — which cannot tell a written row from a committed one.
        self.committed_roles: set[str] = set()
        self.committed_role_tools: dict[str, set[str]] = {}

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

    async def list_user_ids_with_role(self, role_name: str) -> list[UUID]:
        """The list-changed emitter's notification audience.

        Mirrors the SQL exactly, including what it does *not* filter: a disabled user still
        appears, because their catalog moved too and their session may still be open. Derived
        from `user_roles` rather than from a second dict, so a test that assigns a role through
        the service reaches this the same way production does.
        """
        return sorted(user_id for user_id, roles in self.user_roles.items() if role_name in roles)

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
        # Mirrors the SQL: internal roles survive replacement.
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
        """The cascade revoke. Token *count* per user, since that is all the
        service asserts on — the SQL that proves rows really go is in
        `test_rbac_repository.py`."""
        return self.mcp_tokens.pop(user_id, 0)

    # --- Transaction boundary ---

    async def commit(self) -> None:
        """Snapshot every row, so a test can separate "written" from "committed"."""
        self.commits += 1
        # The notification must follow the commit, never precede it — a client told to
        # refetch before the transaction ends could read rows that then roll back. Recorded on a
        # shared log with `RecordingToolListNotifier` so the *order* is assertable, which a
        # counter on each side separately could not be.
        self.calls.append("commit")
        self.committed_users = {user_id: replace(user) for user_id, user in self.users.items()}
        self.committed_user_roles = {
            user_id: set(names) for user_id, names in self.user_roles.items()
        }
        self.committed_roles = set(self.roles)
        self.committed_role_tools = {
            role_name: set(tools) for role_name, tools in self.role_tools.items()
        }

    # --- Test helpers ---

    def add_user(
        self,
        email: str,
        *,
        user_id: UUID | None = None,
        is_active: bool = True,
        roles: tuple[str, ...] = (),
        mcp_tokens: int = 0,
    ) -> FakeUserRecord:
        """Add a `users` row. `user_id` is passed when a caller needs it to match another
        double's id — `support.admin` mirrors the signed-in actor into both repositories so
        the self-deactivate and self-delete guards are reachable from HTTP."""
        user = FakeUserRecord(
            id=user_id or uuid4(), email=email, display_name=email, is_active=is_active
        )
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
        """Assign a `user:`-prefixed role the way NOA itself would."""
        self.roles.add(role_name)
        self.user_roles.setdefault(user_id, set()).add(role_name)


class RecordingAuditSink:
    """`AdminAuditSink` that keeps every event for assertion."""

    def __init__(self) -> None:
        self.events: list[AdminAuditEvent] = []

    async def record(self, event: AdminAuditEvent) -> None:
        self.events.append(event)

    @property
    def event_types(self) -> list[str]:
        return [event.event_type for event in self.events]


class RecordingToolListNotifier:
    """`ToolListChangedNotifier` that keeps every audience for assertion.

    Records the *audiences*, not a count, because the list-changed emitter's questions are about
    who: a role's grant change reaches its holders, a disable reaches one account, a role creation
    reaches nobody. A counter would pass against a broadcast.

    `calls` is the ordered log it shares with `FakeAuthorizationRepository`, so
    "the notification followed the commit" is assertable rather than assumed.

    `fail_with` makes the failure path reachable: the list-changed emit is best-effort, and a
    notifier that raises must not turn a committed permission change into a 500.
    """

    def __init__(
        self,
        *,
        calls: list[str] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.audiences: list[list[UUID]] = []
        self.calls = calls if calls is not None else []
        self._fail_with = fail_with

    async def notify(self, user_ids: Collection[UUID]) -> None:
        self.audiences.append(sorted(user_ids))
        self.calls.append("notify")
        if self._fail_with is not None:
            raise self._fail_with

    @property
    def notified(self) -> list[UUID]:
        """Every id told, across all calls, sorted and de-duplicated."""
        return sorted({user_id for audience in self.audiences for user_id in audience})


@dataclass
class RbacFixture:
    """The service under test plus the doubles behind it."""

    service: AuthorizationService
    repository: FakeAuthorizationRepository
    audit: RecordingAuditSink
    notifier: RecordingToolListNotifier


def build_service(
    *,
    repository: FakeAuthorizationRepository | None = None,
    audit: RecordingAuditSink | None = None,
    notifier: RecordingToolListNotifier | None = None,
    known_tools: frozenset[str] = TOOL_CATALOG,
) -> RbacFixture:
    """A real `AuthorizationService` over in-memory doubles.

    The notifier shares the repository's `calls` log, so any test built here can assert
    that a notification followed the commit rather than preceded it — including the tests that
    were written before the list-changed emitter existed and now cover the ordering for free.
    """
    resolved_repository = repository or FakeAuthorizationRepository()
    resolved_audit = audit or RecordingAuditSink()
    resolved_notifier = notifier or RecordingToolListNotifier(calls=resolved_repository.calls)
    return RbacFixture(
        service=AuthorizationService(
            repository=resolved_repository,
            audit_sink=resolved_audit,
            known_tools=known_tools,
            tool_list_notifier=resolved_notifier,
        ),
        repository=resolved_repository,
        audit=resolved_audit,
        notifier=resolved_notifier,
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
    """An app whose only route sits behind `require_admin`.

    Everything on the path is production code — `require_session_user`, `AuthService`, the
    real `JWTService`, the shared error handler — so a 403 here is the same 403 the `/admin`
    routes will return.
    """
    settings = build_settings()
    repository = FakeAuthRepository()
    jwt_service = JWTService(settings)

    app = session_app(settings=settings, jwt_service=jwt_service, repository=repository)

    @app.get(PROBE_PATH)
    async def probe(admin_user: AdminUserDep) -> dict[str, str]:
        return {"email": admin_user.email}

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
    "RecordingToolListNotifier",
    "admin_probe_app",
    "build_service",
]
