import type { StatusChipStatus } from '@gio/bigsu-ui'

import { coerceStringArray } from './users-api'
import type { AdminUser } from './types'

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

export function formatRelativeTime(value: unknown): string {
  if (typeof value !== 'string' || !value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Never'

  const diffMs = Date.now() - date.getTime()
  if (diffMs < 0) return 'Just now'

  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 60) return 'Just now'

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
