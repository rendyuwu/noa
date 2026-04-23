from __future__ import annotations

from noa_api.core.workflows.proxmox.cloudinit_password_reset import (
    ProxmoxVMCloudinitPasswordResetTemplate,
)
from noa_api.core.workflows.proxmox.nic_connectivity import (
    ProxmoxVMNicConnectivityTemplate,
)
from noa_api.core.workflows.proxmox.pool_membership_move import (
    ProxmoxPoolMembershipMoveTemplate,
)
from noa_api.core.workflows.proxmox.postflight import (
    _resolve_proxmox_client,
)
from noa_api.core.workflows.types import WorkflowTemplate
from noa_api.proxmox.tools.nic_tools import proxmox_preflight_vm_nic_toggle

WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {
    "proxmox-vm-nic-connectivity": ProxmoxVMNicConnectivityTemplate(),
    "proxmox-vm-cloudinit-password-reset": ProxmoxVMCloudinitPasswordResetTemplate(),
    "proxmox-pool-membership-move": ProxmoxPoolMembershipMoveTemplate(),
}

__all__ = [
    "WORKFLOW_TEMPLATES",
    "ProxmoxVMNicConnectivityTemplate",
    "ProxmoxVMCloudinitPasswordResetTemplate",
    "ProxmoxPoolMembershipMoveTemplate",
    "_resolve_proxmox_client",
    "proxmox_preflight_vm_nic_toggle",
]
