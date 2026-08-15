'use client'

import { useCallback, useRef, useState } from 'react'

import { toMessage } from '@/lib/admin/shared/error-message'
import { isAuthRedirectError } from '@/lib/auth/session'

// Lazy detail loader for the audit drawers (issue #111). A detail drawer opens
// on demand and fetches the full record — including its already-redacted
// arguments — only then, caching per id so re-opening never re-fetches. A load
// carries the id it was dispatched for, so a slow response for a record the user
// moved away from never surfaces against a drawer that moved on. Audit detail is
// read-only; there is no mutation path here. Generic over the detail type so the
// action-request and tool-run drawers share one controller.

export type AuditDetailController<TDetail> = {
  detailsById: Record<string, TDetail>
  loadingId: string | null
  errorId: string | null
  error: string | null
  load: (id: string) => void
}

export function useAuditDetail<TDetail>(
  fetchDetail: (id: string) => Promise<TDetail>,
  errorFallback: string,
): AuditDetailController<TDetail> {
  const [detailsById, setDetailsById] = useState<Record<string, TDetail>>({})
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const [errorId, setErrorId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef<Set<string>>(new Set())

  const load = useCallback(
    (id: string) => {
      if (detailsById[id] || inFlight.current.has(id)) return
      inFlight.current.add(id)
      setLoadingId(id)
      setErrorId(null)
      setError(null)
      void (async () => {
        try {
          const detail = await fetchDetail(id)
          setDetailsById((prev) => ({ ...prev, [id]: detail }))
        } catch (err) {
          if (isAuthRedirectError(err)) return
          setErrorId(id)
          setError(toMessage(err, errorFallback))
        } finally {
          inFlight.current.delete(id)
          setLoadingId((prev) => (prev === id ? null : prev))
        }
      })()
    },
    [detailsById, errorFallback, fetchDetail],
  )

  return { detailsById, loadingId, errorId, error, load }
}
