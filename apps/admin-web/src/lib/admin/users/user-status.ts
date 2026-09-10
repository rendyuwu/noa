import type { StatusChipStatus } from '@gio/bigsu-ui'

import { coerceStringArray } from './users-api'
import type { AdminUser } from './types'

// Relative-time rendering moved to the shared admin layer so the MCP
// token tables render `Never` exactly as the Users table does. Re-exported here
// so existing Users call sites keep importing it from user-status — the same
// pattern `users-api.ts:14` uses for the coercers.
export { formatRelativeTime } from '@/lib/admin/shared/relative-time'

// User status + guard helpers (issue #106). These are pure so the race-free hook,
// the table columns, and the detail drawer all derive the same verdict, and so
// the awkward cases (last-admin, self-action, pending) are unit-tested directly.

const ADMIN_ROLE = 'admin'

export type UserStatus = Extract<StatusChipStatus, 'Active' | 'Inactive' | 'Pending'>

// Map the activation lifecycle onto the BIGSU status vocabulary. The legacy web
// app rendered Active / Disabled / Pending, but BIGSU only ships Active,
// Inactive and Pending — a disabled account that has signed in before maps to
// Inactive. This is a deliberate BIGSU-conflict correction, not a blind copy:
// "Disabled" is not a standard status. A never-signed-in disabled account is
// still Pending (awaiting first activation), distinct from a revoked one.
export function deriveUserStatus(user: AdminUser): UserStatus {
  if (user.is_active !== false) return 'Active'
  return user.last_login_at ? 'Inactive' : 'Pending'
}

export function isAdminRole(role: string): boolean {
  return role.toLowerCase() === ADMIN_ROLE
}

export function hasAdminRole(user: AdminUser): boolean {
  return coerceStringArray(user.roles).some(isAdminRole)
}

export function isActive(user: AdminUser): boolean {
  return user.is_active !== false
}

export function isSelf(user: AdminUser, meId: string): boolean {
  return user.id === meId
}

export function activeAdminCount(users: AdminUser[]): number {
  return users.filter((user) => isActive(user) && hasAdminRole(user)).length
}

// The last remaining active admin may not be deactivated, deleted, or stripped
// of the admin role — the backend rejects it with LAST_ACTIVE_ADMIN (409). The
// client mirrors the rule so the disallowed action never looks available; the
// server stays authoritative for the stale-data case.
export function isLastActiveAdmin(user: AdminUser, users: AdminUser[]): boolean {
  return isActive(user) && hasAdminRole(user) && activeAdminCount(users) === 1
}

export function formatDate(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

