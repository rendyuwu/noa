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

import noa_api.api.auth_dependencies as auth_dependencies
import noa_api.api.routes.auth as auth_routes
from noa_api.api.error_codes import (
    AUTHENTICATION_SERVICE_UNAVAILABLE,
    INVALID_CREDENTIALS,
    INVALID_TOKEN,
    MISSING_BEARER_TOKEN,
    USER_PENDING_APPROVAL,
)
from noa_api.api.error_handling import install_error_handling
from noa_api.api.routes.auth import router as auth_router
from noa_api.core.auth.auth_service import AuthResult, AuthService, AuthUser
from noa_api.core.auth.errors import (
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
)
from noa_api.core.auth.deps import get_auth_service, get_jwt_service
from noa_api.core.auth.ldap_service import LDAPService, LdapUser
from noa_api.core.config import Settings
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


def _settings(**kwargs: Any) -> Settings:
    return Settings(**kwargs, _env_file=None)  # type: ignore[call-arg]


@dataclass
class _User:
    id: UUID
    email: str
    ldap_dn: str | None = None
    display_name: str | None = None
    is_active: bool = False
    last_login_at: datetime | None = None


class _InMemoryAuthRepository:
    def __init__(self) -> None:
        self.users: dict[str, _User] = {}
        self.roles: set[str] = set()
        self.user_roles: dict[UUID, set[str]] = {}

    async def get_user_by_email(self, email: str) -> _User | None:
        return self.users.get(email)

    async def create_user(
        self,
        *,
        email: str,
        ldap_dn: str | None,
        display_name: str | None,
        is_active: bool,
    ) -> _User:
        user = _User(
            id=uuid4(),
            email=email,
            ldap_dn=ldap_dn,
            display_name=display_name,
            is_active=is_active,
        )
        self.users[email] = user
        return user

    async def update_user(
        self,
        user: _User,
        *,
        ldap_dn: str | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
        last_login_at: datetime | None = None,
    ) -> _User:
        user.ldap_dn = ldap_dn if ldap_dn is not None else user.ldap_dn
        user.display_name = (
            display_name if display_name is not None else user.display_name
        )
        user.is_active = is_active if is_active is not None else user.is_active
        user.last_login_at = (
            last_login_at if last_login_at is not None else user.last_login_at
        )
        return user

    async def ensure_role(self, name: str) -> str:
        self.roles.add(name)
        return name

    async def assign_role(self, user_id: UUID, role_name: str) -> None:
        self.user_roles.setdefault(user_id, set()).add(role_name)

    async def get_role_names(self, user_id: UUID) -> list[str]:
        return sorted(self.user_roles.get(user_id, set()))


class _FakeLDAPService:
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid

    async def authenticate(self, email: str, password: str) -> LdapUser:
        if self.invalid:
            raise AuthInvalidCredentialsError("Invalid credentials")
        return LdapUser(email=email, dn=f"CN={email}", display_name="Example User")


class _FakeJWTService:
    def create_access_token(self, email: str, user_id: UUID) -> tuple[str, int]:
        return f"token:{email}:{user_id}", 3600

    def decode_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "user@example.com"}
        if token == "inactive-token":
            return {"sub": "inactive@example.com"}
        raise AuthInvalidCredentialsError("Invalid token")


class _FakeRouteAuthService:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode

    async def authenticate(self, *, email: str, password: str) -> AuthResult:
        if self.mode == "invalid":
            raise AuthInvalidCredentialsError("Invalid credentials")
        if self.mode == "pending":
            raise AuthPendingApprovalError("Pending approval")
        if self.mode == "auth_error":
            raise AuthError("LDAP authentication failed")

        return AuthResult(
            access_token="jwt-token",
            expires_in=3600,
            user_id=uuid4(),
            email=email,
            display_name="Route User",
            is_active=True,
            roles=["admin"],
        )

    async def get_user_by_email(self, *, email: str) -> AuthUser:
        if email == "inactive@example.com":
            return AuthUser(
                user_id=uuid4(),
                email=email,
                display_name="Inactive User",
                is_active=False,
                roles=["viewer"],
            )
        return AuthUser(
            user_id=uuid4(),
            email=email,
            display_name="Route User",
            is_active=True,
            roles=["admin"],
        )


