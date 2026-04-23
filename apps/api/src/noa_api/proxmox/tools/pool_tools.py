from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.proxmox.tools._shared import (
    client_for_server as _client_for_server,
    resolution_error as _resolution_error,
    upstream_error as _upstream_error,
)
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository


def _normalize_proxmox_userid(email: str) -> str:
    return f"{email.strip()}@pve"


def _pool_members(result: dict[str, object]) -> list[dict[str, object]]:
    data = result.get("data")
    if not isinstance(data, list):
        raise ValueError("invalid pool payload")
    members: list[dict[str, object]] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("invalid pool payload")
        entry_members = entry.get("members")
        if isinstance(entry_members, list):
            for member in entry_members:
                if isinstance(member, dict):
                    members.append(member)
                else:
                    raise ValueError("invalid pool payload")
        else:
            raise ValueError("invalid pool payload")
    return members


def _member_vmids(result: dict[str, object]) -> set[int]:
    vmids: set[int] = set()
    for member in _pool_members(result):
        vmid = member.get("vmid")
        if isinstance(vmid, int):
            vmids.add(vmid)
    return vmids


def _validated_pool_vmids(result: dict[str, object]) -> set[int] | None:
    if result.get("ok") is not True:
        return None
    try:
        return _member_vmids(result)
    except ValueError:
        return None


def _meaningful_permission_entries(
    result: dict[str, object], path: str
) -> dict[str, object] | None:
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    permissions = data.get(path)
    if not isinstance(permissions, dict) or not permissions:
        return None
    return permissions


def _pool_result_vmids(result: dict[str, object]) -> set[int]:
    vmids = _validated_pool_vmids(result)
    if vmids is None:
        raise ValueError("invalid pool payload")
    return vmids


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

    if len(vmids) == 0:
        return {
            "ok": False,
            "error_code": "invalid_request",
            "message": "At least one VMID is required",
        }

    if normalized_source_pool == normalized_destination_pool:
        return {
            "ok": False,
            "error_code": "invalid_request",
            "message": "Source and destination pools must be different",
        }

    source_pool_result = await client.get_pool(normalized_source_pool)
    if source_pool_result.get("ok") is not True:
        return _upstream_error(
            source_pool_result,
            fallback_message="Proxmox source pool lookup failed",
        )
    try:
        source_pool_vmids = _pool_result_vmids(source_pool_result)
    except ValueError:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected pool payload",
        }

    destination_pool_result = await client.get_pool(normalized_destination_pool)
    if destination_pool_result.get("ok") is not True:
        return _upstream_error(
            destination_pool_result,
            fallback_message="Proxmox destination pool lookup failed",
        )
    try:
        _pool_result_vmids(destination_pool_result)
    except ValueError:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected pool payload",
        }

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

    if (
        _meaningful_permission_entries(
            permission_result, f"/pool/{normalized_destination_pool}"
        )
        is None
    ):
        return {
            "ok": False,
            "error_code": "permission_required",
            "message": "Proxmox destination pool permissions are required before moving VMs",
        }

    if not all(vmid in source_pool_vmids for vmid in vmids):
        return {
            "ok": False,
            "error_code": "vmid_not_in_source_pool",
            "message": "One or more requested VMIDs were not found in the source pool",
        }

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

    source_pool_after_add = await client.get_pool(normalized_source_pool)
    if source_pool_after_add.get("ok") is not True:
        return _upstream_error(
            source_pool_after_add,
            fallback_message="Unable to refetch the source pool after the add step",
        )

    destination_pool_after_add = await client.get_pool(normalized_destination_pool)
    if destination_pool_after_add.get("ok") is not True:
        return _upstream_error(
            destination_pool_after_add,
            fallback_message="Unable to refetch the destination pool after the add step",
        )

    try:
        source_vmids_after_add = _pool_result_vmids(source_pool_after_add)
        destination_vmids_after_add = _pool_result_vmids(destination_pool_after_add)
    except ValueError:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected pool payload",
        }

    if not all(vmid in destination_vmids_after_add for vmid in vmids):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox pool move verification did not confirm the requested VMIDs",
        }

    remove_result: dict[str, object] | None = None
    source_pool_after = source_pool_after_add
    destination_pool_after = destination_pool_after_add
    if any(vmid in source_vmids_after_add for vmid in vmids):
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

    try:
        source_vmids_after = _pool_result_vmids(source_pool_after)
        destination_vmids_after = _pool_result_vmids(destination_pool_after)
    except ValueError:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected pool payload",
        }

    if not all(
        vmid not in source_vmids_after and vmid in destination_vmids_after
        for vmid in vmids
    ):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox pool move verification did not confirm the requested VMIDs",
        }

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
