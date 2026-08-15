'use client'

import { createContext, useContext, type ReactNode } from 'react'

import type { VerifiedUser } from '@/lib/auth/use-verified-auth'

// Verified-identity context (issue #101). The protected layout runs the single
// `/auth/me` revalidation and shares the verdict here, so descendant pages read
// the same verified user the AppShell chrome renders — without re-fetching. This
// is presentation state only; FastAPI RBAC stays the authorization source.
const AuthUserContext = createContext<VerifiedUser | null>(null)

export function AuthUserProvider({
  user,
  children,
}: {
  user: VerifiedUser
  children: ReactNode
}) {
  return <AuthUserContext.Provider value={user}>{children}</AuthUserContext.Provider>
}

export function useAuthUser(): VerifiedUser {
  const user = useContext(AuthUserContext)
  if (user === null) {
    throw new Error('useAuthUser must be used within the protected AuthUserProvider')
  }
  return user
}
