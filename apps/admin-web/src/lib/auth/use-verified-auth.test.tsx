import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

const nav = vi.hoisted(() => {
  const replace = vi.fn()
  const push = vi.fn()
  // A STABLE router object: returning a fresh object each render would change
  // the effect dependency and re-run the /auth/me fetch on every render.
  return { replace, push, router: { replace, push, refresh: () => {} } }
})
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
}))

// Fresh Response per call — a Response body can only be read once, so effects
// that re-fetch must not share one instance.
const fetchReturning = (status: number, body: unknown) =>
  vi.fn(() => Promise.resolve(jsonResponse(status, body)))

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })

const meBody = (over: Record<string, unknown> = {}) => ({
  user: {
    id: '1',
    email: 'op@biznetgio.com',
    display_name: 'Operator',
    is_active: true,
    roles: ['user'],
    ...over,
  },
})

describe('useVerifiedAuth', () => {
  beforeEach(() => {
    vi.resetModules()
    window.localStorage.clear()
    nav.replace.mockClear()
    nav.push.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('revalidates /auth/me and becomes ready for an active user', async () => {
    globalThis.fetch = fetchReturning(200, meBody({ roles: ['admin'] }))
    const { useVerifiedAuth } = await import('./use-verified-auth')
    const store = await import('./auth-store')

    const { result } = renderHook(() => useVerifiedAuth())

    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.isAdmin).toBe(true)
    expect(result.current.user?.email).toBe('op@biznetgio.com')
    // Caches verified identity for AppShell presentation.
    expect(store.getStoredUser()?.roles).toContain('admin')
    // Talks to the same-origin proxy, credentials included.
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/auth/me',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('treats a /auth/me user without roles as an authenticated non-admin, not an error', async () => {
    // A response that omits `roles` must not throw on `.includes` — that would
    // be caught and reported as status 'error', locking the user out of every
    // protected route. Absent roles means authenticated non-admin.
    globalThis.fetch = fetchReturning(200, { user: { id: '1', email: 'op@biznetgio.com', is_active: true } })
    const { useVerifiedAuth } = await import('./use-verified-auth')

    const { result } = renderHook(() => useVerifiedAuth())

    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.isAdmin).toBe(false)
    expect(result.current.user?.roles).toEqual([])
  })

  it('surfaces pending/inactive as a distinct state on 403', async () => {
    globalThis.fetch = fetchReturning(403, {
      detail: 'User pending approval',
      error_code: 'user_pending_approval',
    })
    const { useVerifiedAuth } = await import('./use-verified-auth')

    const { result } = renderHook(() => useVerifiedAuth())

    await waitFor(() => expect(result.current.status).toBe('pending'))
    expect(result.current.user).toBeNull()
  })

  it('clears cached identity and redirects on 401 (no stale local state)', async () => {
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...originalLocation, href: 'http://localhost/assistant' },
    })
    globalThis.fetch = fetchReturning(401, {
      detail: 'Missing authentication',
      error_code: 'missing_authentication',
    })

    const store = await import('./auth-store')
    store.setStoredUser({ id: 'stale', email: 'stale@biznetgio.com', roles: ['admin'] })
    const { useVerifiedAuth } = await import('./use-verified-auth')

    renderHook(() => useVerifiedAuth())

    await waitFor(() => expect(store.getStoredUser()).toBeNull())
    expect(window.location.href).toContain('/login')

    Object.defineProperty(window, 'location', { writable: true, value: originalLocation })
  })

  it('resolves a verified non-admin on an admin-only route to forbidden (role-denied)', async () => {
    globalThis.fetch = fetchReturning(200, meBody({ roles: ['user'] }))
    const { useVerifiedAuth } = await import('./use-verified-auth')

    const { result } = renderHook(() => useVerifiedAuth({ requireAdmin: true }))

    await waitFor(() => expect(result.current.status).toBe('forbidden'))
    // Role-denied is distinct from pending and from ready: the session is good,
    // the account is active, and the answer is still no.
    expect(result.current.isAdmin).toBe(false)
    expect(result.current.user?.email).toBe('op@biznetgio.com')
  })

  it('never navigates a role-denied user — every route here is admin-only', async () => {
    // The old repo redirected to a chat landing any signed-in user could open.
    // This app has none, so a redirect would land on another admin-only route
    // and loop. Asserting on the router keeps that from coming back as a
    // one-line "restore the redirect" change.
    globalThis.fetch = fetchReturning(200, meBody({ roles: ['user'] }))
    const { useVerifiedAuth } = await import('./use-verified-auth')

    const { result } = renderHook(() => useVerifiedAuth({ requireAdmin: true }))

    await waitFor(() => expect(result.current.status).toBe('forbidden'))
    expect(nav.replace).not.toHaveBeenCalled()
    expect(nav.push).not.toHaveBeenCalled()
  })

  it('surfaces an unexpected upstream failure as an error state', async () => {
    globalThis.fetch = fetchReturning(500, { detail: 'boom' })
    const { useVerifiedAuth } = await import('./use-verified-auth')

    const { result } = renderHook(() => useVerifiedAuth())

    await waitFor(() => expect(result.current.status).toBe('error'))
  })
})
