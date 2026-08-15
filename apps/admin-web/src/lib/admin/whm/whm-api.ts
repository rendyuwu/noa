import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  ListWhmServersResponse,
  ListWhmServerTokensResponse,
  ValidateWhmServerResponse,
  WhmServer,
  WhmServerResponse,
  WhmServerToken,
  WhmServerTokenResponse,
} from './types'

// Transport for the admin WHM vertical (issue #108). Every call goes through the
// shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the session-
// expiry flow and any non-OK response throws a typed ApiError that preserves the
// backend's stable detail / error_code / request_id. Callers surface that detail
// verbatim — the FastAPI guards (WHM_SERVER_NAME_EXISTS, WHM_SERVER_NOT_FOUND,
// WHM_SERVER_TOKEN_OWNER_EXISTS, WHM_SERVER_TOKEN_NOT_FOUND, …) stay
// authoritative. Secrets are only ever in request bodies, never in responses.

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

export async function fetchWhmServerTokens(serverId: string): Promise<WhmServerToken[]> {
  const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/tokens`)
  const payload = await jsonOrThrow<ListWhmServerTokensResponse>(response)
  return Array.isArray(payload.tokens) ? payload.tokens : []
}

export async function createWhmServerToken(
  serverId: string,
  body: { owner_username: string; api_username: string; api_token: string },
): Promise<WhmServerToken> {
  const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/tokens`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<WhmServerTokenResponse>(response)
  return payload.token
}

// Rotate/rename a token. owner_username is the routing key and is intentionally
// omitted — the backend keeps it fixed on update; api_token is only sent when a
// new value was entered, so a rename never rotates the stored secret.
export async function updateWhmServerToken(
  serverId: string,
  tokenId: string,
  body: { api_username: string; api_token?: string },
): Promise<WhmServerToken> {
  const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/tokens/${tokenId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await jsonOrThrow<WhmServerTokenResponse>(response)
  return payload.token
}

export async function deleteWhmServerToken(serverId: string, tokenId: string): Promise<void> {
  const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/tokens/${tokenId}`, {
    method: 'DELETE',
  })
  await jsonOrThrow(response)
}

export async function validateWhmServerToken(
  serverId: string,
  tokenId: string,
): Promise<ValidateWhmServerResponse> {
  const response = await fetchWithAuth(
    `/admin/whm/servers/${serverId}/tokens/${tokenId}/validate`,
    { method: 'POST' },
  )
  return jsonOrThrow<ValidateWhmServerResponse>(response)
}
