'use client'

import { registerIdentityCleaner } from '@/lib/auth/session'

// App-local cached identity (issue #99). This is PRESENTATION state only — the
// authoritative session is the HttpOnly `noa_session` cookie the browser never
// reads, and FastAPI `/auth/me` + RBAC stay the source of truth. Cached roles
// are never treated as authorization; they only keep AppShell chrome (name,
// email, role-filtered nav) painted between verified `/auth/me` revalidations.
//
// Storage is namespaced: everything on this origin shares one localStorage, so the
// `web-bigsu:` prefix must never collide with the legacy `noa.` keys.

const USER_KEY = 'web-bigsu:auth-user'

export type AuthUser = {
  id: string
  email: string
  display_name?: string | null
  roles?: string[]
}

const canUseStorage = (): boolean => typeof window !== 'undefined'

export const setStoredUser = (user: AuthUser | null): void => {
  if (!canUseStorage()) return
  try {
    if (user === null) {
      window.localStorage.removeItem(USER_KEY)
      return
    }
    window.localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    // Storage may be unavailable (private mode / quota). Identity is revalidated
    // from /auth/me regardless, so a failed write is non-fatal.
  }
}

export const getStoredUser = (): AuthUser | null => {
  if (!canUseStorage()) return null
  try {
    const raw = window.localStorage.getItem(USER_KEY)
    if (!raw) return null
    return JSON.parse(raw) as AuthUser
  } catch {
    return null
  }
}

export const clearStoredUser = (): void => {
  setStoredUser(null)
}

// Wire cached-identity teardown into the shared session-expiry / logout flow.
// session.ts owns the cookie clear + redirect + cross-tab broadcast (#100); #99
// owns the identity it must drop. A 401 or a cross-tab logout now also clears
// this cache, so stale local state can never outlive the session.
if (canUseStorage()) {
  registerIdentityCleaner(clearStoredUser)
}
