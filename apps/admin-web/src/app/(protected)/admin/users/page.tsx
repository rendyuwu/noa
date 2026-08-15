'use client'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { UsersPage } from '@/components/admin/users/users-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/users route (issue #106). The protected layout already verified an
// active session and mounted the AppShell; this route adds the admin gate. It
// re-checks `/auth/me` with requireAdmin, so a verified non-admin gets the 403
// state instead of admin data — role visibility refreshes
// from the server and FastAPI RBAC stays authoritative. The verified user is
// handed to the page as `me` for the self-action guards.
export default function AdminUsersRoute() {
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

  return <UsersPage me={user} />
}
