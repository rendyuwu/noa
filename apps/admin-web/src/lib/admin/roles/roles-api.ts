import { coerceRoleNames, coerceStringArray } from '@/lib/admin/shared/coerce'
import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  AdminRoleToolsResponse,
  AdminRolesResponse,
  AdminToolsResponse,
  DirectGrantsMigrationResponse,
} from './types'

// Transport for the admin Roles vertical (issue #107). Every call goes through
// the shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the session-
// expiry flow and any non-OK response throws a typed ApiError that preserves the
// backend's stable detail / error_code / request_id. Callers surface that detail
// verbatim — the FastAPI guards (ROLE_EXISTS, UNKNOWN_ROLES,
// INTERNAL_ROLE_FORBIDDEN, ROLE_IN_USE, …) stay authoritative.

export type RolesAndTools = {
  roles: string[]
  tools: string[]
}

// Load the role list and the tool catalogue together — the page needs both to
// render the table and drive allowlist editing. Both are de-duplicated (roles)
// and sorted so the list and the allowlist control stay stable between loads.
export async function fetchRolesAndTools(): Promise<RolesAndTools> {
  const [rolesResponse, toolsResponse] = await Promise.all([
    fetchWithAuth('/admin/roles'),
    fetchWithAuth('/admin/tools'),
  ])

  const [rolesPayload, toolsPayload] = await Promise.all([
    jsonOrThrow<AdminRolesResponse>(rolesResponse),
    jsonOrThrow<AdminToolsResponse>(toolsResponse),
  ])

  const roles = Array.from(new Set(coerceRoleNames(rolesPayload.roles))).sort((a, b) =>
    a.localeCompare(b),
  )
  const tools = coerceStringArray(toolsPayload.tools)
    .slice()
    .sort((a, b) => a.localeCompare(b))

  return { roles, tools }
}

// GET one role's allowlist. Returns the sorted tool set so the editor and the
// list count both read a stable order.
export async function fetchRoleTools(roleName: string): Promise<string[]> {
  const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}/tools`)
  const payload = await jsonOrThrow<AdminRoleToolsResponse>(response)
  return coerceStringArray(payload.tools)
    .slice()
    .sort((a, b) => a.localeCompare(b))
}

// POST a new role name. The endpoint returns `{ ok: true }`; a duplicate name is
// rejected by the backend (ROLE_EXISTS, 409) and surfaced verbatim.
export async function createRole(name: string): Promise<void> {
  const response = await fetchWithAuth('/admin/roles', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  await jsonOrThrow(response)
}

// DELETE a role. The endpoint returns `{ ok: true }`; a role still in use or an
// internal role is rejected by the backend and surfaced verbatim.
export async function deleteRole(roleName: string): Promise<void> {
  const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}`, {
    method: 'DELETE',
  })
  await jsonOrThrow(response)
}

// PUT the full tool allowlist for a role. The backend validates tool names
// (UNKNOWN_TOOLS) and stays authoritative over what the role resolves to.
export async function setRoleTools(roleName: string, tools: string[]): Promise<void> {
  const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}/tools`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ tools }),
  })
  await jsonOrThrow(response)
}

// POST the legacy direct-grant migration. Returns the server's summary payload
// (shape normalised by summarizeDirectGrantsMigration). Safe to run repeatedly.
export async function migrateDirectGrants(): Promise<DirectGrantsMigrationResponse> {
  const response = await fetchWithAuth('/admin/migrations/direct-grants', { method: 'POST' })
  return jsonOrThrow<DirectGrantsMigrationResponse>(response)
}
