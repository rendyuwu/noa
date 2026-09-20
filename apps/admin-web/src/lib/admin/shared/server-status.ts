import type { StatusChipStatus } from '@gio/bigsu-ui'

// Presentation derivations shared by the WHM, PMG and Proxmox server verticals,
// mapped onto the standard BIGSU vocabulary. Connection/validation and
// SSH-credential lifecycle render through StatusChip (the 11 standard statuses);
// non-status labels (TLS verification, host-key pinning) render through Badge
// tones. Nothing here ever reads a secret — the safe views carry none.
//
// Each function takes the narrowest structural shape it needs rather than a
// vertical's domain type, so a vertical that lacks a field simply cannot call
// the function that reads it: Proxmox has no SSH host key and never calls
// getFingerprintBadge; PMG is SSH-only with no verify_ssl and never calls
// getTlsBadge. Which signals a vertical surfaces is stated by its config in
// components/admin/{whm,pmg,proxmox}, not by which helpers exist.

export type BadgeTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'review' | 'brand'
export type BadgeSpec = { variant: BadgeTone; label: string }

// Validation lifecycle → StatusChip. A server that has never been validated is
// Pending (waiting to be checked); a passing check is Completed; a failing one
// is Failed. This is the operational question the table column answers.
export function deriveServerValidationStatus(result: { ok: boolean } | undefined): StatusChipStatus {
  if (!result) return 'Pending'
  return result.ok ? 'Completed' : 'Failed'
}

type SshCredentialed = { has_ssh_password: boolean; has_ssh_private_key: boolean }

// SSH credential lifecycle → StatusChip. Configured credentials are Active; no
// stored credentials is Inactive (SSH-backed tools are unavailable until
// credentials are added and validated).
export function deriveSshStatus(server: SshCredentialed | null): StatusChipStatus {
  if (server && (server.has_ssh_password || server.has_ssh_private_key)) return 'Active'
  return 'Inactive'
}

export function getSshAuthLabel(server: SshCredentialed | null): string {
  if (!server) return '—'
  if (server.has_ssh_private_key) return 'SSH key'
  if (server.has_ssh_password) return 'Password'
  return '—'
}

// Host-key pinning is a category label, not a workflow status → Badge.
export function getFingerprintBadge(
  server: { ssh_host_key_fingerprint: string | null } | null,
): BadgeSpec {
  return server?.ssh_host_key_fingerprint
    ? { variant: 'success', label: 'Host key pinned' }
    : { variant: 'neutral', label: 'Fingerprint missing' }
}

// TLS/SSL verification is a configuration label, not a workflow status → Badge.
export function getTlsBadge(server: { verify_ssl: boolean } | null): BadgeSpec {
  return server?.verify_ssl
    ? { variant: 'success', label: 'Verify enabled' }
    : { variant: 'warning', label: 'Verification off' }
}
