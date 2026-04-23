from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.proxmox.tools._shared import (
    client_for_server as _client_for_server,
    resolution_error as _resolution_error,
)
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository


async def proxmox_list_servers(*, session: AsyncSession) -> dict[str, object]:
    repo = SQLProxmoxServerRepository(session)
    servers = await repo.list_servers()
    return {
        "ok": True,
        "servers": [server.to_safe_dict() for server in servers],
    }


async def proxmox_validate_server(
    *, session: AsyncSession, server_ref: str
) -> dict[str, object]:
    repo: Any = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    client = _client_for_server(server)
    result = await client.get_version()
    if result.get("ok") is True:
        return {"ok": True, "message": "ok"}
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or "Proxmox validation failed"),
    }
