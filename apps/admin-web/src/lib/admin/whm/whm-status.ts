import type { StatusChipStatus } from '@gio/bigsu-ui'

import type { ValidateWhmServerResponse, WhmServer } from './types'

// Presentation derivations for the WHM vertical (issue #108), mapped onto the
// standard BIGSU vocabulary. Connection/validation lifecycle renders through
// StatusChip (the 11 standard statuses); non-status labels (TLS verification,
// host-key pinning) render through Badge tones. Nothing here ever reads a
// secret — the safe views carry none.

// Validation lifecycle → StatusChip. A server that has never been validated is
// Pending (waiting to be checked); a passing check is Completed; a failing one
// is Failed. This is the operational question the table column answers.
export function deriveWhmValidationStatus(
  result: ValidateWhmServerResponse | undefined,
): StatusChipStatus {
  if (!result) return 'Pending'
  return result.ok ? 'Completed' : 'Failed'
}

// Validate's message, made operator-actionable (T80 follow-up). BIGSU's status vocabulary is
// fixed at 11 values (rule 9) — a validate failure is always the `Failed` chip above, whatever
// the code — so the code-specific work happens in this sentence, not in a new chip. Almost
// every code already carries a usable message straight from the backend (`ssh_timeout`,
// `ssh_auth_failed`, `ssh_host_key_mismatch`, …), so that is the fallback for anything this
// function does not special-case, including a future code neither side has named yet.
// `whm_token_acl_insufficient` (§V.111, §T.80) is the one exception worth writing by hand: its
// backend message states the fact ("cannot suspend accounts") but not the fix, and the fix here
// is "grant an ACL in WHM", not "retry" — a distinction worth spelling out for the operator
// reading it, not left implicit in an ACL dump.
export function getWhmValidationMessage(result: ValidateWhmServerResponse | undefined): string {
  if (!result) {
    return 'Validate checks the WHM API token, then SSH (if configured), and pins the host key.'
  }
  if (result.error_code === 'whm_token_acl_insufficient') {
    return (
      'This WHM API token is missing the suspend-acct ACL, so it cannot suspend or unsuspend ' +
      'accounts. Grant suspend-acct to the token in WHM, then Validate again.'
    )
  }
  return result.message
}

// SSH credential lifecycle → StatusChip. Configured credentials are Active;
// no stored credentials is Inactive (SSH-backed tools are unavailable).
export function deriveWhmSshStatus(server: WhmServer | null): StatusChipStatus {
  if (server && (server.has_ssh_password || server.has_ssh_private_key)) return 'Active'
  return 'Inactive'
}

export function getWhmSshAuthLabel(server: WhmServer | null): string {
  if (!server) return '—'
  if (server.has_ssh_private_key) return 'SSH key'
  if (server.has_ssh_password) return 'Password'
  return '—'
}

export type BadgeTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'review' | 'brand'

// TLS verification is a configuration label, not a workflow status → Badge.
export function getWhmTlsBadge(server: WhmServer | null): { variant: BadgeTone; label: string } {
  return server?.verify_ssl
    ? { variant: 'success', label: 'Verify enabled' }
    : { variant: 'warning', label: 'Verification off' }
}

// Reseller-credential flag → Badge, shown only when true. It is a
// visibility/naming label, never authorization (§V.106's owner compare is the
// only write gate, §V.109) — omitted for `false` rows so 16 root rows do not
// all carry a redundant "root" badge.
export function getWhmResellerBadge(
  server: WhmServer | null,
): { variant: BadgeTone; label: string } | null {
  return server?.is_reseller_credential ? { variant: 'info', label: 'Reseller credential' } : null
}

// Host-key pinning is a category label → Badge.
export function getWhmFingerprintBadge(
  server: WhmServer | null,
): { variant: BadgeTone; label: string } {
  return server?.ssh_host_key_fingerprint
    ? { variant: 'success', label: 'Host key pinned' }
    : { variant: 'neutral', label: 'Fingerprint missing' }
}

// Relative-time formatter, ported verbatim from the legacy vertical so the
// "Updated N ago" copy stays identical across the migration.
export function formatWhmRelativeTime(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const now = Date.now()
  const diffMs = now - date.getTime()
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
