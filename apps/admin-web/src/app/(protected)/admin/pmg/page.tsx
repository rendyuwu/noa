'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { PmgServersPage } from '@/components/admin/pmg/pmg-servers-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/pmg route (issue #110). The protected layout already verified an active
// session and mounted the AppShell; this route adds the admin gate. It re-checks
// `/auth/me` with requireAdmin, so a verified non-admin gets the 403 state
// instead of admin data — FastAPI RBAC stays authoritative.
export default function AdminPmgRoute() {
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

  return <PmgServersPage />
}
