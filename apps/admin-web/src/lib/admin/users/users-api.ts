import { coerceRoleNames } from '@/lib/admin/shared/coerce'
import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  AdminRolesResponse,
  AdminUser,
  AdminUsersResponse,
  UpdateUserResponse,
} from './types'

// Coercion of loosely-typed API payloads now lives in the shared admin layer
// (issue #107), used identically by the Users and Roles verticals. Re-exported
// here so existing Users call sites keep importing it from users-api.
export { coerceRoleNames, coerceStringArray } from '@/lib/admin/shared/coerce'

// Transport for the admin Users vertical (issue #106). Every call goes through
// the shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the session-
// expiry flow and any non-OK response throws a typed ApiError that preserves the
// backend's stable detail / error_code / request_id. Callers surface that detail
// verbatim — the FastAPI guards (LAST_ACTIVE_ADMIN, SELF_DEACTIVATE_ADMIN,
// SELF_DELETE, SELF_REMOVE_ADMIN_ROLE, UNKNOWN_ROLES, …) stay authoritative.

export type UsersAndRoles = {
  users: AdminUser[]
  roles: string[]
}

// Load just the list. Split out of fetchUsersAndRoles for the admin MCP tokens
// page, which needs one user's email for its breadcrumb and has no role
// assignment control to feed — pulling `/admin/roles` for it would be a request
// whose answer is discarded.
export async function fetchUsers(): Promise<AdminUser[]> {
  const response = await fetchWithAuth('/admin/users')
  const payload = await jsonOrThrow<AdminUsersResponse>(response)
  return Array.isArray(payload.users) ? payload.users : []
}

// Load the list and the role catalogue together — the Users page needs both to
// render the table and drive role assignment. Both requests are still issued
// before either is awaited. Roles are de-duplicated and sorted so the assignment
// control is stable between loads.
export async function fetchUsersAndRoles(): Promise<UsersAndRoles> {
  const [users, rolesPayload] = await Promise.all([
    fetchUsers(),
    fetchWithAuth('/admin/roles').then((response) =>
      jsonOrThrow<AdminRolesResponse>(response),
    ),
  ])

  const roles = Array.from(new Set(coerceRoleNames(rolesPayload.roles))).sort((a, b) =>
    a.localeCompare(b),
  )

  return { users, roles }
}

// PUT the full role set. The backend returns the updated user (source of truth
// for the effective tools the roles resolve to), which we thread back into the
// list so the UI reflects the server result rather than an optimistic guess.
export async function setUserRoles(userId: string, roles: string[]): Promise<AdminUser> {
  const response = await fetchWithAuth(`/admin/users/${userId}/roles`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ roles }),
  })
  const payload = await jsonOrThrow<UpdateUserResponse>(response)
  return payload.user
}

// PATCH activation. Returns the updated user so the derived status stays honest.
export async function setUserActive(userId: string, isActive: boolean): Promise<AdminUser> {
  const response = await fetchWithAuth(`/admin/users/${userId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  })
  const payload = await jsonOrThrow<UpdateUserResponse>(response)
  return payload.user
}

// DELETE a user. The endpoint returns `{ ok: true }`; we only need to confirm a
// non-error response before dropping the row.
export async function deleteUser(userId: string): Promise<void> {
  const response = await fetchWithAuth(`/admin/users/${userId}`, { method: 'DELETE' })
  await jsonOrThrow(response)
}
