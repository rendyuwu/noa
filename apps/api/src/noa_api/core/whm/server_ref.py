from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID


class WHMServerLike(Protocol):
    id: UUID
    name: str
    base_url: str


class WHMServerRefRepositoryProtocol(Protocol):
    async def list_servers(self) -> Sequence[WHMServerLike]: ...

    async def get_by_id(self, server_id: UUID) -> WHMServerLike | None: ...


@dataclass(frozen=True)
class WHMServerRefResolution:
    ok: bool
    server_id: UUID | None
    server: object | None
    error_code: str | None
    message: str
    choices: list[dict[str, str]]


def _hostname_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.hostname


async def resolve_whm_server_ref(
    server_ref: str, *, repo: WHMServerRefRepositoryProtocol
) -> WHMServerRefResolution:
    ref = server_ref.strip()
    if not ref:
        return WHMServerRefResolution(
            ok=False,
            server_id=None,
            server=None,
            error_code="host_required",
            message="WHM server reference is required",
            choices=[],
        )

    try:
        server_id = UUID(ref)
    except ValueError:
        server_id = None

    if server_id is not None:
        server = await repo.get_by_id(server_id=server_id)
        if server is None:
            return WHMServerRefResolution(
                ok=False,
                server_id=None,
                server=None,
                error_code="host_not_found",
                message=f"No WHM server found for id {server_id}",
                choices=[],
            )
        return WHMServerRefResolution(
            ok=True,
            server_id=server_id,
            server=server,
            error_code=None,
            message="ok",
            choices=[],
        )

    servers = list(await repo.list_servers())

    name_matches = [s for s in servers if s.name.lower() == ref.lower()]
    if name_matches:
        if len(name_matches) == 1:
            server = name_matches[0]
            return WHMServerRefResolution(
                ok=True,
                server_id=server.id,
                server=server,
                error_code=None,
                message="ok",
                choices=[],
            )
        return WHMServerRefResolution(
            ok=False,
            server_id=None,
            server=None,
            error_code="host_ambiguous",
            message=f"Multiple WHM servers match '{ref}'. Use the server id.",
            choices=[
                {"id": str(s.id), "name": s.name, "base_url": s.base_url}
                for s in name_matches[:10]
            ],
        )

    host_matches: list[WHMServerLike] = []
    for s in servers:
        hostname = _hostname_from_url(s.base_url)
        if hostname is not None and hostname.lower() == ref.lower():
            host_matches.append(s)

    if host_matches:
        if len(host_matches) == 1:
            server = host_matches[0]
            return WHMServerRefResolution(
                ok=True,
                server_id=server.id,
                server=server,
                error_code=None,
                message="ok",
                choices=[],
            )
        return WHMServerRefResolution(
            ok=False,
            server_id=None,
            server=None,
            error_code="host_ambiguous",
            message=f"Multiple WHM servers match host '{ref}'. Use the server id.",
            choices=[
                {"id": str(s.id), "name": s.name, "base_url": s.base_url}
                for s in host_matches[:10]
            ],
        )

    return WHMServerRefResolution(
        ok=False,
        server_id=None,
        server=None,
        error_code="host_not_found",
        message=f"No WHM server found matching '{ref}'",
        choices=[],
    )
