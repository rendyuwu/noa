'use client'

import {
  type MutationResult,
  type ServersController,
  useServersController,
} from '@/lib/admin/shared/use-servers-controller'

import type { ProxmoxServer, ValidateProxmoxServerResponse } from './types'
import {
  createProxmoxServer,
  deleteProxmoxServer,
  fetchProxmoxServers,
  updateProxmoxServer,
  validateProxmoxServer,
} from './proxmox-api'

// The Proxmox servers data controller (issue #109). All the race-guard machinery
// it once carried now lives in the shared useServersController (#131 finding
// #15); this wrapper only binds the Proxmox api transport and the Proxmox-
// specific copy. The exported MutationResult / ProxmoxServersController aliases
// keep the page and drawer imports unchanged.

export type { MutationResult }

export type ProxmoxServersController = ServersController<ProxmoxServer, ValidateProxmoxServerResponse>

export function useProxmoxServers(): ProxmoxServersController {
  return useServersController<ProxmoxServer, ValidateProxmoxServerResponse>({
    fetchServers: fetchProxmoxServers,
    createServer: createProxmoxServer,
    updateServer: updateProxmoxServer,
    deleteServer: deleteProxmoxServer,
    validateServer: validateProxmoxServer,
    messages: {
      load: 'Unable to load Proxmox servers',
      create: 'Unable to create Proxmox server',
      update: 'Unable to update Proxmox server',
      delete: 'Unable to delete Proxmox server',
      validate: 'Validation failed',
    },
  })
}
