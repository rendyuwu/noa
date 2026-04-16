from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_proxmox_userid(email: str) -> str:
    return f"{email.strip()}@pve"


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


async def _resolve_client(
    *, session: AsyncSession, server_ref: str
) -> tuple[ProxmoxClient, str] | dict[str, object]:
    repo: Any = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    assert resolution.server_id is not None
    return _client_for_server(server), str(resolution.server_id)


async def proxmox_get_user_by_email(
    *, session: AsyncSession, server_ref: str, email: str
) -> dict[str, object]:
    resolved = await _resolve_client(session=session, server_ref=server_ref)
    if isinstance(resolved, dict):
        return resolved

    client, _server_id = resolved
    userid = _normalize_proxmox_userid(email)
    result = await client.get_user(userid)
    if result.get("ok") is not True:
        return _upstream_error(result, fallback_message="Proxmox user lookup failed")

    return {"ok": True, "message": "ok", "data": result.get("data")}


async def proxmox_preflight_move_vms_between_pools(
    *,
    session: AsyncSession,
    server_ref: str,
    source_pool: str,
    destination_pool: str,
    vmids: list[int],
    email: str,
) -> dict[str, object]:
    resolved = await _resolve_client(session=session, server_ref=server_ref)
    if isinstance(resolved, dict):
        return resolved

    client, server_id = resolved
    normalized_source_pool = source_pool.strip()
    normalized_destination_pool = destination_pool.strip()
    normalized_userid = _normalize_proxmox_userid(email)

    source_pool_result = await client.get_pool(normalized_source_pool)
    if source_pool_result.get("ok") is not True:
        return _upstream_error(
            source_pool_result,
            fallback_message="Proxmox source pool lookup failed",
        )

    destination_pool_result = await client.get_pool(normalized_destination_pool)
    if destination_pool_result.get("ok") is not True:
        return _upstream_error(
            destination_pool_result,
            fallback_message="Proxmox destination pool lookup failed",
        )

    target_user_result = await client.get_user(normalized_userid)
    if target_user_result.get("ok") is not True:
        return _upstream_error(
            target_user_result, fallback_message="Proxmox user lookup failed"
        )

    permission_result = await client.get_effective_permissions(
        normalized_userid,
        f"/pool/{normalized_destination_pool}",
    )
    if permission_result.get("ok") is not True:
        return _upstream_error(
            permission_result,
            fallback_message="Proxmox destination permission lookup failed",
        )

    return {
        "ok": True,
        "message": "ok",
        "server_id": server_id,
        "source_pool": source_pool_result,
        "destination_pool": destination_pool_result,
        "target_user": target_user_result,
        "destination_permission": permission_result,
        "requested_vmids": vmids,
        "normalized_userid": normalized_userid,
    }


async def proxmox_move_vms_between_pools(
    *,
    session: AsyncSession,
    server_ref: str,
    source_pool: str,
    destination_pool: str,
    vmids: list[int],
    email: str,
    reason: str,
) -> dict[str, object]:
    preflight = await proxmox_preflight_move_vms_between_pools(
        session=session,
        server_ref=server_ref,
        source_pool=source_pool,
        destination_pool=destination_pool,
        vmids=vmids,
        email=email,
    )
    if preflight.get("ok") is not True:
        return preflight

    resolved = await _resolve_client(session=session, server_ref=server_ref)
    if isinstance(resolved, dict):
        return resolved

    client, server_id = resolved
    normalized_source_pool = source_pool.strip()
    normalized_destination_pool = destination_pool.strip()

    add_result = await client.add_vms_to_pool(normalized_destination_pool, vmids)
    if add_result.get("ok") is not True:
        return _upstream_error(
            add_result,
            fallback_message="Unable to add VM(s) to the destination pool",
        )

    remove_result = await client.remove_vms_from_pool(normalized_source_pool, vmids)
    if remove_result.get("ok") is not True:
        return _upstream_error(
            remove_result,
            fallback_message="Unable to remove VM(s) from the source pool",
        )

    source_pool_after = await client.get_pool(normalized_source_pool)
    if source_pool_after.get("ok") is not True:
        return _upstream_error(
            source_pool_after,
            fallback_message="Unable to refetch the source pool after the move",
        )

    destination_pool_after = await client.get_pool(normalized_destination_pool)
    if destination_pool_after.get("ok") is not True:
        return _upstream_error(
            destination_pool_after,
            fallback_message="Unable to refetch the destination pool after the move",
        )

    return {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": server_id,
        "source_pool_before": preflight["source_pool"],
        "destination_pool_before": preflight["destination_pool"],
        "add_to_destination": add_result,
        "remove_from_source": remove_result,
        "source_pool_after": source_pool_after,
        "destination_pool_after": destination_pool_after,
        "results": [{"vmid": vmid, "status": "changed"} for vmid in vmids],
        "verified": True,
    }
