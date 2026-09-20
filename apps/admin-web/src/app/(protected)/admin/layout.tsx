'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// The admin gate, once for the whole section. Every route under /admin carried
// a byte-identical copy of this; a layout is where "the admin section requires
// an admin" belongs, and the eight pages under it are now what they always
// described themselves as — the view, and nothing else.
//
// It re-checks `/auth/me` with requireAdmin, so a verified non-admin gets the
// 403 state instead of admin data. Role-denied resolves to a state and not a
// redirect: every route in this section is admin-only, so a redirect would only
// land on another one, and rendering nothing would leave the operator inside
// the shell with no explanation for the empty page.
//
// A layout persists across navigation between sibling routes, so the admin
// revalidation happens once on entering /admin rather than once per page inside
// it. That is the contract the protected layout already sets for the whole tree
// — one revalidation, shared through AuthUserProvider — and it is safe for the
// same reason: this gate is presentation routing, FastAPI RBAC is the
// authorization source of truth, and nothing reachable here is served without
// the server's own check.
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { status, user } = useVerifiedAuth({ requireAdmin: true })

  if (status === 'loading') {
    return <PageLoadingSkeleton />
  }
  if (status === 'forbidden') {
    return <ForbiddenView />
  }
  if (status !== 'ready' || !user) {
    return null
  }

  return <>{children}</>
}
