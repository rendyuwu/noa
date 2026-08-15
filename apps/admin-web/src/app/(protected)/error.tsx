'use client'

import { useEffect } from 'react'

import { UnexpectedErrorView } from '@/components/states'
import { reportClientError } from '@/lib/observability/error-reporting'

// Protected-segment error boundary (issue #101). Any unexpected render/data
// failure in a protected route surfaces the shared recovery state — never a dead
// end — with Next's error `digest` shown as the copyable incident ID and `reset`
// wired to retry. The failure is also routed to the client error sink.
export default function ProtectedError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    reportClientError(error)
  }, [error])

  return <UnexpectedErrorView incidentId={error.digest} onRetry={reset} />
}
