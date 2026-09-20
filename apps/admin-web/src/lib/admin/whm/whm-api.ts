import { makeServersApi } from '@/lib/admin/shared/servers-api'

import type { ValidateWhmServerResponse, WhmServer } from './types'

// Transport for the admin WHM vertical (issue #108), bound to the shared
// servers transport. The FastAPI guards (whm_server_name_exists,
// whm_server_not_found) stay authoritative; this layer only names the base path.
//
// Five reseller-token calls lived here until the admin validate route removed them: NOA has no
// whm_server_tokens table and the admin API's contract names no such route (see ./types.ts).

const api = makeServersApi<WhmServer, ValidateWhmServerResponse>('/admin/whm/servers')

export const fetchWhmServers = api.fetchServers
export const createWhmServer = api.createServer
export const updateWhmServer = api.updateServer
export const deleteWhmServer = api.deleteServer
export const validateWhmServer = api.validateServer
