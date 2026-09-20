import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

// The admin servers transport, shared by the WHM, PMG and Proxmox verticals.
// All three speak the same five-route CRUD-and-validate contract over the same
// response envelopes (`{ servers }` for the list, `{ server }` for one row), so
// the only thing that differs between them is the base path and the validate
// response type.
//
// Every call goes through the shared fetchWithAuth + jsonOrThrow helpers, so a
// 401 triggers the session-expiry flow and any non-OK response throws a typed
// ApiError that preserves the backend's stable message / error_code /
// request_id. Callers surface that message verbatim — the FastAPI guards stay
// authoritative. Secrets are only ever in request bodies, never in responses:
// the safe views these calls return carry no secret material at all, which is
// why a list response can be handed straight to the table.

export type ServersApi<TServer, TValidate> = {
  fetchServers: () => Promise<TServer[]>
  createServer: (body: Record<string, unknown>) => Promise<TServer>
  updateServer: (serverId: string, body: Record<string, unknown>) => Promise<TServer>
  deleteServer: (serverId: string) => Promise<void>
  validateServer: (serverId: string) => Promise<TValidate>
}

const jsonBody = (body: Record<string, unknown>) => ({
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
})

export function makeServersApi<TServer, TValidate>(
  basePath: string,
): ServersApi<TServer, TValidate> {
  return {
    fetchServers: async () => {
      const payload = await jsonOrThrow<{ servers?: TServer[] }>(await fetchWithAuth(basePath))
      return Array.isArray(payload.servers) ? payload.servers : []
    },
    createServer: async (body) => {
      const response = await fetchWithAuth(basePath, { method: 'POST', ...jsonBody(body) })
      return (await jsonOrThrow<{ server: TServer }>(response)).server
    },
    updateServer: async (serverId, body) => {
      const response = await fetchWithAuth(`${basePath}/${serverId}`, {
        method: 'PATCH',
        ...jsonBody(body),
      })
      return (await jsonOrThrow<{ server: TServer }>(response)).server
    },
    deleteServer: async (serverId) => {
      await jsonOrThrow(await fetchWithAuth(`${basePath}/${serverId}`, { method: 'DELETE' }))
    },
    validateServer: async (serverId) =>
      jsonOrThrow<TValidate>(
        await fetchWithAuth(`${basePath}/${serverId}/validate`, { method: 'POST' }),
      ),
  }
}
