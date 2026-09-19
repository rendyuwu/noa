'use client'

import { DEFAULT_RETURN_TO, sanitizeReturnTo } from '@/lib/auth/return-to'

// Session-expiry signaling (issue #100 foundation). Owns the "the session is
// gone" transport signal: the sentinel error transport helpers throw, the
// idempotent clear-and-redirect flow, and cross-tab broadcast.
//
// There is no app-local identity to tear down: the only client-side identity
// state is the `/auth/me` verdict held in React state by `use-verified-auth`,
// which the redirect below unmounts. This file used to carry an identity-cleaner
// registry for a `localStorage` cache no code ever read (issue #10). If app-local
// identity is ever persisted again, its teardown belongs in `clearAuth` and in
// the cross-tab listener — both paths, or a logged-out tab keeps it.

export type ClearAuthReason = 'session_expired' | 'logged_out'

const LOGOUT_CHANNEL = 'noa:auth'
const LOGIN_PATH = '/login'

// Thrown by transport helpers when a 401 has triggered an auth redirect, so
// callers can distinguish an in-flight redirect from a real failure.
export class AuthRedirectError extends Error {
  constructor(reason: ClearAuthReason = 'session_expired') {
    super(`Auth redirect in progress (${reason})`)
    this.name = 'AuthRedirectError'
  }
}

export const isAuthRedirectError = (error: unknown): error is AuthRedirectError => {
  return error instanceof AuthRedirectError
}

let clearAuthInProgress = false

export const isClearAuthInProgress = (): boolean => clearAuthInProgress

/** Reset the idempotency guard — test-only. */
export const _resetForTesting = (): void => {
  clearAuthInProgress = false
}

/** Manually wire up the cross-tab listener — test-only. */
export const _initLogoutListenerForTesting = (): void => {
  initLogoutListener()
}

function getSafeReturnTo(): string {
  if (typeof window === 'undefined') return DEFAULT_RETURN_TO
  const raw = `${window.location.pathname}${window.location.search}${window.location.hash}`
  return sanitizeReturnTo(raw)
}

function broadcastLogout(reason?: ClearAuthReason): void {
  try {
    if (typeof BroadcastChannel === 'undefined') return
    const ch = new BroadcastChannel(LOGOUT_CHANNEL)
    ch.postMessage({ type: 'noa:logout', reason })
    ch.close()
  } catch {
    // BroadcastChannel may not be available in all environments.
  }
}

// Idempotent session teardown: clear the server cookie, notify other tabs, and
// redirect to the login page (preserving a safe returnTo). One redirect per page
// lifecycle.
export const clearAuth = (reason?: ClearAuthReason): void => {
  if (typeof window === 'undefined') return
  if (clearAuthInProgress) return
  clearAuthInProgress = true

  // Fire-and-forget cookie clear via the same-origin proxy.
  fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {
    // Best-effort — the cookie expires naturally if this fails.
  })

  broadcastLogout(reason)

  const params = new URLSearchParams()
  if (reason) params.set('reason', reason)
  const returnTo = getSafeReturnTo()
  if (returnTo !== DEFAULT_RETURN_TO) params.set('returnTo', returnTo)

  const qs = params.toString()
  window.location.href = qs ? `${LOGIN_PATH}?${qs}` : LOGIN_PATH
}

function initLogoutListener(): void {
  try {
    if (typeof BroadcastChannel === 'undefined') return
    const ch = new BroadcastChannel(LOGOUT_CHANNEL)
    ch.onmessage = (event: MessageEvent) => {
      const data = event.data as { type?: string } | undefined
      if (data?.type !== 'noa:logout') return
      // Ignore our own broadcast echo: the initiating tab already redirected via
      // clearAuth with the correct reason/returnTo, so reacting here would clobber
      // it with a bare /login. Only other tabs (not mid-clear) react.
      if (clearAuthInProgress) return
      if (typeof window !== 'undefined') {
        window.location.href = LOGIN_PATH
      }
    }
  } catch {
    // BroadcastChannel may not be available.
  }
}

// Auto-wire the cross-tab listener in the browser.
if (typeof window !== 'undefined') {
  initLogoutListener()
}
