import hashlib
import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import (
    ADMIN_ROLE_NOT_FOUND,
    DIRECT_TOOL_GRANTS_DISABLED,
    INTERNAL_ROLE_FORBIDDEN,
    INVALID_ROLE_NAME,
    INVALID_TOKEN,
    MISSING_BEARER_TOKEN,
    RESERVED_ROLE,
    SELF_REMOVE_ADMIN_ROLE,
    UNKNOWN_ROLES,
    UNKNOWN_TOOLS,
)
from noa_api.api.error_handling import install_error_handling
from noa_api.api.routes.admin import router as admin_router
from noa_api.core.auth.auth_service import AuthUser
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    LastActiveAdminError,
    SelfDeleteAdminError,
    SelfDeactivateAdminError,
    UnknownToolError,
    get_authorization_service,
)
from noa_api.core.auth.deps import get_auth_service, get_jwt_service
from noa_api.core.auth.errors import AuthInvalidCredentialsError
from noa_api.core.logging import configure_logging
from noa_api.core.telemetry import TelemetryEvent


class RecordingTelemetryRecorder:
    def __init__(self) -> None:
        self.trace_events: list[TelemetryEvent] = []
        self.metric_events: list[tuple[TelemetryEvent, int | float]] = []
        self.report_events: list[tuple[TelemetryEvent, str | None]] = []

    def trace(self, event: TelemetryEvent) -> None:
        self.trace_events.append(event)

    def metric(self, event: TelemetryEvent, *, value: int | float) -> None:
        self.metric_events.append((event, value))

    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None:
        self.report_events.append((event, detail))


@contextmanager
def _capture_structured_logs() -> Iterator[io.StringIO]:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_formatters = {
        id(handler): handler.formatter for handler in original_handlers
    }
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    try:
        configure_logging()
        yield stream
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for existing_handler in original_handlers:
            existing_handler.setFormatter(original_formatters[id(existing_handler)])


def _load_log_payloads(stream: io.StringIO) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in stream.getvalue().splitlines()
        if line.strip()
    ]


@dataclass
class _RepoUser:
    id: UUID
    email: str
    display_name: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class _InMemoryAuthorizationRepository:
    def __init__(self) -> None:
        self.users: dict[UUID, _RepoUser] = {}
        self.roles: set[str] = set()
        self.user_roles: dict[UUID, set[str]] = {}
        self.role_tools: dict[str, set[str]] = {}
        self.audit_events: list[dict[str, object | None]] = []

    async def get_role_tool_names(self, role_names: list[str]) -> list[str]:
        tools: set[str] = set()
        for role_name in role_names:
            tools.update(self.role_tools.get(role_name, set()))
        return sorted(tools)

    async def list_manageable_role_names(self) -> list[str]:
        return sorted({name for name in self.roles if not name.startswith("user:")})

    async def role_exists(self, role_name: str) -> bool:
        return role_name in self.roles

    async def create_role(self, role_name: str) -> str:
        self.roles.add(role_name)
        return role_name

    async def delete_role(self, role_name: str) -> bool:
        if role_name not in self.roles:
            return False
        self.roles.discard(role_name)
        self.role_tools.pop(role_name, None)
        for roles in self.user_roles.values():
            roles.discard(role_name)
        return True

    async def list_existing_role_names(self, role_names: list[str]) -> list[str]:
        normalized = {name.strip() for name in role_names if name.strip()}
        return sorted({name for name in normalized if name in self.roles})

    async def get_role_tool_names_for_role(self, role_name: str) -> list[str]:
        return sorted(self.role_tools.get(role_name, set()))

    async def replace_user_non_internal_roles(
        self, user_id: UUID, role_names: list[str]
    ) -> None:
        internal = {
            name
            for name in self.user_roles.get(user_id, set())
            if name.startswith("user:")
        }
        updated = internal | set(role_names)
        self.user_roles[user_id] = set(updated)

    async def list_users(self) -> list[_RepoUser]:
        return sorted(self.users.values(), key=lambda user: user.email)

    async def get_user_by_id(self, user_id: UUID) -> _RepoUser | None:
        return self.users.get(user_id)

    async def update_user_active(
        self, user_id: UUID, *, is_active: bool
    ) -> _RepoUser | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        user.is_active = is_active
        return user

    async def count_active_admin_users(self) -> int:
        total = 0
        for user in self.users.values():
            if not user.is_active:
                continue
            if "admin" in self.user_roles.get(user.id, set()):
                total += 1
        return total

    async def delete_user(self, user_id: UUID) -> _RepoUser | None:
        return self.users.pop(user_id, None)

    async def get_role_names(self, user_id: UUID) -> list[str]:
        return sorted(self.user_roles.get(user_id, set()))

    async def ensure_role(self, name: str) -> str:
        self.roles.add(name)
        return name

    async def assign_role(self, user_id: UUID, role_name: str) -> None:
        self.user_roles.setdefault(user_id, set()).add(role_name)

    async def replace_role_tool_permissions(
        self, role_name: str, tool_names: list[str]
    ) -> None:
        self.role_tools[role_name] = set(tool_names)

    async def get_user_allowlist_tools(self, user_id: UUID) -> list[str]:
        role_name = f"user:{user_id}"
        return sorted(self.role_tools.get(role_name, set()))

    async def remove_user_allowlist_role(self, user_id: UUID) -> bool:
        role_name = f"user:{user_id}"
        if role_name not in self.roles:
            self.role_tools.pop(role_name, None)
            for roles in self.user_roles.values():
                roles.discard(role_name)
            return False
        self.roles.discard(role_name)
        self.role_tools.pop(role_name, None)
        for roles in self.user_roles.values():
            roles.discard(role_name)
        return True

    async def list_all_tools(self) -> list[str]:
        all_tools: set[str] = set()
        for tools in self.role_tools.values():
            all_tools.update(tools)
        return sorted(all_tools)

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None:
        self.audit_events.append(
            {
                "event_type": event_type,
                "actor_email": actor_email,
                "tool_name": tool_name,
                "metadata": metadata,
            }
        )


