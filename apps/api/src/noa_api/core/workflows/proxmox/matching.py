from __future__ import annotations

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.proxmox.common import (
    _normalized_int,
    _normalized_int_list,
)
from noa_api.core.workflows.types import (
    collect_recent_preflight_evidence,
    normalized_text,
)


def _server_identity_matches(
    *,
    item_args: dict[str, object],
    result: dict[str, object],
    requested_server_ref: str,
    requested_server_id: str | None,
) -> bool:
    result_server_id = normalized_text(result.get("server_id"))
    if requested_server_id is not None and result_server_id is not None:
        return result_server_id == requested_server_id
    return normalized_text(item_args.get("server_ref")) == requested_server_ref


def _server_identity_matches_any(
    *,
    item_args: dict[str, object],
    result: dict[str, object],
    requested_server_ref: str,
    requested_server_id: str | None,
) -> bool:
    result_server_id = normalized_text(result.get("server_id"))
    item_server_ref = normalized_text(item_args.get("server_ref"))
    if requested_server_id is not None and result_server_id is not None:
        return result_server_id == requested_server_id
    if requested_server_id is not None:
        return item_server_ref == requested_server_ref
    if item_server_ref == requested_server_ref:
        return True
    return result_server_id is not None and result_server_id == requested_server_ref


def _matching_preflight(
    preflight_evidence: list[dict[str, object]],
    args: dict[str, object],
    *,
    requested_server_id: str | None = None,
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_net = normalized_text(args.get("net"))
    requested_digest = normalized_text(args.get("digest"))
    requested_vmid = _normalized_int(args.get("vmid"))

    if (
        requested_server_ref is None
        or requested_node is None
        or requested_net is None
        or requested_vmid is None
    ):
        return None

    for item in reversed(preflight_evidence):
        if item.get("toolName") != "proxmox_preflight_vm_nic_toggle":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        if normalized_text(result.get("net")) != requested_net:
            continue
        if (
            requested_digest is not None
            and normalized_text(result.get("digest")) != requested_digest
        ):
            continue
        return result

    return None


def _require_vm_nic_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_net = normalized_text(args.get("net"))
    requested_digest = normalized_text(args.get("digest"))
    requested_vmid = _normalized_int(args.get("vmid"))
    if (
        requested_server_ref is None
        or requested_node is None
        or requested_net is None
        or requested_digest is None
        or requested_vmid is None
    ):
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "proxmox_preflight_vm_nic_toggle"
        and isinstance(item.get("result"), dict)
        and item["result"].get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required Proxmox preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run proxmox_preflight_vm_nic_toggle with the same server_ref, node, vmid, net, and digest before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        if normalized_text(result.get("net")) != requested_net:
            continue
        if normalized_text(result.get("digest")) != requested_digest:
            continue
        return None

    return SanitizedToolError(
        error="Required Proxmox preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful proxmox_preflight_vm_nic_toggle was found for server_ref '{requested_server_ref}', node '{requested_node}', vmid '{requested_vmid}', net '{requested_net}', and digest '{requested_digest}' in the current turn.",
        ),
    )


def _matching_cloudinit_preflight(
    preflight_evidence: list[dict[str, object]],
    args: dict[str, object],
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_vmid = _normalized_int(args.get("vmid"))

    if requested_server_ref is None or requested_node is None or requested_vmid is None:
        return None

    for item in reversed(preflight_evidence):
        if item.get("toolName") != "proxmox_preflight_vm_cloudinit_password_reset":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=None,
        ):
            continue
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        return result

    return None


def _require_cloudinit_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_vmid = _normalized_int(args.get("vmid"))
    if requested_server_ref is None or requested_node is None or requested_vmid is None:
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "proxmox_preflight_vm_cloudinit_password_reset"
        and isinstance(item.get("result"), dict)
        and item["result"].get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required Proxmox preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run proxmox_preflight_vm_cloudinit_password_reset with the same server_ref, node, and vmid before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        return None

    return SanitizedToolError(
        error="Required Proxmox preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful proxmox_preflight_vm_cloudinit_password_reset was found for server_ref '{requested_server_ref}', node '{requested_node}', and vmid '{requested_vmid}' in the current turn.",
        ),
    )


def _matching_pool_move_preflight(
    preflight_evidence: list[dict[str, object]],
    args: dict[str, object],
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_source_pool = normalized_text(args.get("source_pool"))
    requested_destination_pool = normalized_text(args.get("destination_pool"))
    requested_vmids = _normalized_int_list(args.get("vmids"))
    requested_email = normalized_text(args.get("email"))

    if (
        requested_server_ref is None
        or requested_source_pool is None
        or requested_destination_pool is None
        or not requested_vmids
        or requested_email is None
    ):
        return None

    for item in reversed(preflight_evidence):
        if item.get("toolName") != "proxmox_preflight_move_vms_between_pools":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=None,
        ):
            continue
        if normalized_text(item_args.get("source_pool")) != requested_source_pool:
            continue
        if (
            normalized_text(item_args.get("destination_pool"))
            != requested_destination_pool
        ):
            continue
        if _normalized_int_list(item_args.get("vmids")) != requested_vmids:
            continue
        if normalized_text(item_args.get("email")) != requested_email:
            continue
        return result

    return None


def _require_pool_move_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_source_pool = normalized_text(args.get("source_pool"))
    requested_destination_pool = normalized_text(args.get("destination_pool"))
    requested_vmids = _normalized_int_list(args.get("vmids"))
    requested_email = normalized_text(args.get("email"))
    if (
        requested_server_ref is None
        or requested_source_pool is None
        or requested_destination_pool is None
        or not requested_vmids
        or requested_email is None
    ):
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "proxmox_preflight_move_vms_between_pools"
        and isinstance(item.get("result"), dict)
        and item["result"].get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required Proxmox preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run proxmox_preflight_move_vms_between_pools with the same server_ref, source_pool, destination_pool, vmids, and email before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        if normalized_text(item_args.get("source_pool")) != requested_source_pool:
            continue
        if (
            normalized_text(item_args.get("destination_pool"))
            != requested_destination_pool
        ):
            continue
        if _normalized_int_list(item_args.get("vmids")) != requested_vmids:
            continue
        if normalized_text(item_args.get("email")) != requested_email:
            continue
        return None

    return SanitizedToolError(
        error="Required Proxmox preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful proxmox_preflight_move_vms_between_pools was found for server_ref '{requested_server_ref}', source_pool '{requested_source_pool}', destination_pool '{requested_destination_pool}', vmids '{requested_vmids}', and email '{requested_email}' in the current turn.",
        ),
    )
