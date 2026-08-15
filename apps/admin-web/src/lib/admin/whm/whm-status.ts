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
