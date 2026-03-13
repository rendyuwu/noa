from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from noa_api.api.routes.admin import router as admin_router
from noa_api.core.auth.authorization import (
    AuthorizationService,
    AuthorizationUser,
    LastActiveAdminError,
    SelfDeactivateAdminError,
    UnknownToolError,
    get_authorization_service,
    get_current_auth_user,
)


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
                created_at=created_at,
                last_login_at=None,
            )
        ]
        self.all_tools = ["get_current_time", "get_current_date", "set_demo_flag"]
        self.last_set_tools: list[str] | None = None
        self.last_is_active: bool | None = None

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
        self.users[0].tools = sorted(set(tool_names))
        return self.users[0]


async def test_authorization_service_admin_requires_explicit_tool_permissions() -> None:
    repo = _InMemoryAuthorizationRepository()
    service = AuthorizationService(repository=repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is False


async def test_authorization_service_admin_allows_when_role_grants_tool() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["admin"] = {"get_current_time"}
    service = AuthorizationService(repository=repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is True


async def test_authorization_service_disabled_user_has_zero_permissions() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["member"] = {"get_current_time"}
    service = AuthorizationService(repository=repo)
    allowed = await service.authorize_tool_access(
        AuthorizationUser(
            user_id=uuid4(),
            email="disabled@example.com",
            display_name="Disabled",
            is_active=False,
            roles=["admin", "member"],
            tools=[],
        ),
        "get_current_time",
    )
    assert allowed is False


async def test_authorization_service_non_admin_depends_on_tool_permissions() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["member"] = {"get_current_time"}
    service = AuthorizationService(repository=repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="member@example.com",
        display_name="Member",
        is_active=True,
        roles=["member"],
        tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is True
    assert await service.authorize_tool_access(user, "set_demo_flag") is False


async def test_authorization_service_lists_canonical_tools() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["member"] = {"db-only"}
    service = AuthorizationService(repository=repo)

    assert await service.list_tools() == [
        "get_current_time",
        "get_current_date",
        "set_demo_flag",
        "update_workflow_todo",
        "whm_list_servers",
        "whm_validate_server",
        "whm_list_accounts",
        "whm_search_accounts",
        "whm_preflight_account",
        "whm_preflight_csf_entries",
        "whm_suspend_account",
        "whm_unsuspend_account",
        "whm_change_contact_email",
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
    service = AuthorizationService(repository=repo)

    users = await service.list_users()
    assert users[0].created_at == now
    assert users[0].last_login_at is None


async def test_authorization_service_rejects_unknown_tool_updates() -> None:
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
    service = AuthorizationService(repository=repo)

    try:
        await service.set_user_tools(
            user_id,
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
    service = AuthorizationService(repository=repo)
    user = AuthorizationUser(
        user_id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        roles=[],
        tools=[],
    )

    assert await service.authorize_tool_access(user, "get_current_time") is False

    updated = await service.set_user_tools(
        user_id, ["get_current_time"], actor_email="admin@example.com"
    )
    assert updated is not None

    updated_user = AuthorizationUser(
        user_id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        roles=updated.roles,
        tools=updated.tools,
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
    service = AuthorizationService(repository=repo)

    await service.set_user_active(
        user_id, is_active=False, actor_email="admin@example.com"
    )
    await service.set_user_tools(
        user_id, ["get_current_time"], actor_email="admin@example.com"
    )

    assert [event["event_type"] for event in repo.audit_events] == [
        "admin_user_status_updated",
        "admin_user_tools_updated",
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
    service = AuthorizationService(repository=repo)

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
    service = AuthorizationService(repository=repo)

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
    app = FastAPI()
    app.include_router(admin_router)
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
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        users_response = await client.get("/admin/users")
        tools_response = await client.get("/admin/tools")

    assert users_response.status_code == 403
    assert tools_response.status_code == 403


async def test_admin_routes_allow_admin_management_operations() -> None:
    app = FastAPI()
    app.include_router(admin_router)
    service = _FakeAuthorizationService()
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
    )

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
            json={"tools": ["set_demo_flag", "get_current_date"]},
        )

    assert users_response.status_code == 200
    assert users_response.json()["users"][0]["email"] == "member@example.com"

    assert tools_response.status_code == 200
    assert tools_response.json() == {
        "tools": ["get_current_time", "get_current_date", "set_demo_flag"]
    }

    assert patch_response.status_code == 200
    assert patch_response.json()["user"]["is_active"] is False
    assert service.last_is_active is False

    assert put_response.status_code == 200
    assert put_response.json()["user"]["tools"] == ["get_current_date", "set_demo_flag"]
    assert service.last_set_tools == ["set_demo_flag", "get_current_date"]


async def test_admin_route_rejects_unknown_tools_with_400() -> None:
    app = FastAPI()
    app.include_router(admin_router)
    service = _FakeAuthorizationService()
    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/admin/users/{service.target_user_id}/tools",
            json={"tools": ["get_current_time", "unknown-tool"]},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown tools: unknown-tool"}


async def test_admin_route_blocks_disabling_last_active_admin_with_409() -> None:
    app = FastAPI()
    app.include_router(admin_router)

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
    service = AuthorizationService(repository=repo)

    app.dependency_overrides[get_authorization_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["admin"],
        tools=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/admin/users/{admin_id}", json={"is_active": False}
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Cannot disable the last active admin"}
