'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { PageHeader } from '@gio/bigsu-app-shell'

import { TokensPanel } from '@/components/admin/tokens/tokens-panel'
import { fetchUsers } from '@/lib/admin/users/users-api'

// /admin/users/[userId]/tokens. The admin gate lives in the section layout, so
// a verified non-admin never reaches this render and never sees another
// operator's token list. FastAPI RBAC stays authoritative for the token routes
// either way.
//
// A client component, so the route segment is read with `useParams()`: the
// `params` prop is a Promise in Next 16 and this page needs the id during
// render, not after an await.
export default function AdminUserTokensRoute() {
  const params = useParams<{ userId: string }>()
  const rawUserId = params?.userId
  const userId = typeof rawUserId === 'string' ? rawUserId : ''

  const email = useUserEmail(userId)

  if (!userId) {
    return null
  }

  return (
    <>
      <PageHeader
        breadcrumb={[
          { label: 'Administration', href: '/admin' },
          { label: 'Users', href: '/admin/users' },
          { label: email ?? userId },
          { label: 'MCP tokens' },
        ]}
        title="MCP tokens"
        description={`MCP tokens issued to ${email ?? 'this account'}. Minting one here produces a credential that authenticates as that user; it is shown once and cannot be recovered.`}
      />

      <TokensPanel scope={{ kind: 'user', userId }} />
    </>
  )
}

// The email is a LABEL, not a gate. It comes from `fetchUsers()` rather than
// `fetchUsersAndRoles()` so this page does not pull `/admin/roles` it has no use
// for. When the lookup fails or the id is not in the list, the header falls back
// to the monospace id and says nothing it cannot support — the token request's
// own 404 `user_not_found` (mcp_tokens.py:150-155) is this page's authority on
// whether the user exists, not a list that may simply be stale.
function useUserEmail(userId: string): string | null {
  const [email, setEmail] = useState<string | null>(null)

  useEffect(() => {
    if (!userId) return
    let cancelled = false

    void fetchUsers().then(
      (users) => {
        if (!cancelled) setEmail(users.find((user) => user.id === userId)?.email ?? null)
      },
      () => {
        // Already reported by the transport where it is worth reporting. A
        // missing label is not a page failure.
        if (!cancelled) setEmail(null)
      },
    )

    return () => {
      cancelled = true
    }
  }, [userId])

  return email
}
