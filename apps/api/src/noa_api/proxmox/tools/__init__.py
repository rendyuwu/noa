from __future__ import annotations

from noa_api.proxmox.tools import cloudinit_tools
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

__all__ = [
    "cloudinit_tools",
    "proxmox_disable_vm_nic",
    "proxmox_enable_vm_nic",
    "proxmox_get_user_by_email",
    "proxmox_get_vm_config",
    "proxmox_get_vm_pending",
    "proxmox_get_vm_status_current",
    "proxmox_list_servers",
    "proxmox_move_vms_between_pools",
    "proxmox_preflight_vm_cloudinit_password_reset",
    "proxmox_preflight_vm_nic_toggle",
    "proxmox_preflight_move_vms_between_pools",
    "proxmox_reset_vm_cloudinit_password",
    "proxmox_validate_server",
]
