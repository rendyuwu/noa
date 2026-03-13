from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from noa_api.core.auth.authorization import AuthorizationUser, get_current_auth_user


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


async def test_whm_admin_routes_never_return_api_token() -> None:
    from noa_api.api.routes.whm_admin import (
        get_whm_server_service,
        router as whm_admin_router,
    )

    service = _FakeWHMServerService()

    app = FastAPI()
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
