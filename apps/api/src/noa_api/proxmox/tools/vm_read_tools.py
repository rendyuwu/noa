from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.proxmox.tools._shared import (
    client_for_server as _client_for_server,
    resolution_error as _resolution_error,
    sanitize_proxmox_payload as _sanitize_payload,
    upstream_error as _upstream_error,
)
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository


async def _fetch_vm_read_data(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    fetcher: Callable[[ProxmoxClient, str, int], Awaitable[dict[str, object]]],
    fallback_message: str,
) -> dict[str, object]:
    client_or_error = await _resolve_vm_client(session=session, server_ref=server_ref)
    if isinstance(client_or_error, dict):
        return client_or_error

    client = client_or_error
    normalized_node = node.strip()
    result = await fetcher(client, normalized_node, vmid)
    if result.get("ok") is not True:
        return _upstream_error(result, fallback_message=fallback_message)

    return {
        "ok": True,
        "message": "ok",
        "data": _sanitize_payload(result.get("data")),
    }


async def _resolve_vm_client(
    *,
    session: AsyncSession,
    server_ref: str,
) -> ProxmoxClient | dict[str, object]:
    repo: Any = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    return _client_for_server(server)


async def _fetch_vm_config(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
) -> dict[str, object]:
    client_or_error = await _resolve_vm_client(session=session, server_ref=server_ref)
    if isinstance(client_or_error, dict):
        return client_or_error

    client = client_or_error
    normalized_node = node.strip()

    result = await client.get_qemu_config(normalized_node, vmid)
    if result.get("ok") is not True:
        return _upstream_error(
            result, fallback_message="Proxmox VM config lookup failed"
        )

    return {
        "ok": True,
        "message": "ok",
        "data": _sanitize_payload(result.get("config")),
    }


async def proxmox_get_vm_status_current(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
) -> dict[str, object]:
    return await _fetch_vm_read_data(
        session=session,
        server_ref=server_ref,
        node=node,
        vmid=vmid,
        fetcher=ProxmoxClient.get_qemu_status_current,
        fallback_message="Proxmox VM status lookup failed",
    )


async def proxmox_get_vm_config(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
) -> dict[str, object]:
    return await _fetch_vm_config(
        session=session, server_ref=server_ref, node=node, vmid=vmid
    )


async def proxmox_get_vm_pending(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
) -> dict[str, object]:
    return await _fetch_vm_read_data(
        session=session,
        server_ref=server_ref,
        node=node,
        vmid=vmid,
        fetcher=ProxmoxClient.get_qemu_pending,
        fallback_message="Proxmox VM pending lookup failed",
    )
