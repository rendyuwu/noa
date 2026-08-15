'use client'

import {
  type MutationResult,
  type ServersController,
  useServersController,
} from '@/lib/admin/shared/use-servers-controller'

import type { ValidateWhmServerResponse, WhmServer } from './types'
import {
  createWhmServer,
  deleteWhmServer,
  fetchWhmServers,
  updateWhmServer,
  validateWhmServer,
} from './whm-api'

// The WHM servers data controller (issue #108). All the race-guard machinery it
// once carried now lives in the shared useServersController (#131 finding #15);
// this wrapper only binds the WHM api transport and the WHM-specific copy. The
// exported MutationResult / WhmServersController aliases keep the page and drawer
// imports unchanged.

export type { MutationResult }

export type WhmServersController = ServersController<WhmServer, ValidateWhmServerResponse>

export function useWhmServers(): WhmServersController {
  return useServersController<WhmServer, ValidateWhmServerResponse>({
    fetchServers: fetchWhmServers,
    createServer: createWhmServer,
    updateServer: updateWhmServer,
    deleteServer: deleteWhmServer,
    validateServer: validateWhmServer,
    messages: {
      load: 'Unable to load WHM servers',
      create: 'Unable to create WHM server',
      update: 'Unable to update WHM server',
      delete: 'Unable to delete WHM server',
      validate: 'Validation failed',
    },
  })
}
