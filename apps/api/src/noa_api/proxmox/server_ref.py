from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID


class ProxmoxServerLike(Protocol):
    id: UUID
    name: str
    base_url: str


class ProxmoxServerRefRepositoryProtocol(Protocol):
    async def list_servers(self) -> Sequence[ProxmoxServerLike]: ...

    async def get_by_id(self, *, server_id: UUID) -> ProxmoxServerLike | None: ...


@dataclass(frozen=True)
class ProxmoxServerRefResolution:
    ok: bool
    server_id: UUID | None
    server: object | None
    error_code: str | None
    message: str
    choices: list[dict[str, str]]


def _hostname_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.hostname


async def resolve_proxmox_server_ref(
    server_ref: str,
    *,
    repo: ProxmoxServerRefRepositoryProtocol,
) -> ProxmoxServerRefResolution:
    ref = server_ref.strip()
    if not ref:
        return ProxmoxServerRefResolution(
            ok=False,
            server_id=None,
            server=None,
            error_code="host_required",
            message="Proxmox server reference is required",
            choices=[],
        )

    try:
        server_id = UUID(ref)
    except ValueError:
        server_id = None

    if server_id is not None:
        server = await repo.get_by_id(server_id=server_id)
        if server is None:
            return ProxmoxServerRefResolution(
                ok=False,
                server_id=None,
                server=None,
                error_code="host_not_found",
                message=f"No Proxmox server found for id {server_id}",
                choices=[],
            )
        return ProxmoxServerRefResolution(
            ok=True,
            server_id=server_id,
            server=server,
            error_code=None,
            message="ok",
            choices=[],
        )

    servers = list(await repo.list_servers())

    name_matches = [server for server in servers if server.name.lower() == ref.lower()]
    if name_matches:
        if len(name_matches) == 1:
            server = name_matches[0]
            return ProxmoxServerRefResolution(
                ok=True,
                server_id=server.id,
                server=server,
                error_code=None,
                message="ok",
                choices=[],
            )
        return ProxmoxServerRefResolution(
            ok=False,
            server_id=None,
            server=None,
            error_code="host_ambiguous",
            message=f"Multiple Proxmox servers match '{ref}'. Use the server id.",
            choices=[
                {"id": str(server.id), "name": server.name, "base_url": server.base_url}
                for server in name_matches[:10]
            ],
        )

    host_matches: list[ProxmoxServerLike] = []
    for server in servers:
        hostname = _hostname_from_url(server.base_url)
        if hostname is not None and hostname.lower() == ref.lower():
            host_matches.append(server)

    if host_matches:
        if len(host_matches) == 1:
            server = host_matches[0]
            return ProxmoxServerRefResolution(
                ok=True,
                server_id=server.id,
                server=server,
                error_code=None,
                message="ok",
                choices=[],
            )
        return ProxmoxServerRefResolution(
            ok=False,
            server_id=None,
            server=None,
            error_code="host_ambiguous",
            message=f"Multiple Proxmox servers match host '{ref}'. Use the server id.",
            choices=[
                {"id": str(server.id), "name": server.name, "base_url": server.base_url}
                for server in host_matches[:10]
            ],
        )

    return ProxmoxServerRefResolution(
        ok=False,
        server_id=None,
        server=None,
        error_code="host_not_found",
        message=f"No Proxmox server found matching '{ref}'",
        choices=[],
    )