def _create_auth_app() -> FastAPI:
    app = FastAPI()
    install_error_handling(app)
    app.include_router(auth_router)
    return app


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


async def test_auth_service_auto_provisions_pending_user() -> None:
    repo = _InMemoryAuthRepository()
    service = AuthService(
        auth_repository=repo,
        ldap_service=cast(Any, _FakeLDAPService()),
        jwt_service=cast(Any, _FakeJWTService()),
        bootstrap_admin_emails=set(),
    )

    try:
        await service.authenticate(email="new.user@example.com", password="secret")
        assert False, "Expected AuthPendingApprovalError"
    except AuthPendingApprovalError:
        created = repo.users["new.user@example.com"]
        assert created.is_active is False
        assert created.display_name == "Example User"


async def test_auth_service_bootstrap_admin_auto_active_and_issues_jwt() -> None:
    repo = _InMemoryAuthRepository()
    service = AuthService(
        auth_repository=repo,
        ldap_service=cast(Any, _FakeLDAPService()),
        jwt_service=cast(Any, _FakeJWTService()),
        bootstrap_admin_emails={"admin@example.com"},
    )

    result = await service.authenticate(email="admin@example.com", password="secret")

    assert result.access_token.startswith("token:admin@example.com:")
    assert result.is_active is True
    assert "admin" in result.roles
    assert repo.users["admin@example.com"].is_active is True
    assert repo.users["admin@example.com"].last_login_at is not None


async def test_auth_service_smoke_bootstrap_user_uses_dev_ldap_bypass() -> None:
    repo = _InMemoryAuthRepository()
    cfg = _settings(
        environment="development",
        ldap_server_uri="ldap://127.0.0.1:1",
        ldap_timeout_seconds=1,
        auth_dev_bypass_ldap=True,
    )
    service = AuthService(
        auth_repository=repo,
        ldap_service=LDAPService(cfg),
        jwt_service=cast(Any, _FakeJWTService()),
        bootstrap_admin_emails={"smoke@example.com"},
    )

    result = await service.authenticate(email="smoke@example.com", password="secret")

    assert result.access_token.startswith("token:smoke@example.com:")
    assert result.email == "smoke@example.com"
    assert result.is_active is True
    assert result.roles == ["admin"]
    assert repo.users["smoke@example.com"].is_active is True
    assert repo.users["smoke@example.com"].ldap_dn is not None
    assert "smoke@example.com" in repo.users["smoke@example.com"].ldap_dn
    assert repo.users["smoke@example.com"].last_login_at is not None


async def test_auth_service_updates_last_login_at_for_existing_active_user() -> None:
    repo = _InMemoryAuthRepository()
    existing_user = await repo.create_user(
        email="active.user@example.com",
        ldap_dn="CN=active.user@example.com",
        display_name="Existing User",
        is_active=True,
    )
    existing_user.last_login_at = datetime(2024, 1, 1, tzinfo=UTC)

    service = AuthService(
        auth_repository=repo,
        ldap_service=cast(Any, _FakeLDAPService()),
        jwt_service=cast(Any, _FakeJWTService()),
        bootstrap_admin_emails={"admin@example.com"},
    )

    await service.authenticate(email="active.user@example.com", password="secret")

    updated_user = repo.users["active.user@example.com"]
    assert updated_user.last_login_at is not None
    assert updated_user.last_login_at > datetime(2024, 1, 1, tzinfo=UTC)
    assert updated_user.last_login_at.tzinfo is not None


