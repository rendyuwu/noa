'use client'

import {
  type MutationResult,
  type ServersController,
  useServersController,
} from '@/lib/admin/shared/use-servers-controller'

import type { PmgServer, ValidatePmgServerResponse } from './types'
import {
  createPmgServer,
  deletePmgServer,
  fetchPmgServers,
  updatePmgServer,
  validatePmgServer,
} from './pmg-api'

// The PMG servers data controller (issue #110). All the race-guard machinery it
// once carried now lives in the shared useServersController (#131 finding #15);
// this wrapper only binds the PMG api transport and the PMG-specific copy. PMG is
// SSH-only, so a successful validate can refresh the pinned host-key fingerprint
// — the shared silentRefresh already picks that up. The exported MutationResult /
// PmgServersController aliases keep the page and drawer imports unchanged.

export type { MutationResult }

export type PmgServersController = ServersController<PmgServer, ValidatePmgServerResponse>

export function usePmgServers(): PmgServersController {
  return useServersController<PmgServer, ValidatePmgServerResponse>({
    fetchServers: fetchPmgServers,
    createServer: createPmgServer,
    updateServer: updatePmgServer,
    deleteServer: deletePmgServer,
    validateServer: validatePmgServer,
    messages: {
      load: 'Unable to load PMG servers',
      create: 'Unable to create PMG server',
      update: 'Unable to update PMG server',
      delete: 'Unable to delete PMG server',
      validate: 'Validation failed',
    },
  })
}
