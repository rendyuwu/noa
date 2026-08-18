"""An app carrying the real `/admin` and `/me` routes, over in-memory doubles (T51-T54).

Same split every route test in this suite uses: the router, `require_admin`,
`require_session_user`, the real `AuthService`, the real `AuthorizationService`, the real
`JWTService` and the shared error handler are all production code — only SQL and LDAP are faked.
So a 403 here is the shipped 403, a 409 is the shipped 409, and the V12 guards run for real.
`SQLAuthorizationRepository` gets its own live-database coverage in `test_rbac_repository.py`.

**Three repositories, one identity.** The session path resolves the caller through
`support.auth`'s `FakeAuthRepository`, the admin routes read and write `support.rbac`'s
`FakeAuthorizationRepository`, and T53's token routes read and write
`support.mcp_tokens`'s `FakeMcpTokenRepository` — the same split production has, where three
services hold different repositories over one session. `sign_in` and `add_target` therefore
write each user into *all three*, under one id. Without that, `actor_user_id` could never equal
a target's id and V12's self-deactivate, self-delete and self-demote refusals would be
unreachable from HTTP — the tests would pass while asserting nothing — and every token route
would answer 404 `user_not_found` for a user the panel can see.

**Every router is mounted, not one per harness.** T52's role routes and T51's user routes
share the actor, the gate and one `AuthorizationService` over one repository, and V14's
"permission updates take effect immediately" is a claim that spans them: a `PUT
/admin/roles/{name}/tools` has to be visible in the very next `GET /admin/users`. Two harnesses
could not express that without a second repository, i.e. without the thing being asserted.
T53's routers join for a second reason: `/me/mcp-tokens` is gated by `require_session_user`
while `/admin/users/{id}/tokens` is gated by `require_admin`, and the difference between them
is only observable when one signed-in actor can try both.

T54's three server routers join for a third: `require_admin` is a parameter on all fifteen of
their handlers, and "every admin route is admin-only" is a claim about the whole surface — a
test that walks it needs the whole surface mounted under one actor. Their write repositories
are `support.server_admin`'s, their CRUD services are the **real** ones over those, and only
the validate services are stubbed (that module records why).

T55's audit router joins for that third reason, and adds nothing else to the harness: its service
is the **real** `ToolRunAuditService` over `support.tool_run_audit`'s in-memory reader, so the page
bound, the cursor minting and the payload shape all run for real. What is *not* modelled there is
filtering — that lives in the SQL, and `support.tool_run_audit` records why a Python copy of it
would be a test agreeing with a double.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.audit.tool_run_reads import ToolRunAuditService
from core.auth.authorization_service import AuthorizationService
from core.auth.jwt_service import JWTService
from core.auth.mcp_token_service import McpTokenService
from core.auth.tool_catalog import TOOL_CATALOG
from core.config import Settings
from core.db.models import ADMIN_ROLE_NAME, PMGServer, ProxmoxServer, WHMServer
from core.servers.admin_service import (
    PMGServerAdminService,
    ProxmoxServerAdminService,
    WHMServerAdminService,
)
from core.servers.errors import (
    PMGServerNotFoundError,
    ProxmoxServerNotFoundError,
    WHMServerNotFoundError,
)
from core.servers.validation import ServerValidationResult
from noa_api.api.deps import (
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
    get_auth_service,
    get_authorization_service,
    get_mcp_token_service,
    get_pmg_server_admin_service,
    get_pmg_server_validation_service,
    get_proxmox_server_admin_service,
    get_proxmox_server_validation_service,
    get_tool_run_audit_service,
    get_whm_server_admin_service,
    get_whm_server_validation_service,
)
from noa_api.api.errors import install_error_handling
from noa_api.api.routes.admin_audit import router as admin_audit_router
from noa_api.api.routes.admin_roles import router as admin_roles_router
from noa_api.api.routes.admin_servers import pmg_router as admin_pmg_servers_router
from noa_api.api.routes.admin_servers import proxmox_router as admin_proxmox_servers_router
from noa_api.api.routes.admin_servers import whm_router as admin_whm_servers_router
from noa_api.api.routes.admin_users import router as admin_users_router
from noa_api.api.routes.mcp_tokens import admin_router as admin_tokens_router
from noa_api.api.routes.mcp_tokens import me_router as me_tokens_router
from support.auth import (
    COOKIE_NAME,
    FakeAuthRepository,
    FakeUserRow,
    build_settings,
    override_auth_service_factory,
)
from support.mcp_tokens import FakeMcpTokenRepository
from support.rbac import (
    FakeAuthorizationRepository,
    FakeUserRecord,
    RecordingAuditSink,
    RecordingToolListNotifier,
)
from support.secrets import build_cipher
from support.server_admin import (
    FakePMGServerAdminRepository,
    FakeProxmoxServerAdminRepository,
    FakeWHMServerAdminRepository,
    RecordingValidationService,
)
from support.tool_run_audit import FakeToolRunAuditReader

ADMIN_EMAIL = "admin@example.com"
OPERATOR_EMAIL = "operator@example.com"

USERS_PATH = "/admin/users"
ROLES_PATH = "/admin/roles"
TOOLS_PATH = "/admin/tools"
ME_TOKENS_PATH = "/me/mcp-tokens"
WHM_SERVERS_PATH = "/admin/whm/servers"
PROXMOX_SERVERS_PATH = "/admin/proxmox/servers"
PMG_SERVERS_PATH = "/admin/pmg/servers"
TOOL_RUNS_PATH = "/admin/audit/tool-runs"


def admin_tokens_path(user_id: UUID) -> str:
    """`/admin/users/{user_id}/tokens` — spelled once so a route rename lands in one place."""
    return f"{USERS_PATH}/{user_id}/tokens"


@dataclass
class SignedInUser:
    """One actor, as both doubles see them.

    Held together so a test that flips `is_active` knows which row it is flipping: `session` is
    what `require_session_user` re-reads (V6), `record` is what the admin routes read and write.
    """

    id: UUID
    email: str
    session: FakeUserRow
    record: FakeUserRecord


@dataclass
class AdminHarness:
    """A client plus every double behind it, so assertions read off one object."""

    client: TestClient
    app: FastAPI
    settings: Settings
    jwt_service: JWTService
    auth_repository: FakeAuthRepository
    repository: FakeAuthorizationRepository
    token_repository: FakeMcpTokenRepository
    audit: RecordingAuditSink
    notifier: RecordingToolListNotifier
    # T54's three verticals. The write repositories are what the routes mutate; the validation
    # services are stubs (see `support.server_admin.RecordingValidationService` for why).
    whm_servers: FakeWHMServerAdminRepository
    proxmox_servers: FakeProxmoxServerAdminRepository
    pmg_servers: FakePMGServerAdminRepository
    whm_validation: RecordingValidationService
    proxmox_validation: RecordingValidationService
    pmg_validation: RecordingValidationService
    # T55. The audit trail this harness serves; a test appends items to it directly, because the
    # writers that fill the real table are on the MCP side of V22's boundary.
    tool_runs: FakeToolRunAuditReader

    def sign_in(
        self,
        email: str = ADMIN_EMAIL,
        *,
        roles: tuple[str, ...] = (ADMIN_ROLE_NAME,),
        mcp_tokens: int = 0,
    ) -> SignedInUser:
        """Put an active user's session cookie on the client, mirrored into all three doubles."""
        session_row = self.auth_repository.add_active_user(email, roles=roles)
        record = self.repository.add_user(
            email, user_id=session_row.id, roles=roles, mcp_tokens=mcp_tokens
        )
        self.token_repository.add_user(session_row.id)
        issued = self.jwt_service.create_access_token(email=email, user_id=session_row.id)
        self.client.cookies.set(COOKIE_NAME, issued.token)
        return SignedInUser(id=session_row.id, email=email, session=session_row, record=record)

    def add_target(
        self,
        email: str = OPERATOR_EMAIL,
        *,
        is_active: bool = True,
        roles: tuple[str, ...] = (),
        mcp_tokens: int = 0,
    ) -> FakeUserRecord:
        """A user the admin routes act on. No session: they never sign in."""
        record = self.repository.add_user(
            email, is_active=is_active, roles=roles, mcp_tokens=mcp_tokens
        )
        self.token_repository.add_user(record.id)
        return record

    def mint_token(self, user_id: UUID, *, label: str | None = None) -> dict[str, object]:
        """`POST /admin/users/{id}/tokens` → the whole body, plaintext included (T53)."""
        response = self.client.post(admin_tokens_path(user_id), json={"label": label})
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, dict)
        return body

    def list_tokens(self, user_id: UUID) -> list[dict[str, object]]:
        """`GET /admin/users/{id}/tokens` → the `tokens` array (T53)."""
        response = self.client.get(admin_tokens_path(user_id))
        assert response.status_code == 200, response.text
        tokens = response.json()["tokens"]
        assert isinstance(tokens, list)
        return tokens

    def grant(self, role_name: str, *tool_names: str) -> None:
        """Give a role tool grants, through the same shape the SQL stores them in."""
        self.repository.grant(role_name, *tool_names)

    def list_users(self) -> list[dict[str, object]]:
        """`GET /admin/users` → the `users` array, for tests that assert on the list."""
        response = self.client.get(USERS_PATH)
        assert response.status_code == 200, response.text
        users = response.json()["users"]
        assert isinstance(users, list)
        return users

    def user_in_list(self, email: str) -> dict[str, object]:
        return next(user for user in self.list_users() if user["email"] == email)

    def list_roles(self) -> list[str]:
        """`GET /admin/roles` → the `roles` array (T52)."""
        response = self.client.get(ROLES_PATH)
        assert response.status_code == 200, response.text
        roles = response.json()["roles"]
        assert isinstance(roles, list)
        return roles

    def role_tools(self, role_name: str) -> list[str]:
        """`GET /admin/roles/{name}/tools` → the `tools` array (T52)."""
        response = self.client.get(f"{ROLES_PATH}/{role_name}/tools")
        assert response.status_code == 200, response.text
        tools = response.json()["tools"]
        assert isinstance(tools, list)
        return tools

    def list_servers(self, path: str) -> list[dict[str, object]]:
        """`GET` one of the three server lists → the `servers` array (T54)."""
        response = self.client.get(path)
        assert response.status_code == 200, response.text
        servers = response.json()["servers"]
        assert isinstance(servers, list)
        return servers

    def create_server(self, path: str, body: dict[str, object]) -> dict[str, object]:
        """`POST` one of the three server lists → the created row (T54). Asserts 201."""
        response = self.client.post(path, json=body)
        assert response.status_code == 201, response.text
        server = response.json()["server"]
        assert isinstance(server, dict)
        return server


