import type { PmgServer, SshAuthMode } from './types'

// Form model + payload builders for the PMG server dialog (issue #110), ported
// from the legacy vertical with contract parity. The write-only secret rule is
// encoded here: secrets (sshPassword, sshPrivateKey, passphrase) are only ever
// SENT, never seeded from a server (a server carries no secret), and on update a
// blank secret means "keep the stored value".
//
// PMG is SSH-only. The legacy form carried an "Enable SSH" toggle, but PMG
// validation hard-rejects an SSH-off server ("SSH access is required for PMG
// servers"), so the toggle could never be usefully turned off. That is a BIGSU
// correction, not a copied defect: SSH is mandatory here, so there is no toggle
// and every update always carries SSH config.

export type PmgServerFormState = {
  name: string
  sshHost: string
  sshUsername: string
  sshPort: string
  sshHostKeyFingerprint: string
  sshAuthMode: SshAuthMode
  sshPassword: string
  sshPrivateKey: string
  sshPrivateKeyPassphrase: string
}

export const EMPTY_PMG_FORM: PmgServerFormState = {
  name: '',
  sshHost: '',
  sshUsername: '',
  sshPort: '',
  sshHostKeyFingerprint: '',
  sshAuthMode: 'private_key',
  sshPassword: '',
  sshPrivateKey: '',
  sshPrivateKeyPassphrase: '',
}

// The secret fields, cleared after every save/cancel/close/failure. Exported so
// the dialog and controller clear exactly the same set — the single source of
// truth for "what must never persist in component state".
export const PMG_SECRET_FIELDS = [
  'sshPassword',
  'sshPrivateKey',
  'sshPrivateKeyPassphrase',
] as const

export function clearPmgSecrets(form: PmgServerFormState): PmgServerFormState {
  return { ...form, sshPassword: '', sshPrivateKey: '', sshPrivateKeyPassphrase: '' }
}

// Seed the edit form from a server. Non-secret fields carry over; every secret
// field starts blank because the API never returns one. The auth mode opens on
// whichever credential the server already stores (private key preferred).
export function pmgFormStateFromServer(server: PmgServer): PmgServerFormState {
  return {
    name: server.name,
    sshHost: server.ssh_host,
    sshUsername: server.ssh_username ?? '',
    sshPort: server.ssh_port != null ? String(server.ssh_port) : '',
    sshHostKeyFingerprint: server.ssh_host_key_fingerprint ?? '',
    sshAuthMode: server.has_ssh_private_key ? 'private_key' : 'password',
    sshPassword: '',
    sshPrivateKey: '',
    sshPrivateKeyPassphrase: '',
  }
}

function parseOptionalPort(value: string): number | null {
  const normalized = value.trim()
  if (!normalized) return null
  const parsed = Number(normalized)
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) return Number.NaN
  return parsed
}

// A validation failure routed to the field it belongs to, so the Zod resolver
// can attach it to that FormField (per-field error text) rather than a generic
// banner. Single source of truth for the form's cross-field rules.
export type PmgFormError = { field: keyof PmgServerFormState; message: string }

// Client-side validation mirrors the legacy vertical; the FastAPI request models
// stay authoritative (name/ssh_host charset, port range, min lengths). Returns
// the first failure only (matching the legacy single-message behavior).
export function validatePmgServerForm(
  form: PmgServerFormState,
  mode: 'create' | 'update',
  existingServer?: PmgServer | null,
): PmgFormError | null {
  if (!form.name.trim()) return { field: 'name', message: 'Name is required' }
  if (!form.sshHost.trim()) return { field: 'sshHost', message: 'SSH host/IP is required' }

  const sshPort = parseOptionalPort(form.sshPort)
  if (Number.isNaN(sshPort)) {
    return { field: 'sshPort', message: 'SSH port must be between 1 and 65535' }
  }

  const hadPassword = existingServer?.has_ssh_password ?? false
  const hadPrivateKey = existingServer?.has_ssh_private_key ?? false

  if (form.sshAuthMode === 'password') {
    const requiresNewPassword = mode === 'create' || !hadPassword || hadPrivateKey
    if (requiresNewPassword && !form.sshPassword.trim()) {
      return {
        field: 'sshPassword',
        message: 'SSH password is required when password authentication is selected',
      }
    }
    return null
  }

  const requiresNewPrivateKey = mode === 'create' || !hadPrivateKey || hadPassword
  if (requiresNewPrivateKey && !form.sshPrivateKey.trim()) {
    return {
      field: 'sshPrivateKey',
      message: 'SSH private key is required when SSH key authentication is selected',
    }
  }
  return null
}

export function buildPmgCreatePayload(form: PmgServerFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    ssh_host: form.sshHost.trim(),
  }

  const sshUsername = form.sshUsername.trim()
  const sshPort = parseOptionalPort(form.sshPort)
  const sshHostKeyFingerprint = form.sshHostKeyFingerprint.trim()
  if (sshUsername) payload.ssh_username = sshUsername
  if (sshPort !== null && !Number.isNaN(sshPort)) payload.ssh_port = sshPort
  if (sshHostKeyFingerprint) payload.ssh_host_key_fingerprint = sshHostKeyFingerprint

  if (form.sshAuthMode === 'password') {
    payload.ssh_password = form.sshPassword.trim()
  } else {
    payload.ssh_private_key = form.sshPrivateKey.trim()
    const passphrase = form.sshPrivateKeyPassphrase.trim()
    if (passphrase) payload.ssh_private_key_passphrase = passphrase
  }

  return payload
}

export function buildPmgUpdatePayload(
  form: PmgServerFormState,
  existingServer: PmgServer,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    ssh_host: form.sshHost.trim(),
  }

  const normalizedUsername = form.sshUsername.trim()
  if (normalizedUsername) {
    payload.ssh_username = normalizedUsername
  } else if (existingServer.ssh_username) {
    payload.clear_ssh_username = true
  }

  const normalizedHostKeyFingerprint = form.sshHostKeyFingerprint.trim()
  if (normalizedHostKeyFingerprint) {
    payload.ssh_host_key_fingerprint = normalizedHostKeyFingerprint
  } else if (existingServer.ssh_host_key_fingerprint) {
    payload.clear_ssh_host_key_fingerprint = true
  }

  const sshPort = parseOptionalPort(form.sshPort)
  if (sshPort !== null && !Number.isNaN(sshPort)) {
    payload.ssh_port = sshPort
  } else if (existingServer.ssh_port !== null) {
    payload.clear_ssh_port = true
  }

  if (form.sshAuthMode === 'password') {
    if (existingServer.has_ssh_private_key) {
      payload.clear_ssh_private_key = true
      payload.clear_ssh_private_key_passphrase = true
    }
    if (form.sshPassword.trim()) payload.ssh_password = form.sshPassword.trim()
  } else {
    if (existingServer.has_ssh_password) payload.clear_ssh_password = true
    if (form.sshPrivateKey.trim()) {
      payload.ssh_private_key = form.sshPrivateKey.trim()
      if (form.sshPrivateKeyPassphrase.trim()) {
        payload.ssh_private_key_passphrase = form.sshPrivateKeyPassphrase.trim()
      } else {
        payload.clear_ssh_private_key_passphrase = true
      }
    }
  }

  return payload
}
