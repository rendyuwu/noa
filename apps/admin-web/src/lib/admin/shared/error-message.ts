import { ApiError } from '@/lib/auth/fetch-helper'

// Turn any thrown value into a user-facing message that preserves the backend's
// stable detail (issues #106/#107). A typed ApiError carries the server's
// `detail` verbatim — the wording the API guarantees for LAST_ACTIVE_ADMIN,
// SELF_DELETE, UNKNOWN_ROLES, ROLE_EXISTS and friends — so we surface that
// instead of a generic string. Shared by the Users and Roles admin verticals.
export function toMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.detail || fallback
  if (error instanceof Error && error.message) return error.message
  return fallback
}
