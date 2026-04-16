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

##### Read current QEMU VM config
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'
```

##### Update QEMU VM config
Current implementation uses this for NIC enable/disable.
Those NIC CHANGE actions require approval, a recorded reason, and captured before/after evidence.

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

## Current Proxmox features actually implemented
- Proxmox server inventory CRUD in NOA admin UI/API
- Proxmox server connectivity validation
- QEMU VM NIC preflight
- Disable VM network interface with approval, recorded reason, and evidence capture
- Enable VM network interface with approval, recorded reason, and evidence capture

## Backlog / research notes
- QEMU cloud-init password reset flow
- QEMU guest-agent password reset flow
- VM status/resource lookup workflow improvements for very large clusters
- Pool membership move workflow
- Pool ACL / permission move workflow by user email
- Pool-based “change email” operational flow

## 1) Reset password from cloud-init (`qemu` only)

### Set cloud-init password on the VM
```bash
curl -k -X POST \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'cipassword=<new-password>' \
  --data-urlencode 'ciuser=<guest-user>' \
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
- If you need immediate password change and QEMU Guest Agent is installed, official endpoint is:

```bash
curl -k -X POST \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'username=<guest-user>' \
  --data-urlencode 'password=<new-password>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/agent/set-user-password'
```

## 2) Check VM status / resources / start / stop / hardware / disk / RAM

### Find VM cluster-wide by VMID, including `node` and `pool`
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/cluster/resources?type=vm'
```

### Large cluster note
- Officially, `GET /cluster/resources` only documents `type` as query param.
- I did **not** find an official server-side `vmid` or `search` filter for this endpoint.
- For very large clusters, better official alternatives are:

#### Per-node QEMU list
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu'
```

#### Per-node QEMU list with `full=1`
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu?full=1'
```

#### Direct VM lookup when node is already known
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/config'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/status/current'
```

### Live runtime status/usage
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/status/current'
```

### Full VM config/hardware
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

### Start VM
```bash
curl -k -X POST \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/status/start'
```

### Stop VM
```bash
curl -k -X POST \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/nodes/<node>/qemu/<vmid>/status/stop'
```

### Notes
- `status/current` = runtime state + CPU/mem/disk/net counters.
- `config` = hardware/disk/RAM/CPU/network definitions.
- `cluster/resources?type=vm` = best first call if you only know `vmid`.

## 3) Change email

### If you mean literal Proxmox user email
```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'email=<new-email@example.com>' \
  'https://<pve-host>:8006/api2/json/access/users/<userid>'
```

### Read user(s)
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/users'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/users/<userid>'
```

## 3b) Pool membership + pool permissions flow for “change email”

Important:
- Pools store VM/storage membership via the pool endpoints.
- The user email shown in the pool **Permissions** tab is not a pool field; it is an ACL entry on path `/pool/<poolid>`.
- So “change email pool” usually means two separate actions:
  1. change VM membership between pools
  2. change ACL user permission between old/new pool

### 1. Search VM pool id from VMID
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/cluster/resources?type=vm'
```

### 1b. Search user by email
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/users'
```

### 2. Verify pool member and user ACL
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/pools?poolid=<old-poolid>'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/users/<userid>'
```

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/acl'
```

Filter client-side for:
- `path=/pool/<old-poolid>`
- `type=user`
- `ugid=<email@realm>`

### 2b. Optional: verify effective permission for one user on one pool
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --get \
  --data-urlencode 'userid=<email@realm>' \
  --data-urlencode 'path=/pool/<old-poolid>' \
  'https://<pve-host>:8006/api2/json/access/permissions'
```

### 3. Remove VM from old pool
```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'poolid=<old-poolid>' \
  --data-urlencode 'vms=<vmid>' \
  --data-urlencode 'delete=1' \
  'https://<pve-host>:8006/api2/json/pools'
```

### 4. Verify new pool exists / inspect members
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/pools?poolid=<new-poolid>'
```

### 4b. Verify new pool ACL target
```bash
curl -k \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  'https://<pve-host>:8006/api2/json/access/acl'
```

Filter client-side for:
- `path=/pool/<new-poolid>`
- confirm whether the target email/user already exists there

### 5. Add VM to new pool
```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'poolid=<new-poolid>' \
  --data-urlencode 'vms=<vmid>' \
  --data-urlencode 'allow-move=1' \
  'https://<pve-host>:8006/api2/json/pools'
```

### 6. Remove old pool permission for old email/user
```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'path=/pool/<old-poolid>' \
  --data-urlencode 'users=<old-email@realm>' \
  --data-urlencode 'roles=<roleid>' \
  --data-urlencode 'delete=1' \
  'https://<pve-host>:8006/api2/json/access/acl'
```

### 7. Add new pool permission for new email/user
```bash
curl -k -X PUT \
  -H 'Authorization: PVEAPIToken=<user>@<realm>!<tokenid>=<secret>' \
  --data-urlencode 'path=/pool/<new-poolid>' \
  --data-urlencode 'users=<new-email@realm>' \
  --data-urlencode 'roles=<roleid>' \
  --data-urlencode 'propagate=1' \
  'https://<pve-host>:8006/api2/json/access/acl'
```
