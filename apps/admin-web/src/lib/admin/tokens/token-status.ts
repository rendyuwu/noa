import type { StatusChipStatus } from '@gio/bigsu-ui'

import type { McpToken } from './types'

// Display derivations for the MCP token vertical (§T76). Pure, so the panel, the
// columns and the tests all reach the same verdict, and so the awkward cases
// (never expires, already expired, unbound) are unit-tested directly rather than
// through a rendered table.

// The BIGSU vocabulary has no `Expired`, and inventing one is forbidden — so an
// expired token maps to `Inactive`, exactly as a deactivated user does in
// `user-status.ts`. The two-value type is `Extract`ed from the chip's own union
// so a value outside the vocabulary cannot be returned.
export type TokenStatus = Extract<StatusChipStatus, 'Active' | 'Inactive'>

// Timestamps in the token tables render through the shared helper, so a token
// nobody has used reads `Never` — the same word, capitalised the same way, as a
// user who has never signed in.
export { formatRelativeTime } from '@/lib/admin/shared/relative-time'

// `expires_at` NULL means nothing retires the row but a revoke (mcp_tokens.py:62-66),
// so a token without one is Active until it is deleted.
//
// This is a DISPLAY derivation and not an authorization one. Whether the
// credential still works is decided by the API on the next request, never here —
// which is also why an unparseable `expires_at` renders Active rather than
// Inactive: the UI has no basis to declare a token dead, and the row's own
// expiry column is shown beside the chip for the operator to read.
export function deriveTokenStatus(token: McpToken): TokenStatus {
  const raw = token.expires_at
  if (typeof raw !== 'string' || !raw) return 'Active'
  const expiresAt = new Date(raw)
  if (Number.isNaN(expiresAt.getTime())) return 'Active'
  return expiresAt.getTime() <= Date.now() ? 'Inactive' : 'Active'
}

// `librechat_user_id` NULL means TOFU binding has not happened yet:
// the token has been minted but no LibreChat identity has claimed it. That is a
// normal state for a token an operator has just pasted into their config and not
// yet used, so it reads as a fact, not as a fault.
export function formatBinding(token: McpToken): string {
  const bound = token.librechat_user_id
  if (typeof bound !== 'string' || !bound.trim()) return 'Not bound yet'
  return bound
}
