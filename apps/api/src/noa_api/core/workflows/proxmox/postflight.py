from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.core.workflows.proxmox.common import (
    _upstream_error,
)
from noa_api.core.workflows.types import normalized_text
from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.proxmox.tools._cloudinit_passwords import (
    cloudinit_dump_matches_password,
    sanitize_cloudinit_dump_user,
)
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository


async def _resolve_proxmox_client(
    *, session: AsyncSession, server_ref: object
) -> tuple[ProxmoxClient, str] | dict[str, object] | None:
    server_ref_text = normalized_text(server_ref)
    if server_ref_text is None:
        return None

    repo: Any = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref_text, repo=repo)
    if not resolution.ok:
        return {
            "ok": False,
            "error_code": str(resolution.error_code or "unknown"),
            "message": str(resolution.message or "Proxmox server lookup failed"),
        }

    server = resolution.server
    if server is None or resolution.server_id is None:
        return None

    client = ProxmoxClient(
        base_url=str(getattr(server, "base_url")),
        api_token_id=str(getattr(server, "api_token_id")),
        api_token_secret=maybe_decrypt_text(str(getattr(server, "api_token_secret"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )
    return client, str(resolution.server_id)


async def _cloudinit_postflight_result(
    *, client: ProxmoxClient, node: str, vmid: int, new_password: str | None = None
) -> dict[str, object] | None:
    verification_result = await _wait_for_cloudinit_verification(
        client=client,
        node=node,
        vmid=vmid,
        new_password=new_password,
    )
    if verification_result.get("ok") is not True:
        return verification_result
    return {
        "ok": True,
        "cloudinit": verification_result["cloudinit"],
        "cloudinit_dump_user": verification_result["cloudinit_dump_user"],
        "verified": True,
    }


async def _pool_postflight_result(
    *,
    client: ProxmoxClient,
    source_pool: str,
    destination_pool: str,
    vmids: list[int],
) -> dict[str, object] | None:
    source_pool_after = await client.get_pool(source_pool)
    if source_pool_after.get("ok") is not True:
        return _upstream_error(
            source_pool_after,
            fallback_message="Unable to fetch the source pool after the move",
        )
    destination_pool_after = await client.get_pool(destination_pool)
    if destination_pool_after.get("ok") is not True:
        return _upstream_error(
            destination_pool_after,
            fallback_message="Unable to fetch the destination pool after the move",
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
        "verified": True,
        "source_pool_after": source_pool_after,
        "destination_pool_after": destination_pool_after,
    }


async def _wait_for_cloudinit_verification(
    *,
    client: ProxmoxClient,
    node: str,
    vmid: int,
    new_password: str | None,
) -> dict[str, object]:
    cloudinit_result = await client.get_qemu_cloudinit(node, vmid)
    if cloudinit_result.get("ok") is not True:
        return _upstream_error(
            cloudinit_result,
            fallback_message="Unable to verify Proxmox cloud-init values",
        )

    dump_result = await client.get_qemu_cloudinit_dump_user(node, vmid)
    if dump_result.get("ok") is not True:
        return _upstream_error(
            dump_result,
            fallback_message="Unable to verify Proxmox cloud-init user dump",
        )

    sanitized_dump, confirmed = sanitize_cloudinit_dump_user(dump_result.get("data"))
    if not confirmed or sanitized_dump is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    if not _cloudinit_confirms_password_reset(cloudinit_result):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    # Only check password hash match if we have the plaintext password
    if new_password is not None and not cloudinit_dump_matches_password(
        dump_result.get("data"), new_password
    ):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    return {
        "ok": True,
        "cloudinit": cloudinit_result,
        "cloudinit_dump_user": {**dump_result, "data": sanitized_dump},
        "verified": True,
    }


def _cloudinit_confirms_password_reset(result: dict[str, object]) -> bool:
    data = result.get("data")
    if isinstance(data, dict):
        return normalized_text(data.get("cipassword")) is not None
    if not isinstance(data, list):
        return False
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if normalized_text(entry.get("key")) != "cipassword":
            continue
        if normalized_text(entry.get("value")) is not None:
            return True
    return False


def _pool_result_vmids(result: dict[str, object]) -> set[int]:
    vmids: set[int] = set()
    for member in _pool_members_from_result(result):
        vmid = member.get("vmid")
        if isinstance(vmid, int) and not isinstance(vmid, bool):
            vmids.add(vmid)
    return vmids


def _pool_members_from_result(
    result: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if isinstance(data, list):
        members: list[dict[str, object]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            entry_members = entry.get("members")
            if not isinstance(entry_members, list):
                continue
            for member in entry_members:
                if isinstance(member, dict):
                    members.append(member)
        return members
    members = result.get("members")
    if isinstance(members, list):
        return [member for member in members if isinstance(member, dict)]
    return []
