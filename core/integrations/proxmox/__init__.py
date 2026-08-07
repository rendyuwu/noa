"""Proxmox VE integration layer (T17).

Copied from `noa-old` branch `MCP` (`noa_api/proxmox/integrations/`) rather than rewritten
(C13, V69). One transport, unlike WHM: Proxmox exposes everything NOA needs over its HTTP API,
so there is no SSH path here and a `proxmox_servers` row carries no SSH credentials (I.ext).

Modules:

- `client` — `ProxmoxClient` + `build_proxmox_client_from_creds`. Normalises Proxmox's four
             different refusal shapes into stable `error_code` strings, and splits the two that
             need distinct operator action: `permission_denied` (fix the ACL in Proxmox, ⊥ the
             token in NOA) and `digest_mismatch` (re-read, ⊥ retry).

There is no `errors.py` here, unlike WHM's: this layer returns dicts and raises nothing, because
Proxmox needs no CLI over SSH. `SecretCipher` is injected rather than imported, following T15 —
there is no settings singleton in this repo (C7).

Consumers: the Proxmox tools (T27, T28) and the admin server CRUD + validate routes (T54).
Nothing imports this yet. Reference doc: `docs/integrations/proxmox.md`.

Not ported from `MCP`: the C22 never-implement client methods (`get_user`, `get_pool`,
`get_effective_permissions`, `add_vms_to_pool`, `remove_vms_from_pool`), `server_ref`
resolution (a tool-layer concern, and Proxmox's is not named in any §T line yet), and the
`core/workflows/proxmox/` presentation layer that C16 drops with chat.
"""

from core.integrations.proxmox.client import ProxmoxClient, build_proxmox_client_from_creds

__all__ = ["ProxmoxClient", "build_proxmox_client_from_creds"]
