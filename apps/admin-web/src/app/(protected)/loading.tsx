import { PageLoadingSkeleton } from '@/components/states'

// Route-segment loading fallback for protected pages (issue #101). Rendered
// inside the already-mounted AppShell, so it uses the chrome-less skeleton.
export default function ProtectedLoading() {
  return <PageLoadingSkeleton />
}
