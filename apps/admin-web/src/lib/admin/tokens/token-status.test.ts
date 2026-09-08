import { afterEach, describe, expect, it, vi } from 'vitest'

import { deriveTokenStatus, formatBinding, formatRelativeTime } from './token-status'
import type { McpToken } from './types'

const token = (over: Partial<McpToken> = {}): McpToken => ({
  id: 't1',
  user_id: 'u1',
  token_prefix: 'noa_abcd1234',
  label: null,
  librechat_user_id: null,
  last_used_at: null,
  last_ldap_check_at: null,
  expires_at: null,
  created_at: '2026-09-01T00:00:00Z',
  ...over,
})

afterEach(() => {
  vi.useRealTimers()
})

// The BIGSU vocabulary has no `Expired`, so an expired token is `Inactive`,
// the same mapping a deactivated user gets.
describe('deriveTokenStatus', () => {
  it('is Active while nothing has retired the row', () => {
    expect(deriveTokenStatus(token({ expires_at: null }))).toBe('Active')
  })

  it('is Active for an expiry still in the future', () => {
    const future = new Date(Date.now() + 60_000).toISOString()
    expect(deriveTokenStatus(token({ expires_at: future }))).toBe('Active')
  })

  it('is Inactive once the expiry is past', () => {
    const past = new Date(Date.now() - 60_000).toISOString()
    expect(deriveTokenStatus(token({ expires_at: past }))).toBe('Inactive')
  })

  it('flips exactly at the boundary, not a second early', () => {
    vi.useFakeTimers()
    const at = new Date('2026-09-09T12:00:00.000Z')
    vi.setSystemTime(at)
    expect(deriveTokenStatus(token({ expires_at: at.toISOString() }))).toBe('Inactive')
    vi.setSystemTime(new Date(at.getTime() - 1))
    expect(deriveTokenStatus(token({ expires_at: at.toISOString() }))).toBe('Active')
  })

  it('does not declare a token dead on an unreadable expiry', () => {
    // The UI has no basis to call it expired, and the API decides the real
    // outcome on the next request. Showing Inactive would tell an operator to
    // replace a credential that still works.
    expect(deriveTokenStatus(token({ expires_at: 'not-a-date' }))).toBe('Active')
    expect(deriveTokenStatus(token({ expires_at: '' }))).toBe('Active')
  })

  it('only ever returns values from the 11-status vocabulary', () => {
    const statuses = [null, 'not-a-date', new Date(Date.now() - 1).toISOString()].map((expires_at) =>
      deriveTokenStatus(token({ expires_at })),
    )
    expect(new Set(statuses)).toEqual(new Set(['Active', 'Inactive']))
  })
})

describe('formatBinding', () => {
  it('names the LibreChat identity once TOFU binding has happened', () => {
    expect(formatBinding(token({ librechat_user_id: 'lc-42' }))).toBe('lc-42')
  })

  it('reads as a normal state, not a fault, before binding', () => {
    expect(formatBinding(token({ librechat_user_id: null }))).toBe('Not bound yet')
    expect(formatBinding(token({ librechat_user_id: '   ' }))).toBe('Not bound yet')
  })
})

describe('timestamp rendering', () => {
  it('is the shared helper, so an unused token reads Never like an unused account', () => {
    expect(formatRelativeTime(token().last_used_at)).toBe('Never')
  })
})
