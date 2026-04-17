# Proxmox Integration Reference

This is the canonical Proxmox integration reference for NOA.
It records the actual NOA admin endpoints, upstream Proxmox API calls, and currently
implemented Proxmox-backed features.

The backlog sections below capture candidate future work and related API research.
Update this file whenever the implemented Proxmox surface area or supporting API calls change.

## Connection base
- API base: `https://<pve-host>:8006/api2/json`
- Auth for examples: `-H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>'`

## Implemented in NOA today

### NOA admin endpoints currently implemented
- `GET /admin/proxmox/servers`
- `POST /admin/proxmox/servers`
- `PATCH /admin/proxmox/servers/{server_id}`
- `DELETE /admin/proxmox/servers/{server_id}`
- `POST /admin/proxmox/servers/{server_id}/validate`

### Browser proxy endpoints

Browser calls are made through the same-origin proxy as:
- `GET /api/admin/proxmox/servers`
- `POST /api/admin/proxmox/servers`
- `PATCH /api/admin/proxmox/servers/{server_id}`
- `DELETE /api/admin/proxmox/servers/{server_id}`
- `POST /api/admin/proxmox/servers/{server_id}/validate`

### Upstream Proxmox endpoints currently used by code

##### Validate Proxmox server connection
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/version'
```

##### Update QEMU VM config
Current implementation uses this for NIC enable/disable.

```bash
curl -k -X POST \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'digest=<digest-from-config>' \
  --data-urlencode 'net0=<updated-net-config>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'
```

##### Poll async task status
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/tasks/<upid>/status'
```

##### Cloud-init password reset / verification
```bash
curl -k -X POST \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'cipassword=<new-password>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'
```

```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit/dump?type=user'
```

##### Pool move preflight / permission / membership updates
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/pools?poolid=<old-poolid>'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/pools?poolid=<new-poolid>'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --get \
  --data-urlencode 'userid=<email@pve>' \
  --data-urlencode 'path=/pool/<poolid>' \
  'https://<pve-host>:8006/api2/json/access/permissions'
```

```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'poolid=<new-poolid>' \
  --data-urlencode 'vms=<vmid-list>' \
  --data-urlencode 'allow-move=1' \
  'https://<pve-host>:8006/api2/json/pools'
```

```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'poolid=<old-poolid>' \
  --data-urlencode 'vms=<vmid-list>' \
  --data-urlencode 'delete=1' \
  'https://<pve-host>:8006/api2/json/pools'
```

##### Read one user by email-derived userid
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/users/<email@pve>'
```

## Current Proxmox features actually implemented
- Proxmox server inventory CRUD in NOA admin UI/API
- Proxmox server connectivity validation
- QEMU VM NIC preflight
- Disable VM network interface with approval, recorded reason, and evidence capture
- Enable VM network interface with approval, recorded reason, and evidence capture
- Exact-node QEMU VM runtime status, config, and pending-change reads
- QEMU cloud-init password reset with approval, recorded reason, exact preflight matching, and postflight verification
- Pool membership move with approval, recorded reason, exact preflight matching, and postflight verification
- User lookup by email-derived Proxmox userid for pool preflight

## Backlog / research notes
- QEMU guest-agent password reset flow

## Implemented workflow details

### Cloud-init password reset workflow

### Set cloud-init password on the VM
- Only update the password; keep the existing guest username unchanged.

```bash
curl -k -X POST \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'cipassword=<new-password>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'
```

### Regenerate cloud-init drive
```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit'
```

### Verify cloud-init values
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/cloudinit/dump?type=user'
```

### Caveat
- This is cloud-init driven, not guaranteed immediate in-guest reset.
- After changing the password, tell the user they may need to restart the VM or do a stop/start cycle before the new password takes effect.
- NOA workflow replies should repeat that restart / stop-start caveat after a successful reset.

### Exact-node VM status / hardware workflow

This workflow requires the exact Proxmox node name. If the user only provides a VMID, ask for the node instead of using cluster-wide discovery endpoints.

### Live runtime status/usage
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/status/current'
```

### Full VM configuration/hardware
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'
```

### Pending config changes
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/pending'
```

### Notes
- `status/current` = runtime state + CPU/mem/disk/net counters.
- `config` = VM configuration/hardware definitions (disk/RAM/CPU/network).
- `pending` = pending config changes not yet applied.

### VM NIC toggle workflow

Use this flow for enabling or disabling a specific VM NIC on an exact node.

- Preflight-only NIC selection: when the VM has exactly one NIC, preflight may infer it; otherwise preflight returns `net_selection_required` and the available NIC list for user choice, and the ensuing CHANGE must use the concrete NIC key confirmed in that preflight.
- `proxmox_preflight_vm_nic_toggle` must come from the current turn/context so the approval step uses matching preflight data for the same `server_ref`, `node`, `vmid`, and selected NIC.
- Preflight returns the config digest; the CHANGE call must reuse that returned value exactly, and if the VM config changed in the meantime the operation fails closed with `digest_mismatch` and requires a fresh preflight.
- Enable/disable CHANGE calls are approval-gated and require a recorded non-empty reason.
- If the selected NIC is already in the requested state, the enable/disable request may return success with `status: "no-op"` and no config mutation.
- Postflight verification must re-read the VM config and confirm the requested final link state: `up` for enable, `down` for disable.
- Approval responses should summarize the selected NIC, the link-state transition, the digest, and the evidence expectations.
- Completion responses should summarize the selected NIC, the completed link-state transition, the digest used, and the verification evidence collected.

### Pool membership move workflow

Important:
- In this workflow, “change email” / “change PIC” means moving one or more VMs from one pool to another.
- Do not mutate Proxmox user email fields.
- Do not add or remove ACL entries as part of this flow.
- Ask the user directly for the source pool, destination pool, one or more VMIDs, and target email.

### Read one user by email-derived userid
- Do not call `GET /access/users`; require the email from the user.
- Backend/tool should normalize the provided email to a Proxmox userid by appending `@pve`.

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/users/<email@pve>'
```

Example normalization:
- User input: `l1@biznetgio.com`
- Tool userid: `l1@biznetgio.com@pve`

### Operational notes
- Preflight and postflight verification are required.
- Support moving one VM or multiple VMs in the same flow.
- Ask the user for explicit pool IDs, VMIDs, and email values instead of relying on large cluster-wide discovery endpoints.
- User-facing results should summarize before/after pool membership for the moved VM set.
- NOA workflow replies should render the pool membership tables from structured member data with columns `VMID`, `Name`, `Node`, and `Status`.
- Approval replies should show source pool before and destination pool before; completion replies should show source before/after and destination before/after.
- Implementation order is add-to-destination first, then remove-from-source only if the requested VMIDs still remain in the source pool, followed by final-state verification.
