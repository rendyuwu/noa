import type { ProxmoxServer } from './types'

// Form model + payload builders for the Proxmox server dialog (issue #109),
// ported from the legacy vertical with contract parity. The write-only secret
// rule is encoded here: the API token secret is only ever SENT, never seeded
// from a server (a server carries no secret), and on update a blank secret means
// "keep the stored value" — it is omitted from the payload rather than sent empty.

export type ProxmoxServerFormState = {
  name: string
  baseUrl: string
  apiTokenId: string
  apiTokenSecret: string
  verifySsl: boolean
}

// verify_ssl defaults off, matching the legacy Proxmox create form (Proxmox
// hosts commonly present self-signed certs, so verification is opt-in).
export const EMPTY_PROXMOX_FORM: ProxmoxServerFormState = {
  name: '',
  baseUrl: '',
  apiTokenId: '',
  apiTokenSecret: '',
  verifySsl: false,
}

// The single secret field, cleared after every save/cancel/close/failure.
// Exported so the dialog and controller clear exactly the same set — the single
// source of truth for "what must never persist in component state".
export const PROXMOX_SECRET_FIELDS = ['apiTokenSecret'] as const

export function clearProxmoxSecrets(form: ProxmoxServerFormState): ProxmoxServerFormState {
  return { ...form, apiTokenSecret: '' }
}

// Seed the edit form from a server. Non-secret fields carry over; the secret
// field starts blank because the API never returns one.
export function proxmoxFormStateFromServer(server: ProxmoxServer): ProxmoxServerFormState {
  return {
    name: server.name,
    baseUrl: server.base_url,
    apiTokenId: server.api_token_id,
    apiTokenSecret: '',
    verifySsl: server.verify_ssl,
  }
}

// A validation failure routed to the field it belongs to, so the Zod resolver
// can attach it to that FormField (per-field error text) rather than a generic
// banner. Single source of truth for the form's rules.
export type ProxmoxFormError = { field: keyof ProxmoxServerFormState; message: string }

// Client-side validation mirrors the legacy vertical; the FastAPI request models
// stay authoritative (name/base_url charset, HTTPS normalization, min lengths).
// Returns the first failure only (matching the legacy single-message behavior).
export function validateProxmoxServerForm(
  form: ProxmoxServerFormState,
  mode: 'create' | 'update',
): ProxmoxFormError | null {
  if (!form.name.trim()) return { field: 'name', message: 'Name is required' }
  if (!form.baseUrl.trim()) return { field: 'baseUrl', message: 'Base URL is required' }
  if (!form.apiTokenId.trim()) {
    return { field: 'apiTokenId', message: 'API token ID is required' }
  }
  if (mode === 'create' && !form.apiTokenSecret.trim()) {
    return { field: 'apiTokenSecret', message: 'API token secret is required' }
  }
  return null
}

export function buildProxmoxCreatePayload(form: ProxmoxServerFormState): Record<string, unknown> {
  return {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_token_id: form.apiTokenId.trim(),
    api_token_secret: form.apiTokenSecret.trim(),
    verify_ssl: form.verifySsl,
  }
}

export function buildProxmoxUpdatePayload(form: ProxmoxServerFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_token_id: form.apiTokenId.trim(),
    verify_ssl: form.verifySsl,
  }

  // Secret is write-only: only sent when the admin typed a new value to replace it.
  if (form.apiTokenSecret.trim()) {
    payload.api_token_secret = form.apiTokenSecret.trim()
  }

  return payload
}
