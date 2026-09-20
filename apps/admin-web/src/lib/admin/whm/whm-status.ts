import type { BadgeSpec } from '@/lib/admin/shared/server-status'

import type { ValidateWhmServerResponse, WhmServer } from './types'

// WHM-only presentation derivations (issue #108). Everything WHM shares with the
// PMG and Proxmox verticals — validation status, SSH status and auth label, TLS
// and fingerprint badges, relative time — now lives in
// lib/admin/shared/server-status.ts and lib/admin/shared/relative-time.ts. What
// stays here is what only WHM has: the ACL-aware validate sentence and the
// reseller-credential flag. Nothing here ever reads a secret — the safe views
// carry none.

// Validate's message, made operator-actionable (a follow-up to the ACL-set validate report).
// BIGSU's status vocabulary is fixed at 11 values (rule 9) — a validate failure is always the
// `Failed` chip, whatever the code — so the code-specific work happens in this sentence,
// not in a new chip. Almost every code already carries a usable message straight from the
// backend (`ssh_timeout`, `ssh_auth_failed`, `ssh_host_key_mismatch`, …), so that is the
// fallback for anything this function does not special-case, including a future code neither
// side has named yet.
// `whm_token_acl_insufficient` — from the ACL-set validate report — is the one exception worth
// writing by hand: its backend message states the fact ("cannot suspend accounts") but not the fix, and the fix here
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

// Reseller-credential flag → Badge, shown only when true. It is a
// visibility/naming label, never authorization — the owner compare is the
// only write gate, and this flag only filters `whm_list_servers` output —
// omitted for `false` rows so 16 root rows do not
// all carry a redundant "root" badge.
export function getWhmResellerBadge(server: WhmServer | null): BadgeSpec | null {
  return server?.is_reseller_credential ? { variant: 'info', label: 'Reseller credential' } : null
}
