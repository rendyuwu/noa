'use client'

import { use } from 'react'

import { ForbiddenView, PageLoadingSkeleton } from '@/components/states'
import { AuditReceiptPage } from '@/components/admin/audit/audit-receipt-page'
import { useVerifiedAuth } from '@/lib/auth/use-verified-auth'

// /admin/audit/receipts/[actionRequestId] route (issue #111). The standalone,
// export-friendly receipt for one action request. Same admin gate as the audit
// list; FastAPI RBAC stays authoritative, and the receipt payload is redacted
// server-side before it reaches this client.
export default function AdminAuditReceiptRoute({
  params,
}: {
  params: Promise<{ actionRequestId: string }>
}) {
  const { actionRequestId } = use(params)
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

  return <AuditReceiptPage actionRequestId={actionRequestId} />
}
