import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('auth-store', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('stores, reads, and clears the cached user', async () => {
    const store = await import('./auth-store')
    expect(store.getStoredUser()).toBeNull()

    store.setStoredUser({ id: '1', email: 'op@biznetgio.com', roles: ['admin'] })
    expect(store.getStoredUser()).toEqual({
      id: '1',
      email: 'op@biznetgio.com',
      roles: ['admin'],
    })

    store.clearStoredUser()
    expect(store.getStoredUser()).toBeNull()
  })

  it('namespaces storage under web-bigsu: and never touches legacy noa. keys', async () => {
    const store = await import('./auth-store')
    window.localStorage.setItem('noa.user', 'legacy')

    store.setStoredUser({ id: '1', email: 'op@biznetgio.com' })
    expect(window.localStorage.getItem('web-bigsu:auth-user')).not.toBeNull()
    expect(window.localStorage.getItem('noa.user')).toBe('legacy')

    store.clearStoredUser()
    expect(window.localStorage.getItem('noa.user')).toBe('legacy')
  })

  it('returns null for corrupt JSON rather than throwing', async () => {
    const store = await import('./auth-store')
    window.localStorage.setItem('web-bigsu:auth-user', '{not json')
    expect(store.getStoredUser()).toBeNull()
  })

  it('is torn down by the shared session clear flow (401 / logout)', async () => {
    vi.resetModules()
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...originalLocation, href: 'http://localhost/assistant' },
    })
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))

    const store = await import('./auth-store') // registers the identity cleaner
    const session = await import('./session')
    store.setStoredUser({ id: '1', email: 'op@biznetgio.com', roles: [] })
    expect(store.getStoredUser()).not.toBeNull()

    session._resetForTesting()
    session.clearAuth('session_expired')

    expect(store.getStoredUser()).toBeNull()

    Object.defineProperty(window, 'location', { writable: true, value: originalLocation })
  })
})