class _FakeAuthorizationService:
    def __init__(self) -> None:
        self.target_user_id = uuid4()
        created_at = datetime.now(UTC)
        self.users = [
            AuthorizationUser(
                user_id=self.target_user_id,
                email="member@example.com",
                display_name="Member",
                is_active=True,
                roles=["member"],
                tools=["get_current_time"],
                direct_tools=["get_current_time"],
                created_at=created_at,
                last_login_at=None,
            )
        ]
        self.all_tools = ["get_current_time", "get_current_date"]
        self.last_set_tools: list[str] | None = None
        self.last_is_active: bool | None = None
        self.deleted_user_id: UUID | None = None

    async def list_users(self) -> list[AuthorizationUser]:
        return self.users

    async def set_user_active(
        self,
        user_id: UUID,
        *,
        is_active: bool,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizationUser | None:
        self.last_is_active = is_active
        if user_id != self.target_user_id:
            return None
        self.users[0].is_active = is_active
        return self.users[0]

    async def list_tools(self) -> list[str]:
        return self.all_tools

    async def delete_user(
        self,
        user_id: UUID,
        *,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizationUser | None:
        _ = actor_email
        _ = actor_user_id
        self.deleted_user_id = user_id
        if user_id != self.target_user_id:
            return None
        return self.users.pop(0)

    async def set_user_tools(
        self,
        user_id: UUID,
        tool_names: list[str],
        *,
        actor_email: str | None = None,
    ) -> AuthorizationUser | None:
        unknown = [name for name in tool_names if name not in self.all_tools]
        if unknown:
            raise UnknownToolError(unknown)
        self.last_set_tools = tool_names
        if user_id != self.target_user_id:
            return None
        updated = sorted(set(tool_names))
        self.users[0].tools = updated
        self.users[0].direct_tools = updated
        return self.users[0]


class _FakeProtectedRouteJWTService:
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid

    def decode_token(self, token: str) -> dict[str, str]:
        if self.invalid:
            raise AuthInvalidCredentialsError("Invalid token")
        return {"sub": "admin@example.com"}


class _FakeProtectedRouteAuthService:
    def __init__(self, *, is_active: bool = True) -> None:
        self.is_active = is_active

    async def get_user_by_email(self, *, email: str) -> AuthUser:
        return AuthUser(
            user_id=uuid4(),
            email=email,
            display_name="Admin User",
            is_active=self.is_active,
            roles=["admin"],
        )


def _create_admin_app() -> FastAPI:
    app = FastAPI()
    install_error_handling(app)
    app.include_router(admin_router)
    return app


def _build_authorization_service(
    repo: _InMemoryAuthorizationRepository,
) -> AuthorizationService:
    return AuthorizationService(repository=cast(Any, repo))


async def test_authorization_service_admin_requires_explicit_tool_permissions() -> None:
    repo = _InMemoryAuthorizationRepository()
    service = _build_authorization_service(repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is True


async def test_authorization_service_admin_allows_when_role_grants_tool() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["admin"] = {"get_current_time"}
    service = _build_authorization_service(repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is True


async def test_authorization_service_admin_bypass_still_rejects_unknown_tools() -> None:
    repo = _InMemoryAuthorizationRepository()
    service = _build_authorization_service(repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )
    assert await service.authorize_tool_access(user, "not-a-real-tool") is False


async def test_authorization_service_disabled_user_has_zero_permissions() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["member"] = {"get_current_time"}
    service = _build_authorization_service(repo)
    allowed = await service.authorize_tool_access(
        AuthorizationUser(
            user_id=uuid4(),
            email="disabled@example.com",
            display_name="Disabled",
            is_active=False,
            roles=["admin", "member"],
            tools=[],
            direct_tools=[],
        ),
        "get_current_time",
    )
    assert allowed is False


async def test_authorization_service_non_admin_depends_on_tool_permissions() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["member"] = {"get_current_time"}
    service = _build_authorization_service(repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="member@example.com",
        display_name="Member",
        is_active=True,
        roles=["member"],
        tools=[],
        direct_tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is True
    assert await service.authorize_tool_access(user, "whm_suspend_account") is False


async def test_authorization_service_lists_canonical_tools() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["member"] = {"db-only"}
    service = _build_authorization_service(repo)

    assert await service.list_tools() == [
        "get_current_time",
        "get_current_date",
        "update_workflow_todo",
        "whm_list_servers",
        "whm_validate_server",
        "whm_check_binary_exists",
        "whm_mail_log_failed_auth_suspects",
        "whm_list_accounts",
        "whm_search_accounts",
        "whm_preflight_account",
        "whm_preflight_primary_domain_change",
        "whm_suspend_account",
        "whm_unsuspend_account",
        "whm_change_contact_email",
        "whm_change_primary_domain",
        "whm_preflight_firewall_entries",
        "whm_firewall_unblock",
        "whm_firewall_allowlist_add_ttl",
        "whm_firewall_allowlist_remove",
        "whm_firewall_denylist_add_ttl",
        "proxmox_list_servers",
        "proxmox_validate_server",
        "proxmox_preflight_vm_nic_toggle",
        "proxmox_disable_vm_nic",
        "proxmox_enable_vm_nic",
    ]


async def test_authorization_service_list_users_includes_created_and_last_login() -> (
    None
):
    repo = _InMemoryAuthorizationRepository()
    user_id = uuid4()
    now = datetime.now(UTC)
    repo.users[user_id] = _RepoUser(
        id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        created_at=now,
        last_login_at=None,
    )
    repo.user_roles[user_id] = {"member"}
    service = _build_authorization_service(repo)

    users = await service.list_users()
    assert users[0].created_at == now
    assert users[0].last_login_at is None


async def test_authorization_service_rejects_unknown_tool_updates() -> None:
    repo = _InMemoryAuthorizationRepository()
    service = _build_authorization_service(repo)

    await repo.ensure_role("member")

    try:
        await service.set_role_tools(
            "member",
            ["get_current_time", "unknown-tool"],
            actor_email="admin@example.com",
        )
        assert False, "Expected UnknownToolError"
    except UnknownToolError as exc:
        assert exc.unknown_tools == ["unknown-tool"]


async def test_authorization_service_permission_updates_take_effect_immediately() -> (
    None
):
    repo = _InMemoryAuthorizationRepository()
    user_id = uuid4()
    repo.users[user_id] = _RepoUser(
        id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    service = _build_authorization_service(repo)
    await repo.ensure_role("member")
    await service.set_role_tools(
        "member", ["get_current_time"], actor_email="admin@example.com"
    )
    user = AuthorizationUser(
        user_id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        roles=[],
        tools=[],
        direct_tools=[],
    )

    assert await service.authorize_tool_access(user, "get_current_time") is False

    updated = await service.set_user_roles(
        user_id, ["member"], actor_email="admin@example.com"
    )
    assert updated is not None

    updated_user = AuthorizationUser(
        user_id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        roles=updated.roles,
        tools=updated.tools,
        direct_tools=updated.direct_tools,
    )
    assert await service.authorize_tool_access(updated_user, "get_current_time") is True


async def test_authorization_service_writes_audit_events_for_admin_changes() -> None:
    repo = _InMemoryAuthorizationRepository()
    user_id = uuid4()
    repo.users[user_id] = _RepoUser(
        id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    service = _build_authorization_service(repo)
    await repo.ensure_role("member")

    await service.set_user_active(
        user_id, is_active=False, actor_email="admin@example.com"
    )
    await service.set_role_tools(
        "member", ["get_current_time"], actor_email="admin@example.com"
    )

    assert [event["event_type"] for event in repo.audit_events] == [
        "admin_user_status_updated",
        "admin_role_tools_updated",
    ]


async def test_authorization_service_blocks_disabling_last_active_admin() -> None:
    repo = _InMemoryAuthorizationRepository()
    admin_id = uuid4()
    repo.users[admin_id] = _RepoUser(
        id=admin_id,
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.user_roles[admin_id] = {"admin"}
    service = _build_authorization_service(repo)

    try:
        await service.set_user_active(
            admin_id, is_active=False, actor_email="owner@example.com"
        )
        assert False, "Expected LastActiveAdminError"
    except LastActiveAdminError:
        pass


async def test_authorization_service_blocks_admin_self_deactivation() -> None:
    repo = _InMemoryAuthorizationRepository()
    admin_id = uuid4()
    other_admin_id = uuid4()
    repo.users[admin_id] = _RepoUser(
        id=admin_id,
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.users[other_admin_id] = _RepoUser(
        id=other_admin_id,
        email="backup-admin@example.com",
        display_name="Backup Admin",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.user_roles[admin_id] = {"admin"}
    repo.user_roles[other_admin_id] = {"admin"}
    service = _build_authorization_service(repo)

    try:
        await service.set_user_active(
            admin_id,
            is_active=False,
            actor_email="admin@example.com",
            actor_user_id=admin_id,
        )
        assert False, "Expected SelfDeactivateAdminError"
    except SelfDeactivateAdminError:
        pass


async def test_admin_routes_forbid_non_admin_users() -> None:
    app = _create_admin_app()
    app.dependency_overrides[get_authorization_service] = lambda: (
        _FakeAuthorizationService()
    )
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="member@example.com",
        display_name="Member",
        is_active=True,
        roles=["member"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        users_response = await client.get("/admin/users")
        tools_response = await client.get("/admin/tools")

    assert users_response.status_code == 403
    assert tools_response.status_code == 403


async def test_admin_route_requires_bearer_token_with_stable_error_code() -> None:
    app = _create_admin_app()
    app.dependency_overrides[get_authorization_service] = lambda: (
        _FakeAuthorizationService()
    )
    app.dependency_overrides[get_auth_service] = lambda: (
        _FakeProtectedRouteAuthService()
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeProtectedRouteJWTService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/users", headers={"x-request-id": "admin-missing-bearer"}
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Missing bearer token"
    assert body["error_code"] == MISSING_BEARER_TOKEN
    assert body["request_id"] == "admin-missing-bearer"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_admin_route_rejects_invalid_bearer_token_with_error_code() -> None:
    app = _create_admin_app()
    app.dependency_overrides[get_authorization_service] = lambda: (
        _FakeAuthorizationService()
    )
    app.dependency_overrides[get_auth_service] = lambda: (
        _FakeProtectedRouteAuthService()
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeProtectedRouteJWTService(
        invalid=True
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/users",
            headers={
                "Authorization": "Bearer bad-token",
                "x-request-id": "admin-invalid-token",
            },
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Invalid token"
    assert body["error_code"] == INVALID_TOKEN
    assert body["request_id"] == "admin-invalid-token"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_admin_route_uses_admin_access_contract_for_inactive_user() -> None:
    app = _create_admin_app()
    app.dependency_overrides[get_authorization_service] = lambda: (
        _FakeAuthorizationService()
    )
    app.dependency_overrides[get_auth_service] = lambda: _FakeProtectedRouteAuthService(
        is_active=False
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeProtectedRouteJWTService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/users",
            headers={
                "Authorization": "Bearer good-token",
                "x-request-id": "admin-inactive-user",
            },
        )

    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "Admin access required"
    assert body["error_code"] == "admin_access_required"
    assert body["request_id"] == "admin-inactive-user"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_admin_routes_record_current_user_telemetry_for_protected_requests() -> (
    None
):
    app = _create_admin_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder
    app.dependency_overrides[get_authorization_service] = lambda: (
        _FakeAuthorizationService()
    )
    app.dependency_overrides[get_auth_service] = lambda: (
        _FakeProtectedRouteAuthService()
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[get_jwt_service] = lambda: (
            _FakeProtectedRouteJWTService()
        )
        success_response = await client.get(
            "/admin/users", headers={"Authorization": "Bearer good-token"}
        )

        app.dependency_overrides[get_jwt_service] = lambda: (
            _FakeProtectedRouteJWTService(invalid=True)
        )
        invalid_response = await client.get(
            "/admin/users", headers={"Authorization": "Bearer bad-token"}
        )

    assert success_response.status_code == 200
    assert invalid_response.status_code == 401

    resolved_events = [
        event
        for event in recorder.trace_events
        if event.name == "auth_current_user_resolved"
    ]
    assert len(resolved_events) == 1
    assert resolved_events[0].attributes["user_email"] == "admin@example.com"
    assert isinstance(resolved_events[0].attributes["user_id"], str)

    rejection_events = [
        event
        for event in recorder.trace_events
        if event.name == "auth_current_user_rejected"
    ]
    assert rejection_events == [
        TelemetryEvent(
            name="auth_current_user_rejected",
            attributes={
                "status_code": 401,
                "error_code": INVALID_TOKEN,
                "failure_stage": "jwt_decode",
            },
        )
    ]

    auth_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "auth.outcomes.total"
    ]
    assert auth_metric_events == [
        (
            TelemetryEvent(
                name="auth.outcomes.total",
                attributes={
                    "event_name": "auth_current_user_rejected",
                    "status_code": 401,
                    "error_code": INVALID_TOKEN,
                    "failure_stage": "jwt_decode",
                },
            ),
            1,
        )
    ]
    assert recorder.report_events == []
    assert all(event.name != "auth_me_succeeded" for event in recorder.trace_events)


async def test_admin_routes_allow_admin_management_operations() -> None:
    app = _create_admin_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder
    service = _FakeAuthorizationService()
    admin_user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: admin_user

    with _capture_structured_logs() as stream:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            users_response = await client.get("/admin/users")
            tools_response = await client.get("/admin/tools")
            patch_response = await client.patch(
                f"/admin/users/{service.target_user_id}",
                json={"is_active": False},
            )
            put_response = await client.put(
                f"/admin/users/{service.target_user_id}/tools",
                json={"tools": ["get_current_time", "get_current_date"]},
            )

    assert users_response.status_code == 200
    assert users_response.json()["users"][0]["email"] == "member@example.com"

    assert tools_response.status_code == 200
    assert tools_response.json() == {"tools": ["get_current_time", "get_current_date"]}

    assert patch_response.status_code == 200
    assert patch_response.json()["user"]["is_active"] is False
    assert service.last_is_active is False

    assert put_response.status_code == 410
    put_body = put_response.json()
    assert put_body["detail"] == "Direct tool grants are disabled"
    assert put_body["error_code"] == DIRECT_TOOL_GRANTS_DISABLED
    assert service.last_set_tools is None

    payloads = _load_log_payloads(stream)

    user_list_events = [
        payload
        for payload in payloads
        if payload["event"] == "admin_users_list_succeeded"
    ]
    assert len(user_list_events) == 1
    assert user_list_events[0]["user_count"] == 1
    assert user_list_events[0]["user_id"] == str(admin_user.user_id)
    assert user_list_events[0]["user_email"] == admin_user.email

    tools_list_events = [
        payload
        for payload in payloads
        if payload["event"] == "admin_tools_list_succeeded"
    ]
    assert len(tools_list_events) == 1
    assert tools_list_events[0]["tool_count"] == 2
    assert tools_list_events[0]["user_id"] == str(admin_user.user_id)
    assert tools_list_events[0]["user_email"] == admin_user.email

    status_update_events = [
        payload
        for payload in payloads
        if payload["event"] == "admin_user_status_updated"
    ]
    assert len(status_update_events) == 1
    assert status_update_events[0]["is_active"] is False
    assert status_update_events[0]["user_id"] == str(admin_user.user_id)
    assert status_update_events[0]["target_user_id"] == str(service.target_user_id)

    active_update_events = [
        payload
        for payload in payloads
        if payload["event"] == "admin_user_active_updated"
    ]
    assert active_update_events == []

    disabled_events = [
        payload
        for payload in payloads
        if payload["event"] == "admin_direct_tool_grants_disabled"
    ]
    assert len(disabled_events) == 1
    assert disabled_events[0]["user_id"] == str(admin_user.user_id)
    assert (
        disabled_events[0]["request_path"]
        == f"/admin/users/{service.target_user_id}/tools"
    )

    route_trace_events = [
        event
        for event in recorder.trace_events
        if event.name != "api_request_completed"
    ]
    route_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "admin.outcomes.total"
    ]

    assert route_trace_events == [
        TelemetryEvent(
            name="admin_users_list_succeeded",
            attributes={
                "user_count": 1,
                "user_email": admin_user.email,
                "user_id": str(admin_user.user_id),
            },
        ),
        TelemetryEvent(
            name="admin_tools_list_succeeded",
            attributes={
                "tool_count": 2,
                "user_email": admin_user.email,
                "user_id": str(admin_user.user_id),
            },
        ),
        TelemetryEvent(
            name="admin_user_status_updated",
            attributes={
                "is_active": False,
                "target_user_id": str(service.target_user_id),
                "user_id": str(admin_user.user_id),
            },
        ),
        TelemetryEvent(
            name="admin_direct_tool_grants_disabled",
            attributes={
                "error_code": DIRECT_TOOL_GRANTS_DISABLED,
                "status_code": 410,
                "target_user_id": str(service.target_user_id),
                "user_id": str(admin_user.user_id),
            },
        ),
    ]
    assert route_metric_events == [
        (
            TelemetryEvent(
                name="admin.outcomes.total",
                attributes={
                    "event_name": "admin_users_list_succeeded",
                    "status_family": "2xx",
                },
            ),
            1,
        ),
        (
            TelemetryEvent(
                name="admin.outcomes.total",
                attributes={
                    "event_name": "admin_tools_list_succeeded",
                    "status_family": "2xx",
                },
            ),
            1,
        ),
        (
            TelemetryEvent(
                name="admin.outcomes.total",
                attributes={
                    "event_name": "admin_user_status_updated",
                    "status_family": "2xx",
                },
            ),
            1,
        ),
        (
            TelemetryEvent(
                name="admin.outcomes.total",
                attributes={
                    "error_code": DIRECT_TOOL_GRANTS_DISABLED,
                    "event_name": "admin_direct_tool_grants_disabled",
                    "status_family": "4xx",
                },
            ),
            1,
        ),
    ]
    assert recorder.report_events == []


async def test_admin_route_deletes_user() -> None:
    app = _create_admin_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder
    service = _FakeAuthorizationService()
    admin_user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/admin/users/{service.target_user_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert service.deleted_user_id == service.target_user_id

    route_trace = next(
        event for event in recorder.trace_events if event.name == "admin_user_deleted"
    )
    assert route_trace == TelemetryEvent(
        name="admin_user_deleted",
        attributes={
            "target_user_id": str(service.target_user_id),
            "user_id": str(admin_user.user_id),
        },
    )


async def test_admin_route_blocks_self_delete_with_409() -> None:
    app = _create_admin_app()

    class _SelfDeleteService(_FakeAuthorizationService):
        async def delete_user(
            self,
            user_id: UUID,
            *,
            actor_email: str | None = None,
            actor_user_id: UUID | None = None,
        ) -> AuthorizationUser | None:
            raise SelfDeleteAdminError("Admins cannot delete their own account")

    service = _SelfDeleteService()
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=service.target_user_id,
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/admin/users/{service.target_user_id}",
            headers={"x-request-id": "admin-self-delete"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "Admins cannot delete their own account"
    assert body["error_code"] == "self_delete_admin"
    assert body["request_id"] == "admin-self-delete"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_admin_route_disables_direct_tool_grants_with_410() -> None:
    app = _create_admin_app()
    service = _FakeAuthorizationService()
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/admin/users/{service.target_user_id}/tools",
            json={"tools": ["get_current_time", "unknown-tool"]},
            headers={"x-request-id": "admin-unknown-tools"},
        )

    assert response.status_code == 410
    body = response.json()
    assert body["detail"] == "Direct tool grants are disabled"
    assert body["error_code"] == DIRECT_TOOL_GRANTS_DISABLED
    assert body["request_id"] == "admin-unknown-tools"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_admin_route_returns_error_code_for_missing_user_404() -> None:
    app = _create_admin_app()
    service = _FakeAuthorizationService()
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/admin/users/{uuid4()}",
            json={"is_active": False},
            headers={"x-request-id": "admin-user-missing"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "User not found"
    assert body["error_code"] == "admin_user_not_found"
    assert body["request_id"] == "admin-user-missing"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_admin_route_blocks_disabling_last_active_admin_with_409() -> None:
    app = _create_admin_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder

    repo = _InMemoryAuthorizationRepository()
    admin_id = uuid4()
    repo.users[admin_id] = _RepoUser(
        id=admin_id,
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.user_roles[admin_id] = {"admin"}
    service = _build_authorization_service(repo)
    admin_user = AuthorizationUser(
        user_id=uuid4(),
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/admin/users/{admin_id}",
            json={"is_active": False},
            headers={"x-request-id": "admin-last-active"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "Cannot disable the last active admin"
    assert body["error_code"] == "last_active_admin"
    assert body["request_id"] == "admin-last-active"
    assert response.headers["x-request-id"] == body["request_id"]

    route_trace_events = [
        event
        for event in recorder.trace_events
        if event.name != "api_request_completed"
    ]
    route_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "admin.outcomes.total"
    ]

    assert route_trace_events == [
        TelemetryEvent(
            name="admin_last_active_admin_conflict",
            attributes={
                "error_code": "last_active_admin",
                "status_code": 409,
                "target_user_id": str(admin_id),
                "user_id": str(admin_user.user_id),
            },
        )
    ]
    assert route_metric_events == [
        (
            TelemetryEvent(
                name="admin.outcomes.total",
                attributes={
                    "error_code": "last_active_admin",
                    "event_name": "admin_last_active_admin_conflict",
                    "status_family": "4xx",
                },
            ),
            1,
        )
    ]
    assert recorder.report_events == []


async def test_admin_role_endpoints_manage_roles_and_tools() -> None:
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    repo.roles.update({"admin", "user:legacy"})
    service = _build_authorization_service(repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post("/admin/roles", json={"name": "member"})
        assert create.status_code == 200
        assert create.json() == {"name": "member"}

        listed = await client.get("/admin/roles")
        assert listed.status_code == 200
        assert "user:legacy" not in listed.json()["roles"]
        assert "admin" in listed.json()["roles"]
        assert "member" in listed.json()["roles"]

        put_tools = await client.put(
            "/admin/roles/member/tools", json={"tools": ["get_current_time"]}
        )
        assert put_tools.status_code == 200
        assert put_tools.json() == {"tools": ["get_current_time"]}

        get_tools = await client.get("/admin/roles/member/tools")
        assert get_tools.status_code == 200
        assert get_tools.json() == {"tools": ["get_current_time"]}

        unknown_tools = await client.put(
            "/admin/roles/member/tools", json={"tools": ["unknown-tool"]}
        )
        assert unknown_tools.status_code == 400
        assert unknown_tools.json()["error_code"] == UNKNOWN_TOOLS

        reserved_edit = await client.put(
            "/admin/roles/admin/tools", json={"tools": ["get_current_time"]}
        )
        assert reserved_edit.status_code == 403
        assert reserved_edit.json()["error_code"] == RESERVED_ROLE

        deleted = await client.delete("/admin/roles/member")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}

        delete_reserved = await client.delete("/admin/roles/admin")
        assert delete_reserved.status_code == 403
        assert delete_reserved.json()["error_code"] == RESERVED_ROLE

        missing_role = await client.get("/admin/roles/nope/tools")
        assert missing_role.status_code == 404
        assert missing_role.json()["error_code"] == ADMIN_ROLE_NOT_FOUND


async def test_admin_role_endpoints_reject_invalid_role_name() -> None:
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    repo.roles.add("admin")
    service = _build_authorization_service(repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/admin/roles", json={"name": "bad role"})

    assert response.status_code == 400
    assert response.json()["error_code"] == INVALID_ROLE_NAME


async def test_admin_migration_direct_grants_converts_internal_roles_to_shared_roles() -> (
    None
):
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    tools = ["get_current_time", "get_current_date"]
    joined = "\n".join(sorted(tools))
    role_name = f"legacy_tools_{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:8]}"

    user_1 = uuid4()
    user_2 = uuid4()
    now = datetime.now(UTC)
    repo.users[user_1] = _RepoUser(
        id=user_1,
        email="a@example.com",
        display_name=None,
        is_active=True,
        created_at=now,
        last_login_at=None,
    )
    repo.users[user_2] = _RepoUser(
        id=user_2,
        email="b@example.com",
        display_name=None,
        is_active=True,
        created_at=now,
        last_login_at=None,
    )

    internal_role_1 = f"user:{user_1}"
    internal_role_2 = f"user:{user_2}"
    repo.roles.update({"admin", internal_role_1, internal_role_2})
    repo.user_roles[user_1] = {internal_role_1}
    repo.user_roles[user_2] = {internal_role_2}
    repo.role_tools[internal_role_1] = set(tools)
    repo.role_tools[internal_role_2] = set(tools)

    service = _build_authorization_service(repo)
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/admin/migrations/direct-grants")
        assert response.status_code == 200
        body = response.json()

        assert body["users_migrated"] == 2
        assert body["roles_created"] == 1
        assert body["roles_reused"] == 1
        assert body["internal_roles_deleted"] == 2
        assert body["tool_grant_count"] == 4
        assert body["created_roles"] == [role_name]

        second = await client.post("/admin/migrations/direct-grants")
        assert second.status_code == 200
        assert second.json()["users_migrated"] == 0

    assert role_name in repo.roles
    assert repo.role_tools[role_name] == set(tools)

    assert internal_role_1 not in repo.roles
    assert internal_role_2 not in repo.roles
    assert internal_role_1 not in repo.user_roles[user_1]
    assert internal_role_2 not in repo.user_roles[user_2]
    assert role_name in repo.user_roles[user_1]
    assert role_name in repo.user_roles[user_2]

    assert any(
        event["event_type"] == "admin_migration_direct_grants_completed"
        for event in repo.audit_events
    )


async def test_admin_user_roles_endpoint_replaces_non_internal_roles_and_preserves_internal() -> (
    None
):
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    target_user_id = uuid4()
    internal_role = f"user:{target_user_id}"
    repo.users[target_user_id] = _RepoUser(
        id=target_user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.roles.update({"admin", "member", "viewer"})
    repo.user_roles[target_user_id] = {"member", "viewer", internal_role}
    repo.role_tools["member"] = {"get_current_time"}
    repo.role_tools[internal_role] = {"get_current_date"}
    service = _build_authorization_service(repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/admin/users/{target_user_id}/roles",
            json={"roles": ["member"]},
        )

    assert response.status_code == 200
    body = response.json()["user"]
    assert "member" in body["roles"]
    assert "viewer" not in body["roles"]
    assert body["direct_tools"] == ["get_current_date"]
    assert body["tools"] == ["get_current_date", "get_current_time"]

    # Internal roles stay assigned (direct grants preserved).
    assert internal_role in repo.user_roles[target_user_id]


async def test_admin_user_roles_endpoint_blocks_internal_roles_in_payload() -> None:
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    target_user_id = uuid4()
    repo.users[target_user_id] = _RepoUser(
        id=target_user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.roles.add("admin")
    service = _build_authorization_service(repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/admin/users/{target_user_id}/roles",
            json={"roles": [f"user:{target_user_id}"]},
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == INTERNAL_ROLE_FORBIDDEN


async def test_admin_user_roles_endpoint_rejects_unknown_roles() -> None:
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    target_user_id = uuid4()
    repo.users[target_user_id] = _RepoUser(
        id=target_user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.roles.add("admin")
    service = _build_authorization_service(repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/admin/users/{target_user_id}/roles",
            json={"roles": ["member"]},
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == UNKNOWN_ROLES


async def test_admin_user_roles_endpoint_blocks_self_removing_admin_role() -> None:
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    admin_id = uuid4()
    repo.users[admin_id] = _RepoUser(
        id=admin_id,
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.roles.update({"admin", "member"})
    repo.user_roles[admin_id] = {"admin"}
    service = _build_authorization_service(repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=admin_id,
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/admin/users/{admin_id}/roles",
            json={"roles": ["member"]},
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == SELF_REMOVE_ADMIN_ROLE


async def test_admin_user_roles_endpoint_blocks_removing_admin_from_last_active_admin() -> (
    None
):
    app = _create_admin_app()

    repo = _InMemoryAuthorizationRepository()
    admin_id = uuid4()
    repo.users[admin_id] = _RepoUser(
        id=admin_id,
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )
    repo.roles.update({"admin", "member"})
    repo.user_roles[admin_id] = {"admin"}
    service = _build_authorization_service(repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["admin"],
        tools=[],
        direct_tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/admin/users/{admin_id}/roles",
            json={"roles": ["member"]},
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "last_active_admin"
