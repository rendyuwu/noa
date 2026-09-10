import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  ListWhmServersResponse,
  ValidateWhmServerResponse,
  WhmServer,
  WhmServerResponse,
} from './types'

// Transport for the admin WHM vertical (issue #108). Every call goes through the
// shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the session-
// expiry flow and any non-OK response throws a typed ApiError that preserves the
// backend's stable message / error_code / request_id. Callers surface that
// message verbatim — the FastAPI guards (whm_server_name_exists,
// whm_server_not_found) stay authoritative. Secrets are only ever in request
// bodies, never in responses.
//
// Five reseller-token calls lived here until the admin validate route removed them: NOA has no
// whm_server_tokens table and the admin API's contract names no such route (see ./types.ts).

export async function fetchWhmServers(): Promise<WhmServer[]> {
  const response = await fetchWithAuth('/admin/whm/servers')
  const payload = await jsonOrThrow<ListWhmServersResponse>(response)
  return Array.isArray(payload.servers) ? payload.servers : []
}

export async function createWhmServer(body: Record<string, unknown>): Promise<WhmServer> {
  const response = await fetchWithAuth('/admin/whm/servers', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<WhmServerResponse>(response)
  return payload.server
}

export async function updateWhmServer(
  serverId: string,
  body: Record<string, unknown>,
): Promise<WhmServer> {
  const response = await fetchWithAuth(`/admin/whm/servers/${serverId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<WhmServerResponse>(response)
  return payload.server
}

export async function deleteWhmServer(serverId: string): Promise<void> {
  const response = await fetchWithAuth(`/admin/whm/servers/${serverId}`, { method: 'DELETE' })
  await jsonOrThrow(response)
}

export async function validateWhmServer(serverId: string): Promise<ValidateWhmServerResponse> {
  const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/validate`, {
    method: 'POST',
  })
  return jsonOrThrow<ValidateWhmServerResponse>(response)
}
