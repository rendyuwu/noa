'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { AuditAdminPage } from '@/components/admin/audit/audit-admin-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/audit/tool-runs route (issue #111). Same admin gate as /admin/audit;
// this deep-link entry point defaults to the tool-runs tab so a shared link
// lands on the right view. The tab is otherwise switchable client-side (with URL
// pushState), and FastAPI RBAC stays authoritative for access.
export default function AdminAuditToolRunsRoute() {
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

  return <AuditAdminPage initialTab="tool-runs" />
}
