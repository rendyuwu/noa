import type { SshAuthMode, WhmServer } from './types'

// Form model + payload builders for the WHM server dialog (issue #108), ported
// from the legacy vertical with contract parity. The write-only secret rule is
// encoded here: secrets (apiToken, sshPassword, sshPrivateKey, passphrase) are
// only ever SENT, never seeded from a server (a server carries no secret), and
// on update a blank secret means "keep the stored value" — the corresponding
// clear_* flag is only set when the admin explicitly drops SSH config.

export type WhmServerFormState = {
  name: string
  baseUrl: string
  apiUsername: string
  apiToken: string
  verifySsl: boolean
  isResellerCredential: boolean
  enableSsh: boolean
  sshUsername: string
  sshPort: string
  sshAuthMode: SshAuthMode
  sshPassword: string
  sshPrivateKey: string
  sshPrivateKeyPassphrase: string
}

export const EMPTY_WHM_FORM: WhmServerFormState = {
  name: '',
  baseUrl: '',
  apiUsername: '',
  apiToken: '',
  verifySsl: true,
  isResellerCredential: false,
  enableSsh: false,
  sshUsername: '',
  sshPort: '',
  sshAuthMode: 'private_key',
  sshPassword: '',
  sshPrivateKey: '',
  sshPrivateKeyPassphrase: '',
}

// Seed the edit form from a server. Non-secret fields carry over; every secret
// field starts blank because the API never returns one. enableSsh is inferred
// from any stored SSH configuration so an existing setup opens expanded.
export function whmFormStateFromServer(server: WhmServer): WhmServerFormState {
  return {
    name: server.name,
    baseUrl: server.base_url,
    apiUsername: server.api_username,
    apiToken: '',
    verifySsl: server.verify_ssl,
    isResellerCredential: server.is_reseller_credential,
    enableSsh:
      server.has_ssh_password ||
      server.has_ssh_private_key ||
      Boolean(server.ssh_username) ||
      server.ssh_port !== null,
    sshUsername: server.ssh_username ?? '',
    sshPort: server.ssh_port != null ? String(server.ssh_port) : '',
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
export type WhmFormError = { field: keyof WhmServerFormState; message: string }

// Client-side validation mirrors the legacy vertical; the FastAPI request models
// stay authoritative (name/base_url/username charset, port range, min lengths).
// Returns the first failure only (matching the legacy single-message behavior).
export function validateWhmServerForm(
  form: WhmServerFormState,
  mode: 'create' | 'update',
  existingServer?: WhmServer | null,
): WhmFormError | null {
  if (!form.name.trim()) return { field: 'name', message: 'Name is required' }
  if (!form.baseUrl.trim()) return { field: 'baseUrl', message: 'Base URL is required' }
  if (!form.apiUsername.trim()) {
    return { field: 'apiUsername', message: 'API username is required' }
  }
  if (mode === 'create' && !form.apiToken.trim()) {
    return { field: 'apiToken', message: 'API token is required for WHM API operations' }
  }

  // The name-equals-api_username rule: a reseller credential must resolve back to itself via
  // resolve_whm_server_ref's name match, since api_username is not one of the
  // forms that resolver tries. Mirrors the server's normalized (trim + lower)
  // compare; the server enforces this regardless of what the client sends.
  if (form.isResellerCredential) {
    const normalizedName = form.name.trim().toLowerCase()
    const normalizedApiUsername = form.apiUsername.trim().toLowerCase()
    if (normalizedName !== normalizedApiUsername) {
      return {
        field: 'name',
        message: 'A reseller credential requires Name to match API username (case-insensitive).',
      }
    }
  }

  if (!form.enableSsh) return null

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

export function buildWhmCreatePayload(form: WhmServerFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_username: form.apiUsername.trim(),
    api_token: form.apiToken.trim(),
    verify_ssl: form.verifySsl,
    is_reseller_credential: form.isResellerCredential,
  }

  if (!form.enableSsh) return payload

  const sshUsername = form.sshUsername.trim()
  const sshPort = parseOptionalPort(form.sshPort)
  if (sshUsername) payload.ssh_username = sshUsername
  if (sshPort !== null && !Number.isNaN(sshPort)) payload.ssh_port = sshPort

  if (form.sshAuthMode === 'password') {
    payload.ssh_password = form.sshPassword.trim()
  } else {
    payload.ssh_private_key = form.sshPrivateKey.trim()
    const passphrase = form.sshPrivateKeyPassphrase.trim()
    if (passphrase) payload.ssh_private_key_passphrase = passphrase
  }

  return payload
}

export function buildWhmUpdatePayload(
  form: WhmServerFormState,
  existingServer: WhmServer,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_username: form.apiUsername.trim(),
    verify_ssl: form.verifySsl,
    is_reseller_credential: form.isResellerCredential,
  }

  // Token is write-only: only sent when the admin typed a new value to replace it.
  if (form.apiToken.trim()) payload.api_token = form.apiToken.trim()

  const hadSshConfig =
    existingServer.has_ssh_password ||
    existingServer.has_ssh_private_key ||
    Boolean(existingServer.ssh_username) ||
    existingServer.ssh_port !== null ||
    Boolean(existingServer.ssh_host_key_fingerprint)

  if (!form.enableSsh) {
    if (hadSshConfig) payload.clear_ssh_configuration = true
    return payload
  }

  const normalizedUsername = form.sshUsername.trim()
  if (normalizedUsername) {
    payload.ssh_username = normalizedUsername
  } else if (existingServer.ssh_username) {
    payload.clear_ssh_username = true
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