@contextmanager
def admin_harness(
    *,
    settings: Settings | None = None,
    known_tools: frozenset[str] = TOOL_CATALOG,
    tool_runs: FakeToolRunAuditReader | None = None,
    whm_rows: Sequence[WHMServer] | None = None,
    proxmox_rows: Sequence[ProxmoxServer] | None = None,
    pmg_rows: Sequence[PMGServer] | None = None,
    validation_result: ServerValidationResult | None = None,
) -> Iterator[AdminHarness]:
    """An app with the `/admin` and `/me` routes, wired to in-memory doubles.

    Three dependency overrides and nothing else: `get_auth_service` (so the cookie resolves
    without Postgres or LDAP), `get_authorization_service` (so the routes read the fake RBAC
    repository) and `get_mcp_token_service` (so T53's routes read the fake token repository).
    `require_admin` and `require_session_user` are never overridden — they are the thing under
    test on every route.
    """
    resolved_settings = settings or build_settings()
    auth_repository = FakeAuthRepository()
    repository = FakeAuthorizationRepository()
    token_repository = FakeMcpTokenRepository()
    audit = RecordingAuditSink()
    # T66/V74. Shares the repository's ordered `calls` log, so a route test can assert the
    # notification followed the commit — the property that keeps a client from being told to
    # refetch a catalog built from rows that may still roll back.
    notifier = RecordingToolListNotifier(calls=repository.calls)
    jwt_service = JWTService(resolved_settings)

    whm_servers = FakeWHMServerAdminRepository(whm_rows or ())
    proxmox_servers = FakeProxmoxServerAdminRepository(proxmox_rows or ())
    pmg_servers = FakePMGServerAdminRepository(pmg_rows or ())
    whm_validation = RecordingValidationService(
        result=validation_result or ServerValidationResult(ok=True, message="ok"),
        not_found=WHMServerNotFoundError,
        known_ids=[row.id for row in whm_servers.servers],
    )
    proxmox_validation = RecordingValidationService(
        result=validation_result or ServerValidationResult(ok=True, message="ok"),
        not_found=ProxmoxServerNotFoundError,
        known_ids=[row.id for row in proxmox_servers.servers],
    )
    pmg_validation = RecordingValidationService(
        result=validation_result or ServerValidationResult(ok=True, message="ok"),
        not_found=PMGServerNotFoundError,
        known_ids=[row.id for row in pmg_servers.servers],
    )
    audit_reader = tool_runs or FakeToolRunAuditReader()

    app = FastAPI()
    install_error_handling(app)
    app.include_router(admin_users_router)
    app.include_router(admin_roles_router)
    app.include_router(admin_tokens_router)
    app.include_router(me_tokens_router)
    app.include_router(admin_whm_servers_router)
    app.include_router(admin_proxmox_servers_router)
    app.include_router(admin_pmg_servers_router)
    app.include_router(admin_audit_router)

    # The same attributes `noa_api.main.lifespan` writes, minus the engine no test here needs.
    setattr(app.state, STATE_SETTINGS, resolved_settings)
    setattr(app.state, STATE_JWT_SERVICE, jwt_service)
    setattr(app.state, STATE_LDAP_SERVICE, None)
    setattr(app.state, STATE_SESSION_FACTORY, None)

    app.dependency_overrides[get_auth_service] = override_auth_service_factory(
        settings=resolved_settings, repository=auth_repository, jwt_service=jwt_service
    )
    app.dependency_overrides[get_authorization_service] = lambda: AuthorizationService(
        repository=repository,
        audit_sink=audit,
        known_tools=known_tools,
        tool_list_notifier=notifier,
    )
    # One audit sink across both services, like production's: an assertion about "the admin
    # trail" reads one ordered log, and a token event landing in a second list would be
    # invisible to a test that walks the first.
    app.dependency_overrides[get_mcp_token_service] = lambda: McpTokenService(
        repository=token_repository,
        audit_sink=audit,
    )
    # T54. The CRUD services are the **real** ones over fake repositories and the real cipher,
    # so encrypt-on-write, the name check, the audit event and the commit call all run — the
    # same split every other service above uses. Only the validate services are stubs, and
    # `support.server_admin.RecordingValidationService` records why.
    cipher = build_cipher()
    app.dependency_overrides[get_whm_server_admin_service] = lambda: WHMServerAdminService(
        repository=whm_servers, cipher=cipher, audit_sink=audit
    )
    app.dependency_overrides[get_proxmox_server_admin_service] = lambda: ProxmoxServerAdminService(
        repository=proxmox_servers, cipher=cipher, audit_sink=audit
    )
    app.dependency_overrides[get_pmg_server_admin_service] = lambda: PMGServerAdminService(
        repository=pmg_servers, cipher=cipher, audit_sink=audit
    )
    app.dependency_overrides[get_whm_server_validation_service] = lambda: whm_validation
    app.dependency_overrides[get_proxmox_server_validation_service] = lambda: proxmox_validation
    app.dependency_overrides[get_pmg_server_validation_service] = lambda: pmg_validation
    # T55. The **real** service over the in-memory reader: the page bound, the cursor minting and
    # the payload shape are the shipped ones, and only the SQL is doubled.
    app.dependency_overrides[get_tool_run_audit_service] = lambda: ToolRunAuditService(
        repository=audit_reader
    )

    with TestClient(app) as client:
        yield AdminHarness(
            client=client,
            app=app,
            settings=resolved_settings,
            jwt_service=jwt_service,
            auth_repository=auth_repository,
            repository=repository,
            token_repository=token_repository,
            audit=audit,
            notifier=notifier,
            whm_servers=whm_servers,
            proxmox_servers=proxmox_servers,
            pmg_servers=pmg_servers,
            whm_validation=whm_validation,
            proxmox_validation=proxmox_validation,
            pmg_validation=pmg_validation,
            tool_runs=audit_reader,
        )


__all__ = [
    "ADMIN_EMAIL",
    "ME_TOKENS_PATH",
    "OPERATOR_EMAIL",
    "PMG_SERVERS_PATH",
    "PROXMOX_SERVERS_PATH",
    "ROLES_PATH",
    "TOOLS_PATH",
    "TOOL_RUNS_PATH",
    "USERS_PATH",
    "WHM_SERVERS_PATH",
    "AdminHarness",
    "SignedInUser",
    "admin_harness",
    "admin_tokens_path",
]
