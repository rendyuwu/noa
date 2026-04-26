"""Proxmox tool definitions."""

from __future__ import annotations

from noa_api.core.tools.schema_builders import _object_schema
from noa_api.core.tools.schemas.common import REASON_PARAM
from noa_api.core.tools.schemas.proxmox import (
    PROXMOX_CLOUDINIT_PASSWORD_PARAM,
    PROXMOX_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA,
    PROXMOX_DIGEST_PARAM,
    PROXMOX_EMAIL_PARAM,
    PROXMOX_NET_PARAM,
    PROXMOX_NEW_EMAIL_PARAM,
    PROXMOX_NIC_CHANGE_RESULT_SCHEMA,
    PROXMOX_NODE_PARAM,
    PROXMOX_OLD_EMAIL_PARAM,
    PROXMOX_POOL_MOVE_RESULT_SCHEMA,
    PROXMOX_POOL_PARAM,
    PROXMOX_PREFLIGHT_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA,
    PROXMOX_PREFLIGHT_NIC_RESULT_SCHEMA,
    PROXMOX_PREFLIGHT_POOL_MOVE_RESULT_SCHEMA,
    PROXMOX_SERVER_REF_PARAM,
    PROXMOX_SERVERS_RESULT_SCHEMA,
    PROXMOX_VALIDATE_SERVER_RESULT_SCHEMA,
    PROXMOX_VM_READ_RESULT_SCHEMA,
    PROXMOX_VMID_LIST_PARAM,
    PROXMOX_VMID_PARAM,
    PROXMOX_USER_RESULT_SCHEMA,
)
from noa_api.core.tools.types import ToolDefinition
from noa_api.proxmox.tools.cloudinit_tools import (
    proxmox_preflight_vm_cloudinit_password_reset,
    proxmox_reset_vm_cloudinit_password,
)
from noa_api.proxmox.tools.nic_tools import (
    proxmox_disable_vm_nic,
    proxmox_enable_vm_nic,
    proxmox_preflight_vm_nic_toggle,
)
from noa_api.proxmox.tools.pool_tools import (
    proxmox_get_user_by_email,
    proxmox_move_vms_between_pools,
    proxmox_preflight_move_vms_between_pools,
)
from noa_api.proxmox.tools.read_tools import (
    proxmox_list_servers,
    proxmox_validate_server,
)
from noa_api.proxmox.tools.vm_read_tools import (
    proxmox_get_vm_config,
    proxmox_get_vm_pending,
    proxmox_get_vm_status_current,
)
from noa_api.storage.postgres.lifecycle import ToolRisk

