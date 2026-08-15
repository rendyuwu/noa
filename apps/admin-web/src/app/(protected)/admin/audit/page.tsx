'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { AuditAdminPage } from '@/components/admin/audit/audit-admin-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/audit route (issue #111). The protected layout already verified an
// active session and mounted the AppShell; this route adds the admin gate. It
// re-checks `/auth/me` with requireAdmin, so a verified non-admin gets the 403
// state instead of audit data — FastAPI RBAC stays
// authoritative. This entry point defaults to the action-requests tab.
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

  return <AuditAdminPage initialTab="action-requests" />
}