async def test_login_route_maps_auth_errors_and_success() -> None:
    app = _create_auth_app()

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="invalid"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "bad"}
        )
    assert invalid_response.status_code == 401
    invalid_body = invalid_response.json()
    assert invalid_body["detail"] == "Invalid credentials"
    assert invalid_body["error_code"] == INVALID_CREDENTIALS
    assert isinstance(invalid_body["request_id"], str)
    assert invalid_response.headers["x-request-id"] == invalid_body["request_id"]

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="pending"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pending_response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )
    assert pending_response.status_code == 403
    pending_body = pending_response.json()
    assert pending_body["detail"] == "User pending approval"
    assert pending_body["error_code"] == USER_PENDING_APPROVAL
    assert isinstance(pending_body["request_id"], str)
    assert pending_response.headers["x-request-id"] == pending_body["request_id"]

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="auth_error"
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unavailable_response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )
    assert unavailable_response.status_code == 503
    unavailable_body = unavailable_response.json()
    assert unavailable_body["detail"] == "Authentication service unavailable"
    assert unavailable_body["error_code"] == AUTHENTICATION_SERVICE_UNAVAILABLE
    assert isinstance(unavailable_body["request_id"], str)
    assert (
        unavailable_response.headers["x-request-id"] == unavailable_body["request_id"]
    )

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        success_response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )
    assert success_response.status_code == 200
    payload = success_response.json()
    assert payload["access_token"] == "jwt-token"
    assert payload["token_type"] == "bearer"
    assert payload["user"]["email"] == "user@example.com"


async def test_auth_routes_emit_structured_auth_boundary_logs() -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()

    with _capture_structured_logs() as stream:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login_response = await client.post(
                "/auth/login", json={"email": "user@example.com", "password": "ok"}
            )
            me_response = await client.get(
                "/auth/me", headers={"Authorization": "Bearer good-token"}
            )
            invalid_response = await client.get(
                "/auth/me", headers={"Authorization": "Bearer bad-token"}
            )

    assert login_response.status_code == 200
    assert me_response.status_code == 200
    assert invalid_response.status_code == 401

    payloads = _load_log_payloads(stream)
    success_events = [
        payload for payload in payloads if payload["event"] == "auth_login_succeeded"
    ]
    assert len(success_events) == 1
    success_payload = success_events[0]
    assert success_payload["user_email"] == "user@example.com"
    assert isinstance(success_payload["user_id"], str)
    assert success_payload["roles"] == ["admin"]
    assert success_payload["is_active"] is True
    assert success_payload["request_path"] == "/auth/login"
    assert "password" not in success_payload
    assert "access_token" not in success_payload

    resolved_events = [
        payload
        for payload in payloads
        if payload["event"] == "auth_current_user_resolved"
    ]
    assert len(resolved_events) == 1
    resolved_payload = resolved_events[0]
    assert resolved_payload["user_email"] == "user@example.com"
    assert isinstance(resolved_payload["user_id"], str)
    assert resolved_payload["roles"] == ["admin"]
    assert resolved_payload["is_active"] is True
    assert resolved_payload["request_path"] == "/auth/me"

    me_success_events = [
        payload for payload in payloads if payload["event"] == "auth_me_succeeded"
    ]
    assert len(me_success_events) == 1
    me_success_payload = me_success_events[0]
    assert me_success_payload["user_email"] == "user@example.com"
    assert isinstance(me_success_payload["user_id"], str)
    assert me_success_payload["roles"] == ["admin"]
    assert me_success_payload["is_active"] is True
    assert me_success_payload["request_path"] == "/auth/me"

    rejection_events = [
        payload
        for payload in payloads
        if payload["event"] == "auth_current_user_rejected"
    ]
    assert len(rejection_events) == 1
    rejection_payload = rejection_events[0]
    assert rejection_payload["status_code"] == 401
    assert rejection_payload["error_code"] == INVALID_TOKEN
    assert rejection_payload["failure_stage"] == "jwt_decode"
    assert rejection_payload["request_path"] == "/auth/me"
    assert "authorization" not in rejection_payload


