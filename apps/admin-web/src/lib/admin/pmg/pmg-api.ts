import { makeServersApi } from '@/lib/admin/shared/servers-api'

import type { PmgServer, ValidatePmgServerResponse } from './types'

// Transport for the admin PMG vertical (issue #110), bound to the shared
// servers transport. The FastAPI guards (PMG_SERVER_NAME_EXISTS,
// PMG_SERVER_NOT_FOUND) stay authoritative; this layer only names the base path.

const api = makeServersApi<PmgServer, ValidatePmgServerResponse>('/admin/pmg/servers')

export const fetchPmgServers = api.fetchServers
export const createPmgServer = api.createServer
export const updatePmgServer = api.updateServer
export const deletePmgServer = api.deleteServer
export const validatePmgServer = api.validateServer
