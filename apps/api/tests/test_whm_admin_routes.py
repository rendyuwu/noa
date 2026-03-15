from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy.exc import IntegrityError

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_handling import ApiHTTPException, install_error_handling
from noa_api.core.auth.authorization import AuthorizationUser


@dataclass
class _WHMServer:
    id: UUID
    name: str
    base_url: str
    api_username: str
    api_token: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "base_url": self.base_url,
            "api_username": self.api_username,
            "verify_ssl": self.verify_ssl,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class _WHMServerServiceProtocol(Protocol):
    async def list_servers(self) -> list[_WHMServer]: ...

    async def create_server(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
    ) -> _WHMServer: ...

    async def update_server(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ) -> _WHMServer | None: ...


class _FakeWHMServerService:
    def __init__(self) -> None:
        self._servers: dict[UUID, _WHMServer] = {}

    async def list_servers(self) -> list[_WHMServer]:
        return sorted(self._servers.values(), key=lambda s: s.name)

    async def create_server(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
    ) -> _WHMServer:
        now = datetime.now(UTC)
        server = _WHMServer(
            id=uuid4(),
            name=name,
            base_url=base_url,
            api_username=api_username,
            api_token=api_token,
            verify_ssl=verify_ssl,
            created_at=now,
            updated_at=now,
        )
        self._servers[server.id] = server
        return server

    async def update_server(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ) -> _WHMServer | None:
        _ = api_token
        server = self._servers.get(server_id)
        if server is None:
            return None
        if name is not None:
            server.name = name
        if base_url is not None:
            server.base_url = base_url
        if api_username is not None:
            server.api_username = api_username
        if verify_ssl is not None:
            server.verify_ssl = verify_ssl
        server.updated_at = datetime.now(UTC)
        return server


class _IntegrityErrorWHMServerRepository:
    async def list_servers(self) -> list[_WHMServer]:
        return []

    async def get_by_id(self, *, server_id: UUID) -> _WHMServer | None:
        _ = server_id
        return None

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
    ) -> _WHMServer:
        _ = (name, base_url, api_username, api_token, verify_ssl)
        raise IntegrityError("insert into whm_servers", {}, Exception("duplicate key"))

    async def update(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ) -> _WHMServer | None:
        _ = (server_id, name, base_url, api_username, api_token, verify_ssl)
        raise AssertionError("update should not be called")

    async def delete(self, *, server_id: UUID) -> bool:
        _ = server_id
        raise AssertionError("delete should not be called")


class _MissingWHMServerRepository:
    async def list_servers(self) -> list[_WHMServer]:
        return []

    async def get_by_id(self, *, server_id: UUID) -> _WHMServer | None:
        _ = server_id
        return None

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
    ) -> _WHMServer:
        _ = (name, base_url, api_username, api_token, verify_ssl)
        raise AssertionError("create should not be called")

    async def update(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ) -> _WHMServer | None:
        _ = (server_id, name, base_url, api_username, api_token, verify_ssl)
        raise AssertionError("update should not be called")

    async def delete(self, *, server_id: UUID) -> bool:
        _ = server_id
        raise AssertionError("delete should not be called")


class _ConflictCreateWHMServerService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def list_servers(self) -> list[_WHMServer]:
        return []

    async def create_server(
        self,
        *,
        name: str,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
    ) -> _WHMServer:
        _ = (name, base_url, api_username, api_token, verify_ssl)
        raise self._error

    async def update_server(
        self,
        *,
        server_id: UUID,
        name: str | None = None,
        base_url: str | None = None,
        api_username: str | None = None,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ) -> _WHMServer | None:
        _ = (server_id, name, base_url, api_username, api_token, verify_ssl)
        raise AssertionError("update should not be called")


def _create_whm_admin_app(service: _WHMServerServiceProtocol) -> FastAPI:
    from noa_api.api.routes.whm_admin import (
        get_whm_server_service,
        router as whm_admin_router,
    )

    app = FastAPI()
    install_error_handling(app)
    app.include_router(whm_admin_router)
    app.dependency_overrides[get_whm_server_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
    )
    return app


async def test_whm_admin_routes_never_return_api_token() -> None:
    service = _FakeWHMServerService()
    app = _create_whm_admin_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post(
            "/admin/whm/servers",
            json={
                "name": "web1",
                "base_url": "https://whm.example.com:2087",
                "api_username": "root",
                "api_token": "SECRET",
                "verify_ssl": True,
            },
        )
        list_response = await client.get("/admin/whm/servers")

    assert create_response.status_code == 201
    assert list_response.status_code == 200

    assert "api_token" not in create_response.json()["server"]
    assert "SECRET" not in create_response.text

    list_payload = list_response.json()
    assert "SECRET" not in list_response.text
    assert list_payload["servers"][0]["name"] == "web1"
    assert "api_token" not in list_payload["servers"][0]


async def test_whm_admin_route_returns_error_contract_for_missing_server() -> None:
    service = _FakeWHMServerService()
    app = _create_whm_admin_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/admin/whm/servers/{uuid4()}",
            json={"name": "renamed"},
            headers={"x-request-id": "whm-server-missing"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "WHM server not found"
    assert body["error_code"] == "whm_server_not_found"
    assert body["request_id"] == "whm-server-missing"
    assert response.headers["x-request-id"] == body["request_id"]


async def test_whm_server_service_raises_typed_conflict_error_for_duplicate_name() -> (
    None
):
    from noa_api.api.routes.whm_admin import WHMServerService

    service = WHMServerService(_IntegrityErrorWHMServerRepository())

    with pytest.raises(Exception) as exc_info:
        await service.create_server(
            name="web1",
            base_url="https://whm.example.com:2087",
            api_username="root",
            api_token="SECRET",
            verify_ssl=True,
        )

    assert type(exc_info.value).__name__ == "WHMServerNameExistsError"
    assert not isinstance(exc_info.value, ApiHTTPException)


async def test_whm_server_service_raises_typed_not_found_error_for_missing_validate_target() -> (
    None
):
    from noa_api.api.routes.whm_admin import WHMServerService

    service = WHMServerService(_MissingWHMServerRepository())

    with pytest.raises(Exception) as exc_info:
        await service.validate_server(server_id=uuid4())

    assert type(exc_info.value).__name__ == "WHMServerNotFoundError"
    assert not isinstance(exc_info.value, ApiHTTPException)


async def test_whm_admin_route_maps_service_conflict_error_to_http_contract() -> None:
    from noa_api.api.routes import whm_admin

    service = _ConflictCreateWHMServerService(whm_admin.WHMServerNameExistsError())
    app = _create_whm_admin_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/whm/servers",
            json={
                "name": "web1",
                "base_url": "https://whm.example.com:2087",
                "api_username": "root",
                "api_token": "SECRET",
                "verify_ssl": True,
            },
            headers={"x-request-id": "whm-server-conflict"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "WHM server name already exists"
    assert body["error_code"] == "whm_server_name_exists"
    assert body["request_id"] == "whm-server-conflict"
    assert response.headers["x-request-id"] == body["request_id"]
