import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  ListPmgServersResponse,
  PmgServer,
  PmgServerResponse,
  ValidatePmgServerResponse,
} from './types'

// Transport for the admin PMG vertical (issue #110). Every call goes through the
// shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the session-
// expiry flow and any non-OK response throws a typed ApiError that preserves the
// backend's stable detail / error_code / request_id. Callers surface that detail
// verbatim — the FastAPI guards (PMG_SERVER_NAME_EXISTS, PMG_SERVER_NOT_FOUND)
// stay authoritative. Secrets are only ever in request bodies, never in responses.

export async function fetchPmgServers(): Promise<PmgServer[]> {
  const response = await fetchWithAuth('/admin/pmg/servers')
  const payload = await jsonOrThrow<ListPmgServersResponse>(response)
  return Array.isArray(payload.servers) ? payload.servers : []
}

export async function createPmgServer(body: Record<string, unknown>): Promise<PmgServer> {
  const response = await fetchWithAuth('/admin/pmg/servers', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<PmgServerResponse>(response)
  return payload.server
}

export async function updatePmgServer(
  serverId: string,
  body: Record<string, unknown>,
): Promise<PmgServer> {
  const response = await fetchWithAuth(`/admin/pmg/servers/${serverId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<PmgServerResponse>(response)
  return payload.server
}

export async function deletePmgServer(serverId: string): Promise<void> {
  const response = await fetchWithAuth(`/admin/pmg/servers/${serverId}`, { method: 'DELETE' })
  await jsonOrThrow(response)
}

export async function validatePmgServer(serverId: string): Promise<ValidatePmgServerResponse> {
  const response = await fetchWithAuth(`/admin/pmg/servers/${serverId}/validate`, {
    method: 'POST',
  })
  return jsonOrThrow<ValidatePmgServerResponse>(response)
}
