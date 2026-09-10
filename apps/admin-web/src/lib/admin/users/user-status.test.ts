import { describe, expect, it } from 'vitest'

import type { AdminUser } from './types'
import {
  activeAdminCount,
  deriveUserStatus,
  formatRelativeTime,
  hasAdminRole,
  isLastActiveAdmin,
  isSelf,
} from './user-status'

// `formatRelativeTime` itself moved to `admin/shared/relative-time.ts` and
// is covered there. What stays here is the re-export: Users call sites import it
// from this module, so the seam has to keep working.

const user = (over: Partial<AdminUser> = {}): AdminUser => ({
  id: 'u1',
  email: 'user@example.com',
  ...over,
})

describe('deriveUserStatus', () => {
  it('maps an active account to Active', () => {
    expect(deriveUserStatus(user({ is_active: true }))).toBe('Active')
    // Missing is_active defaults to active (contract parity with the legacy app).
    expect(deriveUserStatus(user({}))).toBe('Active')
  })

  it('maps a disabled account that has signed in before to Inactive (BIGSU vocabulary)', () => {
    expect(deriveUserStatus(user({ is_active: false, last_login_at: '2026-01-01T00:00:00Z' }))).toBe(
      'Inactive',
    )
  })

  it('maps a disabled, never-signed-in account to Pending', () => {
    expect(deriveUserStatus(user({ is_active: false, last_login_at: null }))).toBe('Pending')
    expect(deriveUserStatus(user({ is_active: false }))).toBe('Pending')
  })
})

describe('self and admin guards', () => {
  it('isSelf compares against the acting admin id', () => {
    expect(isSelf(user({ id: 'me' }), 'me')).toBe(true)
    expect(isSelf(user({ id: 'other' }), 'me')).toBe(false)
  })

  it('hasAdminRole is case-insensitive', () => {
    expect(hasAdminRole(user({ roles: ['Admin'] }))).toBe(true)
    expect(hasAdminRole(user({ roles: ['member'] }))).toBe(false)
  })

  it('counts only active admins', () => {
    const users = [
      user({ id: '1', roles: ['admin'], is_active: true }),
      user({ id: '2', roles: ['admin'], is_active: false }),
      user({ id: '3', roles: ['member'], is_active: true }),
    ]
    expect(activeAdminCount(users)).toBe(1)
  })

  it('flags the last active admin, and only while they are the last', () => {
    const soleAdmin = user({ id: '1', roles: ['admin'], is_active: true })
    expect(isLastActiveAdmin(soleAdmin, [soleAdmin])).toBe(true)

    const secondAdmin = user({ id: '2', roles: ['admin'], is_active: true })
    expect(isLastActiveAdmin(soleAdmin, [soleAdmin, secondAdmin])).toBe(false)

    // A non-admin, or an already-inactive admin, is never "the last active admin".
    const member = user({ id: '3', roles: ['member'], is_active: true })
    expect(isLastActiveAdmin(member, [member])).toBe(false)
  })
})

describe('formatRelativeTime re-export', () => {
  it('is the shared implementation, reachable from user-status', async () => {
    const shared = await import('@/lib/admin/shared/relative-time')
    expect(formatRelativeTime).toBe(shared.formatRelativeTime)
    expect(formatRelativeTime(null)).toBe('Never')
  })
})
