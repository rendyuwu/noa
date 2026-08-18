'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { AuditAdminPage } from '@/components/admin/audit/audit-admin-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/audit route (T55). The protected layout already verified an active
// session and mounted the AppShell; this route adds the admin gate. It re-checks
// `/auth/me` with requireAdmin, so a verified non-admin gets the 403 state
// instead of audit data — FastAPI RBAC stays authoritative either way, and both
// audit routes sit behind `require_admin` there.
//
// The one audit entry point. The ported `/admin/audit/tool-runs` deep link
// existed to preselect one of two tabs; with the action-requests tab gone (T55)
// it was a second address for the only view, so it went with it.
export default function AdminAuditRoute() {
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

  return <AuditAdminPage />
}