async def test_auth_routes_record_success_telemetry_with_bounded_metrics() -> None:
    app = _create_auth_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )
        me_response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer good-token"}
        )

    assert login_response.status_code == 200
    assert me_response.status_code == 200

    login_success_events = [
        event for event in recorder.trace_events if event.name == "auth_login_succeeded"
    ]
    assert len(login_success_events) == 1
    assert login_success_events[0].attributes["user_email"] == "user@example.com"
    assert isinstance(login_success_events[0].attributes["user_id"], str)

    resolved_events = [
        event
        for event in recorder.trace_events
        if event.name == "auth_current_user_resolved"
    ]
    assert len(resolved_events) == 1
    assert resolved_events[0].attributes["user_email"] == "user@example.com"
    assert isinstance(resolved_events[0].attributes["user_id"], str)

    me_success_events = [
        event for event in recorder.trace_events if event.name == "auth_me_succeeded"
    ]
    assert len(me_success_events) == 1
    assert me_success_events[0].attributes["user_email"] == "user@example.com"
    assert isinstance(me_success_events[0].attributes["user_id"], str)

    auth_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "auth.outcomes.total"
    ]
    assert len(auth_metric_events) == 2
    assert any(
        event.attributes == {"event_name": "auth_login_succeeded"} and value == 1
        for event, value in auth_metric_events
    )
    assert any(
        event.attributes == {"event_name": "auth_me_succeeded"} and value == 1
        for event, value in auth_metric_events
    )
    assert all(
        "user_email" not in event.attributes and "user_id" not in event.attributes
        for event, _ in auth_metric_events
    )
    assert recorder.report_events == []


async def test_auth_routes_record_rejection_telemetry_and_report_auth_outages() -> None:
    app = _create_auth_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
            mode="invalid"
        )
        invalid_login_response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "bad"}
        )

        app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
            mode="auth_error"
        )
        unavailable_login_response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )

        app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
            mode="ok"
        )
        app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()
        invalid_token_response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer bad-token"}
        )

    assert invalid_login_response.status_code == 401
    assert unavailable_login_response.status_code == 503
    assert invalid_token_response.status_code == 401

    login_rejection_events = [
        event for event in recorder.trace_events if event.name == "auth_login_rejected"
    ]
    assert len(login_rejection_events) == 2

    invalid_login_event = next(
        event
        for event in login_rejection_events
        if event.attributes["error_code"] == INVALID_CREDENTIALS
    )
    assert invalid_login_event.attributes == {
        "status_code": 401,
        "error_code": INVALID_CREDENTIALS,
        "failure_stage": "invalid_credentials",
        "user_email": "user@example.com",
    }

    unavailable_login_event = next(
        event
        for event in login_rejection_events
        if event.attributes["error_code"] == AUTHENTICATION_SERVICE_UNAVAILABLE
    )
    assert unavailable_login_event.attributes == {
        "status_code": 503,
        "error_code": AUTHENTICATION_SERVICE_UNAVAILABLE,
        "failure_stage": "auth_service",
        "user_email": "user@example.com",
    }

    current_user_rejection_events = [
        event
        for event in recorder.trace_events
        if event.name == "auth_current_user_rejected"
    ]
    assert len(current_user_rejection_events) == 1
    assert current_user_rejection_events[0].attributes == {
        "status_code": 401,
        "error_code": INVALID_TOKEN,
        "failure_stage": "jwt_decode",
    }

    auth_metric_events = [
        (event, value)
        for event, value in recorder.metric_events
        if event.name == "auth.outcomes.total"
    ]
    assert len(auth_metric_events) == 3
    assert any(
        event.attributes
        == {
            "event_name": "auth_login_rejected",
            "status_code": 401,
            "error_code": INVALID_CREDENTIALS,
            "failure_stage": "invalid_credentials",
        }
        and value == 1
        for event, value in auth_metric_events
    )
    assert any(
        event.attributes
        == {
            "event_name": "auth_login_rejected",
            "status_code": 503,
            "error_code": AUTHENTICATION_SERVICE_UNAVAILABLE,
            "failure_stage": "auth_service",
        }
        and value == 1
        for event, value in auth_metric_events
    )
    assert any(
        event.attributes
        == {
            "event_name": "auth_current_user_rejected",
            "status_code": 401,
            "error_code": INVALID_TOKEN,
            "failure_stage": "jwt_decode",
        }
        and value == 1
        for event, value in auth_metric_events
    )
    assert all(
        "user_email" not in event.attributes and "user_id" not in event.attributes
        for event, _ in auth_metric_events
    )

    report_events = [
        event
        for event, detail in recorder.report_events
        if detail is None and event.name == "auth_login_rejected"
    ]
    assert report_events == [
        TelemetryEvent(
            name="auth_login_rejected",
            attributes={
                "status_code": 503,
                "error_code": AUTHENTICATION_SERVICE_UNAVAILABLE,
                "failure_stage": "auth_service",
            },
        )
    ]


