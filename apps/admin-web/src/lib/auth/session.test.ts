import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('AuthRedirectError', () => {
  it('is identifiable via isAuthRedirectError', async () => {
    const mod = await import('./session')
    const err = new mod.AuthRedirectError('session_expired')
    expect(mod.isAuthRedirectError(err)).toBe(true)
    expect(mod.isAuthRedirectError(new Error('other'))).toBe(false)
    expect(err.name).toBe('AuthRedirectError')
  })
})

describe('clearAuth', () => {
  let originalLocation: Location

  beforeEach(() => {
    vi.resetModules()
    originalLocation = window.location
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', { writable: true, value: originalLocation })
    if ('BroadcastChannel' in globalThis) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (globalThis as any).BroadcastChannel
    }
    vi.restoreAllMocks()
  })

  const stubLocation = () => {
    const spy = { ...originalLocation, href: 'http://localhost/assistant/threads/9' }
    Object.defineProperty(window, 'location', { writable: true, value: spy })
    return spy
  }

  it('fires a logout POST and redirects to /login with the reason', async () => {
    const location = stubLocation()
    const mod = await import('./session')
    mod._resetForTesting()
    mod.clearAuth('session_expired')

    expect(globalThis.fetch).toHaveBeenCalledWith('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
    })
    expect(location.href).toContain('/login')
    expect(location.href).toContain('reason=session_expired')
    expect(location.href).toContain('returnTo=')
  })

  it('is idempotent — a second call is a no-op', async () => {
    stubLocation()
    const mod = await import('./session')
    mod._resetForTesting()
    mod.clearAuth('logged_out')
    mod.clearAuth('logged_out')
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    expect(mod.isClearAuthInProgress()).toBe(true)
  })

  it('broadcasts a logout message to other tabs', async () => {
    stubLocation()
    const postMessage = vi.fn()
    const close = vi.fn()
    class MockChannel {
      postMessage = postMessage
      close = close
      onmessage: ((event: MessageEvent) => void) | null = null
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(globalThis as any).BroadcastChannel = MockChannel

    const mod = await import('./session')
    mod._resetForTesting()
    mod.clearAuth('logged_out')

    expect(postMessage).toHaveBeenCalledWith({ type: 'noa:logout', reason: 'logged_out' })
    expect(close).toHaveBeenCalled()
  })
})

describe('cross-tab logout listener', () => {
  let originalLocation: Location

  beforeEach(() => {
    vi.resetModules()
    originalLocation = window.location
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', { writable: true, value: originalLocation })
    if ('BroadcastChannel' in globalThis) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (globalThis as any).BroadcastChannel
    }
  })

  it('redirects to /login when it receives a logout broadcast', async () => {
    const location = { ...originalLocation, href: 'http://localhost/assistant' }
    Object.defineProperty(window, 'location', { writable: true, value: location })

    let captured: ((event: MessageEvent) => void) | null = null
    class MockChannel {
      postMessage = vi.fn()
      close = vi.fn()
      set onmessage(handler: ((event: MessageEvent) => void) | null) {
        captured = handler
      }
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(globalThis as any).BroadcastChannel = MockChannel

    const mod = await import('./session')
    mod._initLogoutListenerForTesting()

    expect(captured).not.toBeNull()
    captured!({ data: { type: 'noa:logout' } } as MessageEvent)
    expect(location.href).toContain('/login')
  })

  it('ignores its own broadcast echo while a clear is already in progress', async () => {
    const location = { ...originalLocation, href: 'http://localhost/assistant/threads/9' }
    Object.defineProperty(window, 'location', { writable: true, value: location })
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))

    let captured: ((event: MessageEvent) => void) | null = null
    class MockChannel {
      postMessage = vi.fn()
      close = vi.fn()
      set onmessage(handler: ((event: MessageEvent) => void) | null) {
        captured = handler
      }
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(globalThis as any).BroadcastChannel = MockChannel

    const mod = await import('./session')
    mod._resetForTesting()
    mod._initLogoutListenerForTesting()
    mod.clearAuth('session_expired')

    const afterClear = location.href
    expect(afterClear).toContain('reason=session_expired')

    // The echo of our own broadcast must not clobber the reason-bearing redirect
    // with a bare /login.
    captured!({ data: { type: 'noa:logout' } } as MessageEvent)
    expect(location.href).toBe(afterClear)
  })
})
