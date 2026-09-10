'use client'

import { useEffect, useState } from 'react'

import { setStoredUser } from '@/lib/auth/auth-store'
import { ApiError, fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'
import { isAuthRedirectError } from '@/lib/auth/session'
import { reportClientError } from '@/lib/observability/error-reporting'

// Verified-auth guard (issue #99). Every protected route revalidates `/auth/me`
// through the same-origin proxy before it renders protected data — the cached
// user (auth-store) is presentation only and is never trusted for authorization.
// FastAPI stays the source of truth; this hook only reflects its verdict.

export type VerifiedUser = {
  id: string
  email: string
  display_name?: string | null
  is_active: boolean
  roles: string[]
}

type MeResponse = { user: VerifiedUser }

// - loading:   revalidating, or a 401 redirect is in flight
// - ready:     active, authorized user; protected data may render
// - pending:   authenticated but inactive/awaiting approval (403) — distinct
// - forbidden: verified, active, but not an admin on an admin-only route
// - error:     unexpected transport/upstream failure
export type VerifiedAuthStatus = 'loading' | 'ready' | 'pending' | 'forbidden' | 'error'

export type VerifiedAuthState = {
  status: VerifiedAuthStatus
  user: VerifiedUser | null
  isAdmin: boolean
}

type UseVerifiedAuthOptions = {
  /** Resolve verified non-admins to `forbidden` instead of `ready`. Default: false. */
  requireAdmin?: boolean
}

const PENDING_APPROVAL_CODE = 'user_pending_approval'

export function useVerifiedAuth(options: UseVerifiedAuthOptions = {}): VerifiedAuthState {
  const { requireAdmin = false } = options
  const [state, setState] = useState<VerifiedAuthState>({
    status: 'loading',
    user: null,
    isAdmin: false,
  })

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    void (async () => {
      try {
        const response = await fetchWithAuth('/auth/me', { signal: controller.signal })
        const data = await jsonOrThrow<MeResponse>(response)
        if (cancelled) return

        const user = data.user
        // `/auth/me` is the source of truth, but guard the shape: a response
        // that omits `roles` must not throw on `.includes` — that would be
        // caught below and lock an authenticated user out of every route.
        // Absent/malformed roles means an authenticated non-admin, not an error.
        const roles = Array.isArray(user.roles) ? user.roles : []
        const isAdmin = roles.includes('admin')
        const verifiedUser: VerifiedUser = { ...user, roles }

        // Refresh the presentation cache so AppShell chrome stays current.
        setStoredUser({
          id: verifiedUser.id,
          email: verifiedUser.email,
          display_name: verifiedUser.display_name ?? undefined,
          roles,
        })

        // Role-denied is distinct from pending/expired. The old repo redirected
        // here, to a chat landing every signed-in user could see. This app has no
        // such page — administration is all of it, per the admin panel's contract — so a redirect
        // would send a non-admin to another admin-only route and loop. It
        // resolves to a state the page renders instead. FastAPI RBAC is still
        // authoritative; this only explains the refusal.
        if (requireAdmin && !isAdmin) {
          setState({ status: 'forbidden', user: verifiedUser, isAdmin })
          return
        }

        setState({ status: 'ready', user: verifiedUser, isAdmin })
      } catch (error) {
        if (cancelled || controller.signal.aborted) return

        // 401 → fetchWithAuth already cleared identity and started the redirect.
        if (isAuthRedirectError(error)) return

        // 403 pending/inactive: cookie is valid but the account is not approved.
        // A distinct product state, not an expiry and not an error.
        if (error instanceof ApiError && error.errorCode === PENDING_APPROVAL_CODE) {
          setState({ status: 'pending', user: null, isAdmin: false })
          return
        }

        // Unexpected failure. The sink drops handled/canceled noise; `/auth/me`
        // carries no password, token, or cookie, so nothing secret is reported.
        reportClientError(error)
        setState({ status: 'error', user: null, isAdmin: false })
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [requireAdmin])

  return state
}
