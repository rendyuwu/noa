import { describe, expect, it } from 'vitest'

import { DEFAULT_RETURN_TO, isSafeReturnTo, sanitizeReturnTo } from './return-to'

describe('isSafeReturnTo', () => {
  it('accepts origin-relative paths with query and hash', () => {
    expect(isSafeReturnTo('/assistant')).toBe(true)
    expect(isSafeReturnTo('/assistant/threads/9?tab=x#frag')).toBe(true)
    expect(isSafeReturnTo('/admin')).toBe(true)
  })

  it('rejects external / absolute URLs', () => {
    expect(isSafeReturnTo('https://evil.com')).toBe(false)
    expect(isSafeReturnTo('http://evil.com/assistant')).toBe(false)
    expect(isSafeReturnTo('mailto:x@y.z')).toBe(false)
    expect(isSafeReturnTo('javascript:alert(1)')).toBe(false)
  })

  it('rejects protocol-relative and backslash-smuggled targets', () => {
    expect(isSafeReturnTo('//evil.com')).toBe(false)
    expect(isSafeReturnTo('/\\evil.com')).toBe(false)
    expect(isSafeReturnTo('/\tevil')).toBe(false)
    expect(isSafeReturnTo('/foo\nbar')).toBe(false)
  })

  it('rejects login-loop targets', () => {
    expect(isSafeReturnTo('/login')).toBe(false)
    expect(isSafeReturnTo('/login?returnTo=/x')).toBe(false)
    expect(isSafeReturnTo('/login/sub')).toBe(false)
  })

  it('rejects callback-loop / API-surface targets', () => {
    expect(isSafeReturnTo('/api')).toBe(false)
    expect(isSafeReturnTo('/api/auth/login')).toBe(false)
    expect(isSafeReturnTo('/api/auth/me')).toBe(false)
    // The query/fragment forms are the same proxy surface (raw backend JSON),
    // and slipped through when only '/api' and '/api/' were guarded.
    expect(isSafeReturnTo('/api?x=1')).toBe(false)
    expect(isSafeReturnTo('/api#frag')).toBe(false)
  })

  it('allows a distinct path segment that merely starts with "api"', () => {
    // Guard boundary: "/apifoo" is a different route, not the "/api" surface.
    expect(isSafeReturnTo('/apifoo')).toBe(true)
  })

  it('rejects empty and non-string input', () => {
    expect(isSafeReturnTo('')).toBe(false)
    expect(isSafeReturnTo(null)).toBe(false)
    expect(isSafeReturnTo(undefined)).toBe(false)
    expect(isSafeReturnTo(42)).toBe(false)
  })
})

describe('sanitizeReturnTo', () => {
  it('returns the path when safe', () => {
    expect(sanitizeReturnTo('/assistant/threads/1')).toBe('/assistant/threads/1')
  })

  it('falls back to the default landing when unsafe', () => {
    expect(sanitizeReturnTo('//evil.com')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo('/login')).toBe(DEFAULT_RETURN_TO)
    expect(sanitizeReturnTo(null)).toBe(DEFAULT_RETURN_TO)
  })

  it('honors a custom fallback', () => {
    expect(sanitizeReturnTo('https://evil.com', '/home')).toBe('/home')
  })
})
