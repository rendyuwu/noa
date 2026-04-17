from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _client_for_server(server: Any) -> ProxmoxClient:
    return ProxmoxClient(
        base_url=str(getattr(server, "base_url")),
        api_token_id=str(getattr(server, "api_token_id")),
        api_token_secret=maybe_decrypt_text(str(getattr(server, "api_token_secret"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


def _upstream_error(
    result: dict[str, object], *, fallback_message: str
) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or fallback_message),
    }


def _sanitize_vm_payload(payload: object) -> object:
    if isinstance(payload, list):
        return [_sanitize_vm_payload(item) for item in payload]
    if isinstance(payload, dict):
        lowered_key = payload.get("key")
        if isinstance(lowered_key, str) and lowered_key.strip().lower() == "cipassword":
            return {
                str(key): (
                    "[redacted]"
                    if str(key).strip().lower() == "value"
                    else _sanitize_vm_payload(item)
                )
                for key, item in payload.items()
            }
    return redact_sensitive_data(payload)


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
        "data": _sanitize_vm_payload(result.get("data")),
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
        "data": _sanitize_vm_payload(result.get("config")),
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
