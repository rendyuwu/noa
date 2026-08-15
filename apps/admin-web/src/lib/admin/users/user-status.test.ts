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

describe('formatRelativeTime', () => {
  it('returns Never for missing or invalid values', () => {
    expect(formatRelativeTime(null)).toBe('Never')
    expect(formatRelativeTime('not-a-date')).toBe('Never')
  })

  it('describes a recent timestamp in relative terms', () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
    expect(formatRelativeTime(twoHoursAgo)).toBe('2 hours ago')
  })
})
