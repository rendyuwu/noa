import { describe, expect, it } from 'vitest'

import { DEFAULT_RETURN_TO, sanitizeReturnTo } from './return-to'

// Asserted through `sanitizeReturnTo`, the only exported entry point: the guard
// behind it is not exported, and a callable nothing calls is a surface that can
// drift away from the one the app actually uses. "Safe" reads as the path coming
// back unchanged; "rejected" reads as the default landing coming back instead.
// No case below is `DEFAULT_RETURN_TO` itself, so the two outcomes stay distinct.

describe('sanitizeReturnTo', () => {
  it('accepts origin-relative paths with query and hash', () => {
    expect(sanitizeReturnTo('/assistant')).toBe('/assistant')
    expect(sanitizeReturnTo('/assistant/threads/9?tab=x#frag')).toBe(
      '/assistant/threads/9?tab=x#frag',
    )
    expect(sanitizeReturnTo('/admin')).toBe('/admin')
  })

  it('rejects external / absolute URLs', () => {
    expect(sanitizeReturnTo('https://evil.com')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('http://evil.com/assistant')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('mailto:x@y.z')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('javascript:alert(1)')).toBe(DEFAULT_RETURN_TO)
  })

  it('rejects protocol-relative and backslash-smuggled targets', () => {
    expect(sanitizeReturnTo('//evil.com')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/\\evil.com')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/\tevil')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/foo\nbar')).toBe(DEFAULT_RETURN_TO)
  })

  it('rejects percent-encoded path separators', () => {
    // "/api%2Fsecrets" slips past the literal "/api/" guard and decodes to the
    // proxy surface after navigation.
    expect(sanitizeReturnTo('/api%2Fsecrets')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/x%5Cevil.com')).toBe(DEFAULT_RETURN_TO)
  })

  it('rejects login-loop targets', () => {
    expect(sanitizeReturnTo('/login')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/login?returnTo=/x')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/login/sub')).toBe(DEFAULT_RETURN_TO)
  })

  it('rejects callback-loop / API-surface targets', () => {
    expect(sanitizeReturnTo('/api')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/api/auth/login')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/api/auth/me')).toBe(DEFAULT_RETURN_TO)
    // The query/fragment forms are the same proxy surface (raw backend JSON),
    // and slipped through when only '/api' and '/api/' were guarded.
    expect(sanitizeReturnTo('/api?x=1')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/api#frag')).toBe(DEFAULT_RETURN_TO)
  })

  it('allows a distinct path segment that merely starts with "api"', () => {
    // Guard boundary: "/apifoo" is a different route, not the "/api" surface.
    expect(sanitizeReturnTo('/apifoo')).toBe('/apifoo')
  })

  it('rejects empty and non-string input', () => {
    expect(sanitizeReturnTo('')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo(null)).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo(undefined)).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo(42)).toBe(DEFAULT_RETURN_TO)
  })

  it('honors a custom fallback', () => {
    expect(sanitizeReturnTo('https://evil.com', '/home')).toBe('/home')
  })
})
