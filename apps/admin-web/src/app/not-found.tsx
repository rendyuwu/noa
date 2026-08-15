import { NotFoundView } from '@/components/states'

// Global 404 (issue #101). Unknown routes land here — outside the protected
// shell — and always offer a route home instead of dead-ending.
export default function NotFound() {
  return <NotFoundView />
}
