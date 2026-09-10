"""Proxmox VE integration layer.

Copied from `noa-old` branch `MCP` (`noa_api/proxmox/integrations/`) rather than rewritten
(C13, V69). One transport, unlike WHM: Proxmox exposes everything NOA needs over its HTTP API,
so there is no SSH path here and a `proxmox_servers` row carries no SSH credentials (I.ext).

Modules:

- `client`    — `ProxmoxClient` + `build_proxmox_client_from_creds` / `build_proxmox_client`.
                Normalises Proxmox's four different refusal shapes into stable `error_code`
                strings, and splits the two that need distinct operator action:
                `permission_denied` (fix the ACL in Proxmox, ⊥ the token in NOA) and
                `digest_mismatch` (re-read, ⊥ retry).
- `cloudinit` — what cloud-init says about a VM's password, and whether NOA may claim a reset
                took. Holds the crypt-compare and, critically, the third answer
                `noa-old` did not have: **unavailable**, for a host whose libcrypt will not load
                (V62 — verification-unavailable ≠ verified).
- `nic`        — the `netN` codec: what one NIC line says, and how to flip its link state without
                dropping a segment. Here rather than in `mcp_tools/` because the grammar is
                Proxmox's, and because `proxmox_vm_nic`'s two halves both need it.

There is no `errors.py` here, unlike WHM's: this layer returns dicts and raises nothing, because
Proxmox needs no CLI over SSH. `SecretCipher` is injected rather than imported, following T15 —
there is no settings singleton in this repo.

Consumers: `proxmox_reset_vm_password`, `proxmox_vm_nic` and the admin server CRUD +
validate routes. Reference doc: `docs/integrations/proxmox.md`.

Not ported from `MCP`: the C22 never-implement client methods (`get_user`, `get_pool`,
`get_effective_permissions`, `add_vms_to_pool`, `remove_vms_from_pool`) and the
`core/workflows/proxmox/` presentation layer that C16 drops with chat. Server-ref resolution
landed at T27, in `core.servers.proxmox_ref` — *finding* a server belongs to `core/servers/`
(T19's split), and this package talks to one it has been handed.
"""

from core.integrations.proxmox.client import (
    ProxmoxClient,
    ProxmoxClientFactory,
    ProxmoxServerSecretLike,
    build_proxmox_client,
    build_proxmox_client_from_creds,
)
from core.integrations.proxmox.cloudinit import (
    CloudInitPasswordVerification,
    CryptVerdict,
    cloudinit_carries_password,
    verify_cloudinit_password,
)
from core.integrations.proxmox.nic import (
    LINK_STATE_DOWN,
    LINK_STATE_UP,
    NetworkInterface,
    find_nic,
    list_nics,
    net_link_state,
    set_link_down,
)

__all__ = [
    "LINK_STATE_DOWN",
    "LINK_STATE_UP",
    "CloudInitPasswordVerification",
    "CryptVerdict",
    "NetworkInterface",
    "ProxmoxClient",
    "ProxmoxClientFactory",
    "ProxmoxServerSecretLike",
    "build_proxmox_client",
    "build_proxmox_client_from_creds",
    "cloudinit_carries_password",
    "find_nic",
    "list_nics",
    "net_link_state",
    "set_link_down",
    "verify_cloudinit_password",
]
