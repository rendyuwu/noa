'use client'

import { UsersPage } from '@/components/admin/users/users-page'
import { useAuthUser } from '@/lib/auth/auth-context'

// /admin/users (issue #106). The admin gate lives in the section layout. This
// route is the one that needs the verified identity in the page — `me` drives
// the self-action guards — and a layout cannot hand props to a page, so it
// reads the same verdict from the context the protected layout already mounted
// rather than re-verifying for itself.
export default function AdminUsersRoute() {
  return <UsersPage me={useAuthUser()} />
}
