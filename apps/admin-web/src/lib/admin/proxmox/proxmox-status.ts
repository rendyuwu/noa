import type { BadgeSpec } from '@/lib/admin/shared/server-status'

import type { ProxmoxServer } from './types'

// Proxmox-only presentation derivations (issue #109). Validation status, the TLS
// badge and relative time are shared with the WHM and PMG verticals and live in
// lib/admin/shared/server-status.ts and lib/admin/shared/relative-time.ts. What
// stays here is the one signal only Proxmox has. Nothing here ever reads a
// secret — the safe view carries none.

// Stored-secret presence is a category label → Badge. The API returns only the
// has_api_token_secret boolean; the secret value is never shown.
export function getProxmoxTokenSecretBadge(server: ProxmoxServer | null): BadgeSpec {
  return server?.has_api_token_secret
    ? { variant: 'success', label: 'Stored' }
    : { variant: 'neutral', label: 'Not configured' }
}
