from __future__ import annotations

from noa_api.core.workflows.types import (
    WorkflowApprovalPresentationBlock,
    normalized_text,
)


def _normalized_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _normalized_int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    vmids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        vmids.append(item)
    return vmids


def _action_label(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "enable VM NIC"
    return "disable VM NIC"


def _approval_action_label(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "Enable VM NIC"
    return "Disable VM NIC"


def _desired_link_state(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "up"
    return "down"


def _action_verb(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "enable"
    return "disable"


def _action_completed_label(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "Enabled"
    return "Disabled"


def _action_outcome_adjective(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "enabled"
    return "disabled"


def _subject(args: dict[str, object]) -> str:
    node = normalized_text(args.get("node")) or "unknown-node"
    vmid = _normalized_int(args.get("vmid"))
    net = normalized_text(args.get("net")) or "unknown-net"
    vmid_text = str(vmid) if vmid is not None else "unknown-vmid"
    return f"VM {vmid_text} NIC {net} on node {node}"


def _title_subject(args: dict[str, object]) -> str:
    node = normalized_text(args.get("node")) or "unknown-node"
    vmid = _normalized_int(args.get("vmid"))
    net = normalized_text(args.get("net")) or "unknown-net"
    vmid_text = str(vmid) if vmid is not None else "unknown-vmid"
    return f"VM {vmid_text} {net} on {node}"


def _workflow_result_failed(result: dict[str, object] | None) -> bool:
    return isinstance(result, dict) and result.get("ok") is False


def _vmids_text(value: object) -> str:
    vmids = _normalized_int_list(value)
    if not vmids:
        return "unknown-vmids"
    return ", ".join(str(vmid) for vmid in vmids)


def _pool_value(value: object) -> str:
    return normalized_text(value) or "unknown"


def _upstream_error(
    result: dict[str, object], *, fallback_message: str
) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or fallback_message),
    }


def _reason_step_content(
    *,
    action_label: str,
    action_verb: str,
    reason: str | None,
    missing_reason_text: str | None = None,
) -> str:
    if reason is None:
        if missing_reason_text is not None:
            return missing_reason_text
        gerund = "enabling" if action_verb == "enable" else "disabling"
        return (
            "Ask the user for a reason—an osTicket/reference number or a brief "
            f"description—before {gerund} the VM NIC."
        )
    return f"Reason captured for the {action_label} change: {reason}."


def _postflight_verified(
    tool_name: str | None, postflight_result: dict[str, object] | None
) -> bool:
    if not isinstance(postflight_result, dict):
        return False
    if tool_name in {"proxmox_disable_vm_nic", "proxmox_enable_vm_nic"}:
        desired_state = _desired_link_state(tool_name)
        return (
            postflight_result.get("ok") is True
            and _link_state(postflight_result) == desired_state
        )
    return postflight_result.get("verified") is True


def _link_state(source: dict[str, object] | None) -> str | None:
    if not isinstance(source, dict):
        return None
    return normalized_text(source.get("link_state"))


def _approval_table_block(
    *, headers: list[str], rows: list[list[str]]
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        kind="table",
        table_headers=headers,
        table_rows=rows,
    )