async def test_login_route_emits_structured_rejection_log() -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="invalid"
    )

    with _capture_structured_logs() as stream:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/auth/login", json={"email": "user@example.com", "password": "bad"}
            )

    assert response.status_code == 401

    payloads = _load_log_payloads(stream)
    rejection_events = [
        payload for payload in payloads if payload["event"] == "auth_login_rejected"
    ]
    assert len(rejection_events) == 1
    rejection_payload = rejection_events[0]
    assert rejection_payload["status_code"] == 401
    assert rejection_payload["error_code"] == INVALID_CREDENTIALS
    assert rejection_payload["failure_stage"] == "invalid_credentials"
    assert rejection_payload["request_path"] == "/auth/login"
    assert rejection_payload["user_email"] == "user@example.com"
    assert "password" not in rejection_payload
    assert "access_token" not in rejection_payload


async def test_me_route_emits_auth_boundary_rejection_log_for_inactive_user() -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()

    with _capture_structured_logs() as stream:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": "Bearer inactive-token"}
            )

    assert response.status_code == 403

    payloads = _load_log_payloads(stream)
    rejection_events = [
        payload
        for payload in payloads
        if payload["event"] == "auth_current_user_rejected"
    ]
    assert len(rejection_events) == 1
    rejection_payload = rejection_events[0]
    assert rejection_payload["status_code"] == 403
    assert rejection_payload["error_code"] == USER_PENDING_APPROVAL
    assert rejection_payload["failure_stage"] == "inactive_user"
    assert rejection_payload["request_path"] == "/auth/me"
    assert rejection_payload["user_email"] == "inactive@example.com"
    assert isinstance(rejection_payload["user_id"], str)

    me_success_events = [
        payload for payload in payloads if payload["event"] == "auth_me_succeeded"
    ]
    assert me_success_events == []


async def test_login_route_uses_shared_invalid_credentials_error_code(
    monkeypatch,
) -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="invalid"
    )
    monkeypatch.setattr(
        auth_routes,
        "INVALID_CREDENTIALS",
        "catalog_invalid_credentials",
        raising=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "bad"}
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Invalid credentials"
    assert body["error_code"] == "catalog_invalid_credentials"


async def test_login_route_uses_shared_user_pending_approval_error_code(
    monkeypatch,
) -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="pending"
    )
    monkeypatch.setattr(
        auth_routes,
        "USER_PENDING_APPROVAL",
        "catalog_user_pending_approval",
        raising=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )

    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "User pending approval"
    assert body["error_code"] == "catalog_user_pending_approval"


async def test_login_route_uses_shared_auth_service_unavailable_error_code(
    monkeypatch,
) -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="auth_error"
    )
    monkeypatch.setattr(
        auth_routes,
        "AUTHENTICATION_SERVICE_UNAVAILABLE",
        "catalog_authentication_service_unavailable",
        raising=False,
    )

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "Authentication service unavailable"
    assert body["error_code"] == "catalog_authentication_service_unavailable"


async def test_login_route_maps_auth_service_failure_to_503() -> None:
    app = _create_auth_app()

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="auth_error"
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/login", json={"email": "user@example.com", "password": "ok"}
        )

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "Authentication service unavailable"
    assert body["error_code"] == AUTHENTICATION_SERVICE_UNAVAILABLE
    assert isinstance(body["request_id"], str)
    assert response.headers["x-request-id"] == body["request_id"]


