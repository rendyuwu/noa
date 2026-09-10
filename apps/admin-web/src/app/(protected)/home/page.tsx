'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

import { PageLoadingSkeleton } from '@/components/states'
import { useAuthUser } from '@/lib/auth/auth-context'

// The role-aware landing. Every "home" in this app — `/`, the post-login
// return-to default, the 403/404 escape, the shell logo — points here, and here
// is the only place that decides where an operator actually belongs.
//
// It is a CLIENT dispatcher on purpose. The decision needs a verified session,
// and `/` must stay a server redirect: `tests/framing-live.server.test.ts`
// asserts `/` answers 307, and that case exists to keep one redirect covered in
// the framing lane. Splitting it this way keeps that assertion true and avoids
// an unauthenticated `/auth/me` round trip.
//
// No auth hook here: the protected layout has already resolved `ready` and put
// the verified user in context. This page gates nothing — a non-admin reaching
// `/admin/users` is refused by that route's own `requireAdmin` check and by
// FastAPI RBAC, not by where this sends them.
const ADMIN_LANDING = '/admin/users'
const OPERATOR_LANDING = '/me/tokens'

export default function HomeRoute() {
  const router = useRouter()
  const user = useAuthUser()

  // Exactly the check `use-verified-auth.ts:69` makes — a plain, case-sensitive
  // `includes('admin')`. A looser test here would dispatch an operator to a page
  // whose own gate then refuses them, which is a 403 this page caused.
  const target = user.roles.includes('admin') ? ADMIN_LANDING : OPERATOR_LANDING

  useEffect(() => {
    router.replace(target)
  }, [router, target])

  // Never `null` — but deliberately NOT `LoadingView`, which is the pre-shell
  // state and owns a `main` of its own (states.tsx:56-67). This page renders
  // inside the AppShell, which already provides the page `main`, so LoadingView
  // here would open a second one: two `main` landmarks in one document. The
  // in-shell skeleton is the one that belongs at this depth. Pinned by
  // `page.test.tsx`, which asserts no `<main>` in this subtree.
  return <PageLoadingSkeleton />
}
