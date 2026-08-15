'use client'

import { AppFrame } from '@/components/app-frame'
import { LoadingView, PendingApprovalView, UnexpectedErrorView } from '@/components/states'
import { AuthUserProvider } from '@/lib/auth/auth-context'
import { clearAuth } from '@/lib/auth/session'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// Protected root layout (issue #101). Mounts the BIGSU AppShell exactly once for
// every protected route and owns the single `/auth/me` revalidation gate:
// children render only for a verified, active user. AppShell persists its own
// collapse state, so navigating between pages never resets the chrome. The gate
// is presentation routing — FastAPI RBAC stays the authorization source of
// truth. Loading / pending-approval / unexpected-error render before the shell
// exists (they own their own `main`); none of them dead-ends the user.
export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const { status, user } = useVerifiedAuth()

  if (status === 'loading') {
    return <LoadingView label="Verifying session" />
  }

  if (status === 'pending') {
    return <PendingApprovalView onSignOut={() => clearAuth('logged_out')} />
  }

  if (status === 'error' || !user) {
    return <UnexpectedErrorView onRetry={() => window.location.reload()} />
  }

  return (
    <AuthUserProvider user={user}>
      <AppFrame user={user}>{children}</AppFrame>
    </AuthUserProvider>
  )
}
