import type { StatusChipStatus } from '@gio/bigsu-ui'

import type { ProxmoxServer, ValidateProxmoxServerResponse } from './types'

// Presentation derivations for the Proxmox vertical (issue #109), mapped onto the
// standard BIGSU vocabulary. Connection/validation lifecycle renders through
// StatusChip (the standard statuses); non-status labels (TLS verification, stored
// token presence) render through Badge tones. Nothing here ever reads a secret —
// the safe view carries none. Proxmox has no SSH host key, so there is no
// fingerprint state to derive; verify_ssl is the only transport-security signal.

export type BadgeTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'review' | 'brand'

// Validation lifecycle → StatusChip. A server that has never been validated is
// Pending (waiting to be checked); a passing check is Completed; a failing one is
// Failed. This is the operational question the table column answers.
export function deriveProxmoxValidationStatus(
  result: ValidateProxmoxServerResponse | undefined,
): StatusChipStatus {
  if (!result) return 'Pending'
  return result.ok ? 'Completed' : 'Failed'
}

// SSL verification is a configuration label, not a workflow status → Badge. It is
// the only transport-security state Proxmox exposes, so the detail drawer and
// table surface it explicitly for accessibility.
export function getProxmoxTlsBadge(
  server: ProxmoxServer | null,
): { variant: BadgeTone; label: string } {
  return server?.verify_ssl
    ? { variant: 'success', label: 'Verify enabled' }
    : { variant: 'warning', label: 'Verification off' }
}

// Stored-secret presence is a category label → Badge. The API returns only the
// has_api_token_secret boolean; the secret value is never shown.
export function getProxmoxTokenSecretBadge(
  server: ProxmoxServer | null,
): { variant: BadgeTone; label: string } {
  return server?.has_api_token_secret
    ? { variant: 'success', label: 'Stored' }
    : { variant: 'neutral', label: 'Not configured' }
}

// Relative-time formatter, ported verbatim from the legacy vertical so the
// "Updated N ago" copy stays identical across the migration.
export function formatProxmoxRelativeTime(value: unknown): string {
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