async def test_me_route_returns_user_payload_for_valid_bearer_token() -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer good-token"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "user@example.com"
    assert payload["user"]["roles"] == ["admin"]


async def test_me_route_rejects_invalid_token() -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer bad-token"}
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Invalid token"
    assert body["error_code"] == INVALID_TOKEN
    assert isinstance(body["request_id"], str)
    assert response.headers["x-request-id"] == body["request_id"]


async def test_me_route_uses_shared_invalid_token_error_code(monkeypatch) -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()
    monkeypatch.setattr(
        auth_dependencies,
        "INVALID_TOKEN",
        "catalog_invalid_token",
        raising=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer bad-token"}
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Invalid token"
    assert body["error_code"] == "catalog_invalid_token"


async def test_me_route_uses_shared_missing_bearer_token_error_code(
    monkeypatch,
) -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()
    monkeypatch.setattr(
        auth_dependencies,
        "MISSING_BEARER_TOKEN",
        "catalog_missing_bearer_token",
        raising=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/me")

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Missing bearer token"
    assert body["error_code"] == "catalog_missing_bearer_token"


async def test_me_route_rejects_missing_bearer_token() -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/me")

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Missing bearer token"
    assert body["error_code"] == MISSING_BEARER_TOKEN
    assert isinstance(body["request_id"], str)
    assert response.headers["x-request-id"] == body["request_id"]


async def test_me_route_rejects_inactive_user() -> None:
    app = _create_auth_app()
    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(
        mode="ok"
    )
    app.dependency_overrides[get_jwt_service] = lambda: _FakeJWTService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer inactive-token"}
        )

    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "User pending approval"
    assert body["error_code"] == USER_PENDING_APPROVAL
    assert isinstance(body["request_id"], str)
    assert response.headers["x-request-id"] == body["request_id"]


def test_settings_requires_jwt_secret_in_non_dev_environment() -> None:
    try:
        _settings(environment="production")
        assert False, "Expected missing JWT secret to fail in production"
    except ValueError:
        pass


def test_settings_generates_jwt_secret_in_dev_environment() -> None:
    cfg = _settings(environment="development")
    assert cfg.auth_jwt_secret is not None
    assert len(cfg.auth_jwt_secret.get_secret_value()) >= 32


def test_settings_rejects_insecure_ldap_transport_in_production() -> None:
    try:
        _settings(
            environment="production",
            auth_jwt_secret="x" * 32,
            ldap_server_uri="ldap://ldap.example.com:389",
        )
        assert False, "Expected insecure LDAP transport to fail in production"
    except ValueError:
        pass


def test_settings_allows_insecure_ldap_transport_in_development() -> None:
    cfg = _settings(
        environment="development",
        ldap_server_uri="ldap://localhost:389",
    )
    assert cfg.ldap_server_uri.startswith("ldap://")


def test_settings_rejects_auth_dev_bypass_ldap_in_production() -> None:
    try:
        _settings(
            environment="production",
            auth_jwt_secret="x" * 32,
            ldap_server_uri="ldaps://ldap.example.com:636",
            auth_dev_bypass_ldap=True,
        )
        assert False, "Expected auth_dev_bypass_ldap to be rejected in production"
    except ValueError:
        pass


async def test_ldap_service_dev_bypass_authenticates_without_ldap_server() -> None:
    cfg = _settings(
        environment="development",
        ldap_server_uri="ldap://127.0.0.1:1",
        ldap_timeout_seconds=1,
        auth_dev_bypass_ldap=True,
    )
    service = LDAPService(cfg)

    try:
        user = await service.authenticate(email="user@example.com", password="secret")
    except Exception as exc:
        assert False, f"Expected dev-bypass LDAP auth to succeed, got: {exc!r}"

    assert user.email == "user@example.com"
    assert "user@example.com" in user.dn
