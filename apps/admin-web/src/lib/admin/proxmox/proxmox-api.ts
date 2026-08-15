import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  ListProxmoxServersResponse,
  ProxmoxServer,
  ProxmoxServerResponse,
  ValidateProxmoxServerResponse,
} from './types'

// Transport for the admin Proxmox vertical (issue #109). Every call goes through
// the shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the session-
// expiry flow and any non-OK response throws a typed ApiError that preserves the
// backend's stable detail / error_code / request_id. Callers surface that detail
// verbatim — the FastAPI guards (PROXMOX_SERVER_NAME_EXISTS,
// PROXMOX_SERVER_NOT_FOUND) stay authoritative. The API token secret is only ever
// in request bodies, never in responses.

export async function fetchProxmoxServers(): Promise<ProxmoxServer[]> {
  const response = await fetchWithAuth('/admin/proxmox/servers')
  const payload = await jsonOrThrow<ListProxmoxServersResponse>(response)
  return Array.isArray(payload.servers) ? payload.servers : []
}

export async function createProxmoxServer(body: Record<string, unknown>): Promise<ProxmoxServer> {
  const response = await fetchWithAuth('/admin/proxmox/servers', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<ProxmoxServerResponse>(response)
  return payload.server
}

export async function updateProxmoxServer(
  serverId: string,
  body: Record<string, unknown>,
): Promise<ProxmoxServer> {
  const response = await fetchWithAuth(`/admin/proxmox/servers/${serverId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<ProxmoxServerResponse>(response)
  return payload.server
}

export async function deleteProxmoxServer(serverId: string): Promise<void> {
  const response = await fetchWithAuth(`/admin/proxmox/servers/${serverId}`, { method: 'DELETE' })
  await jsonOrThrow(response)
}

export async function validateProxmoxServer(
  serverId: string,
): Promise<ValidateProxmoxServerResponse> {
  const response = await fetchWithAuth(`/admin/proxmox/servers/${serverId}/validate`, {
    method: 'POST',
  })
  return jsonOrThrow<ValidateProxmoxServerResponse>(response)
}
