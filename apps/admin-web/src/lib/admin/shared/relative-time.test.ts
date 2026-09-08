import { describe, expect, it } from 'vitest'

import { formatRelativeTime } from './relative-time'

// Moved with the implementation out of `users/user-status.test.ts` (§T76). The
// two original cases are kept verbatim so the move demonstrably preserves
// coverage rather than quietly re-specifying it; the rest pin the boundaries the
// token tables now depend on.

describe('formatRelativeTime', () => {
  it('returns Never for missing or invalid values', () => {
    expect(formatRelativeTime(null)).toBe('Never')
    expect(formatRelativeTime('not-a-date')).toBe('Never')
  })

  it('describes a recent timestamp in relative terms', () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
    expect(formatRelativeTime(twoHoursAgo)).toBe('2 hours ago')
  })

  it('reads Never — capital, never an em dash — for every absent form', () => {
    // The token tables show `last_used_at: null` for a token nobody has used
    // yet, and it has to say the same word the Users table says.
    expect(formatRelativeTime(undefined)).toBe('Never')
    expect(formatRelativeTime('')).toBe('Never')
    expect(formatRelativeTime(0)).toBe('Never')
    expect(formatRelativeTime({})).toBe('Never')
  })

  it('treats sub-minute and future timestamps as Just now', () => {
    expect(formatRelativeTime(new Date(Date.now() - 5_000).toISOString())).toBe('Just now')
    expect(formatRelativeTime(new Date(Date.now() + 60_000).toISOString())).toBe('Just now')
  })

  it('singularises the one-unit cases', () => {
    expect(formatRelativeTime(new Date(Date.now() - 60_000).toISOString())).toBe('1 minute ago')
    expect(formatRelativeTime(new Date(Date.now() - 3_600_000).toISOString())).toBe('1 hour ago')
    expect(formatRelativeTime(new Date(Date.now() - 86_400_000).toISOString())).toBe('1 day ago')
  })

  it('falls back to an absolute date once past a week', () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 86_400_000)
    expect(formatRelativeTime(eightDaysAgo.toISOString())).toBe(
      eightDaysAgo.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
    )
  })
})
