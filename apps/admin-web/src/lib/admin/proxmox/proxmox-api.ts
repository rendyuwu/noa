import { makeServersApi } from '@/lib/admin/shared/servers-api'

import type { ProxmoxServer, ValidateProxmoxServerResponse } from './types'

// Transport for the admin Proxmox vertical (issue #109), bound to the shared
// servers transport. The FastAPI guards (PROXMOX_SERVER_NAME_EXISTS,
// PROXMOX_SERVER_NOT_FOUND) stay authoritative; this layer only names the base
// path.

const api = makeServersApi<ProxmoxServer, ValidateProxmoxServerResponse>('/admin/proxmox/servers')

export const fetchProxmoxServers = api.fetchServers
export const createProxmoxServer = api.createServer
export const updateProxmoxServer = api.updateServer
export const deleteProxmoxServer = api.deleteServer
export const validateProxmoxServer = api.validateServer
