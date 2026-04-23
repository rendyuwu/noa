from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest


@dataclass
class _Server:
    id: UUID
    name: str
    base_url: str


class _Repo:
    def __init__(self, servers: list[_Server]) -> None:
        self._servers = servers

    async def list_servers(self) -> list[_Server]:
        return self._servers

    async def get_by_id(self, *, server_id: UUID) -> _Server | None:
        for server in self._servers:
            if server.id == server_id:
                return server
        return None


@pytest.mark.asyncio
async def test_resolve_proxmox_server_ref_by_host() -> None:
    from noa_api.proxmox.server_ref import resolve_proxmox_server_ref

    server = _Server(
        id=uuid4(),
        name="pve1",
        base_url="https://proxmox.example.com:8006",
    )

    result = await resolve_proxmox_server_ref(
        "proxmox.example.com", repo=_Repo([server])
    )

    assert result.ok is True
    assert result.server_id == server.id


@pytest.mark.asyncio
async def test_resolve_proxmox_server_ref_ambiguous_name_returns_choices() -> None:
    from noa_api.proxmox.server_ref import resolve_proxmox_server_ref

    a = _Server(id=uuid4(), name="pve1", base_url="https://a.example.com:8006")
    b = _Server(id=uuid4(), name="pve1", base_url="https://b.example.com:8006")

    result = await resolve_proxmox_server_ref("pve1", repo=_Repo([a, b]))

    assert result.ok is False
    assert result.error_code == "host_ambiguous"
    assert len(result.choices) == 2


# ---------------------------------------------------------------------------
# Preflight: resolve_requested_server_id – Proxmox fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_requested_server_id_returns_proxmox_server_id(
    monkeypatch,
) -> None:
    from noa_api.core.workflows import preflight_validation

    proxmox_server_id = uuid4()

    # WHM resolution fails
    whm_resolution = MagicMock()
    whm_resolution.ok = False
    whm_resolution.server_id = None

    mock_whm_resolve = AsyncMock(return_value=whm_resolution)
    monkeypatch.setattr(
        preflight_validation, "resolve_whm_server_ref", mock_whm_resolve
    )

    # Proxmox resolution succeeds
    proxmox_resolution = MagicMock()
    proxmox_resolution.ok = True
    proxmox_resolution.server_id = proxmox_server_id

    mock_proxmox_resolve = AsyncMock(return_value=proxmox_resolution)
    monkeypatch.setattr(
        "noa_api.proxmox.server_ref.resolve_proxmox_server_ref",
        mock_proxmox_resolve,
    )

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    result = await preflight_validation.resolve_requested_server_id(
        args={"server_ref": "pve1"},
        session=mock_session,
    )

    assert result == str(proxmox_server_id)


@pytest.mark.asyncio
async def test_resolve_requested_server_id_prefers_whm_over_proxmox(
    monkeypatch,
) -> None:
    from noa_api.core.workflows import preflight_validation

    whm_server_id = uuid4()

    whm_resolution = MagicMock()
    whm_resolution.ok = True
    whm_resolution.server_id = whm_server_id

    mock_whm_resolve = AsyncMock(return_value=whm_resolution)
    monkeypatch.setattr(
        preflight_validation, "resolve_whm_server_ref", mock_whm_resolve
    )

    # Proxmox resolution should NOT be called when WHM succeeds
    mock_proxmox_resolve = AsyncMock()
    monkeypatch.setattr(
        "noa_api.proxmox.server_ref.resolve_proxmox_server_ref",
        mock_proxmox_resolve,
    )

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    result = await preflight_validation.resolve_requested_server_id(
        args={"server_ref": "whm1"},
        session=mock_session,
    )

    assert result == str(whm_server_id)
    mock_proxmox_resolve.assert_not_awaited()