PROXMOX_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="proxmox_list_servers",
        description="List configured Proxmox servers using safe fields only.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(properties={}, required=[]),
        execute=proxmox_list_servers,
        prompt_hints=(
            "Use this when the user has not supplied a server_ref or when you need Proxmox server choices for disambiguation.",
            "Successful results return `servers` and never include the API token secret.",
        ),
        result_schema=PROXMOX_SERVERS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_vm_status_current",
        description="Read the current Proxmox VM runtime status for one exact VM.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_get_vm_status_current,
        prompt_hints=(
            "Use this to inspect the current VM state before proposing a Proxmox change.",
            "Successful results return `data` with the upstream VM status payload.",
        ),
        result_schema=PROXMOX_VM_READ_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_vm_config",
        description="Read the Proxmox VM configuration and hardware layout for one exact VM.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_get_vm_config,
        prompt_hints=(
            "Use this when you need the VM config or the digest before a follow-up change.",
            "Successful results return `data` with the upstream QEMU config payload.",
        ),
        result_schema=PROXMOX_VM_READ_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_vm_pending",
        description="Read the pending Proxmox VM configuration changes for one exact VM.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_get_vm_pending,
        prompt_hints=(
            "Use this to inspect queued VM changes before deciding whether a change is still needed.",
            "Successful results return `data` with the upstream pending-change payload.",
        ),
        result_schema=PROXMOX_VM_READ_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_user_by_email",
        description="Resolve a Proxmox user account from an exact email address.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "email": PROXMOX_EMAIL_PARAM,
            },
            required=["server_ref", "email"],
        ),
        execute=proxmox_get_user_by_email,
        prompt_hints=(
            "Use this to confirm the exact Proxmox user identity before any pool membership change.",
            "Successful results return `data` with the upstream user payload.",
        ),
        result_schema=PROXMOX_USER_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_preflight_vm_cloudinit_password_reset",
        description="Inspect Proxmox VM configuration and cloud-init state before resetting the cloud-init password.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_preflight_vm_cloudinit_password_reset,
        prompt_hints=(
            "Run this before `proxmox_reset_vm_cloudinit_password` and summarize the VM configuration plus cloud-init state first.",
            "Successful results return `config` and `cloudinit` evidence for the exact VM.",
        ),
        result_schema=PROXMOX_PREFLIGHT_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_reset_vm_cloudinit_password",
        description="Reset the Proxmox VM cloud-init password after the exact VM has been preflighted.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
                "new_password": PROXMOX_CLOUDINIT_PASSWORD_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "node", "vmid", "new_password", "reason"],
        ),
        execute=proxmox_reset_vm_cloudinit_password,
        prompt_hints=(
            "Run `proxmox_preflight_vm_cloudinit_password_reset` first and reuse the same server_ref, node, and vmid.",
            "Generate a strong random password (16+ chars, mixed case, digits, symbols) unless the user provides a specific password.",
            "Idempotent result contract: returns `status` `changed` only after postflight confirms the cloud-init password reset.",
        ),
        result_schema=PROXMOX_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA,
        workflow_family="proxmox-vm-cloudinit-password-reset",
    ),
    ToolDefinition(
        name="proxmox_preflight_move_vms_between_pools",
        description="Preflight check for Proxmox Change Email PIC: inspect source and destination pools and validate old and new email ownership before moving VMIDs.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "source_pool": PROXMOX_POOL_PARAM,
                "destination_pool": PROXMOX_POOL_PARAM,
                "vmids": PROXMOX_VMID_LIST_PARAM,
                "old_email": PROXMOX_OLD_EMAIL_PARAM,
                "new_email": PROXMOX_NEW_EMAIL_PARAM,
            },
            required=[
                "server_ref",
                "source_pool",
                "destination_pool",
                "vmids",
                "old_email",
                "new_email",
            ],
        ),
        execute=proxmox_preflight_move_vms_between_pools,
        prompt_hints=(
            "This is the Change Email PIC preflight. Run before `proxmox_move_vms_between_pools`. Confirm the exact source pool, destination pool, VMIDs, old email (current PIC), and new email (new PIC).",
            "Successful results return the source pool, destination pool, old user, new user, and requested VMIDs.",
            "When the user says 'change email PIC', 'change PIC', or 'move VMs between pools', use this tool.",
        ),
        result_schema=PROXMOX_PREFLIGHT_POOL_MOVE_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_move_vms_between_pools",
        description="Change Email PIC: move exact Proxmox VMIDs from one pool to another after preflight verification of old and new email ownership.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "source_pool": PROXMOX_POOL_PARAM,
                "destination_pool": PROXMOX_POOL_PARAM,
                "vmids": PROXMOX_VMID_LIST_PARAM,
                "old_email": PROXMOX_OLD_EMAIL_PARAM,
                "new_email": PROXMOX_NEW_EMAIL_PARAM,
                "reason": REASON_PARAM,
            },
            required=[
                "server_ref",
                "source_pool",
                "destination_pool",
                "vmids",
                "old_email",
                "new_email",
                "reason",
            ],
        ),
        execute=proxmox_move_vms_between_pools,
        prompt_hints=(
            "Run `proxmox_preflight_move_vms_between_pools` first and reuse the same server_ref, source_pool, destination_pool, vmids, old_email, and new_email.",
            "Idempotent result contract: returns `status` `changed` only after postflight confirms the VMIDs moved into the destination pool and out of the source pool.",
        ),
        result_schema=PROXMOX_POOL_MOVE_RESULT_SCHEMA,
        workflow_family="proxmox-pool-membership-move",
    ),
    ToolDefinition(
        name="proxmox_validate_server",
        description="Validate a Proxmox server reference by calling a lightweight API check.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={"server_ref": PROXMOX_SERVER_REF_PARAM},
            required=["server_ref"],
        ),
        execute=proxmox_validate_server,
        prompt_hints=(
            "Use this for Proxmox connectivity or credential validation.",
            "Success returns `ok: true` and `message: ok`; failures return `error_code`, `message`, and possibly `choices`.",
        ),
        result_schema=PROXMOX_VALIDATE_SERVER_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_preflight_vm_nic_toggle",
        description="Read the current Proxmox VM NIC state and strict digest before enabling or disabling one QEMU NIC.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
                "net": {
                    **PROXMOX_NET_PARAM,
                    "description": "Optional Proxmox NIC key such as net0. If omitted and the VM has exactly one NIC, the tool auto-selects it.",
                },
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_preflight_vm_nic_toggle,
        prompt_hints=(
            "Run this before `proxmox_disable_vm_nic` or `proxmox_enable_vm_nic` and summarize the current NIC link state plus digest.",
            "If multiple NICs exist and `net` is omitted, the tool returns `net_selection_required` and a `nets` list for user choice.",
        ),
        result_schema=PROXMOX_PREFLIGHT_NIC_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_disable_vm_nic",
        description="Disable one Proxmox QEMU VM NIC by setting link_down after matching preflight evidence and digest.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
                "net": PROXMOX_NET_PARAM,
                "digest": PROXMOX_DIGEST_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "node", "vmid", "net", "digest", "reason"],
        ),
        execute=proxmox_disable_vm_nic,
        prompt_hints=(
            "Run `proxmox_preflight_vm_nic_toggle` first and use the same server_ref, node, vmid, net, and digest.",
            "Idempotent result contract: returns `status` `no-op` if the NIC is already disabled, or `status` `changed` after task polling and verification confirm `link_down=1`.",
        ),
        result_schema=PROXMOX_NIC_CHANGE_RESULT_SCHEMA,
        workflow_family="proxmox-vm-nic-connectivity",
    ),
    ToolDefinition(
        name="proxmox_enable_vm_nic",
        description="Enable one Proxmox QEMU VM NIC by removing link_down after matching preflight evidence and digest.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": PROXMOX_SERVER_REF_PARAM,
                "node": PROXMOX_NODE_PARAM,
                "vmid": PROXMOX_VMID_PARAM,
                "net": PROXMOX_NET_PARAM,
                "digest": PROXMOX_DIGEST_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "node", "vmid", "net", "digest", "reason"],
        ),
        execute=proxmox_enable_vm_nic,
        prompt_hints=(
            "Run `proxmox_preflight_vm_nic_toggle` first and use the same server_ref, node, vmid, net, and digest.",
            "Idempotent result contract: returns `status` `no-op` if the NIC is already enabled, or `status` `changed` after task polling and verification confirm `link_down` is absent.",
        ),
        result_schema=PROXMOX_NIC_CHANGE_RESULT_SCHEMA,
        workflow_family="proxmox-vm-nic-connectivity",
    ),
)
