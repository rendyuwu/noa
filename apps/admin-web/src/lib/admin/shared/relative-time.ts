// Shared relative-time rendering for admin tables. Lifted out of
// `users/user-status.ts` when the MCP token vertical needed the same
// contract for `last_used_at` as Users has for `last_login_at`: a never-used row
// must read `Never` in both tables, not `Never` in one and `—` in the other.
//
// A SECOND relative-time formatter exists — `formatRelativeTime`, module-private
// inside `admin/audit/audit-format.ts` — and is deliberately NOT merged here.
// Named rather than cited by line: the line number this note first carried went
// stale the next time that file gained a function above it, and a note whose
// whole job is to stop a third formatter is worth nothing pointing at the wrong
// line. It is a different contract, not a
// duplicate: it takes a `Date` rather than an unknown wire value, returns `''`
// (not `Never`) for a missing or future timestamp, uses compact units (`5m ago`,
// `3h ago`, `12d ago`), and gives up past 30 days where this one falls back to
// an absolute date. Folding the two together would change how every audit row
// renders, which is a change to the Audit vertical — a thing this task has no
// business touching. Recorded here rather than left for the next reader to
// rediscover.

// Render a wire timestamp as relative English. Non-strings, blanks and
// unparseable values all read the same word — the table cell means "has this
// ever happened", and a malformed value is not evidence that it did. That word
// defaults to `Never` (capital N) for the Users/Tokens tables; the server
// verticals pass `'—'`, which is the em dash their columns have always shown.
// The parameter exists because those two words are both load-bearing: merging
// them either way would change a rendered cell.
export function formatRelativeTime(value: unknown, missing = 'Never'): string {
  if (typeof value !== 'string' || !value) return missing
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return missing

  const diffMs = Date.now() - date.getTime()
  if (diffMs < 0) return 'Just now'

  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 60) return 'Just now'

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
