'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { ProxmoxServersPage } from '@/components/admin/proxmox/proxmox-servers-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/proxmox route (issue #109). The protected layout already verified an
// active session and mounted the AppShell; this route adds the admin gate. It
// re-checks `/auth/me` with requireAdmin, so a verified non-admin gets the 403
// state instead of admin data — FastAPI RBAC stays
// authoritative.
export default function AdminProxmoxRoute() {
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

  return <ProxmoxServersPage />
}
