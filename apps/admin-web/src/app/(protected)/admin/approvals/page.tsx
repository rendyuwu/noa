'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { ApprovalsAdminPage } from '@/components/admin/audit/approvals-admin-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/approvals route (the admin API's contract). The protected layout already verified
// an active session and mounted the AppShell; this route adds the admin gate. It
// re-checks `/auth/me` with requireAdmin, so a verified non-admin gets the 403
// state instead of the trail — FastAPI RBAC stays authoritative either way, and
// all three action-request routes sit behind `require_admin` there.
//
// A page here is lawful because the admin API's contract names the routes behind it. The
// panel once shipped this view against addresses the API did not serve and
// 404'd on every load; it was deleted rather than stubbed, and the rule that
// deletion established is the one this page satisfies rather than sidesteps.
export default function AdminApprovalsRoute() {
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

  return <ApprovalsAdminPage />
}
