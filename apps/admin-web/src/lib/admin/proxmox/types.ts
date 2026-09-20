// Proxmox administration domain types (issue #109). Mirrors the FastAPI admin
// contract (apps/api .../api/routes/proxmox_admin.py + api/proxmox_admin/schemas.py)
// field-for-field so the BIGSU frontend keeps contract parity with the legacy
// web app: same shapes in, same shapes out, same stable error surface.
//
// The safe view deliberately carries NO secret material — the API never returns
// api_token_secret. Presence of the stored secret is exposed only as the boolean
// has_api_token_secret, which drives the write-only "keep or replace" form copy.
// Proxmox has no SSH path and no host-key fingerprint (unlike WHM); its only
// transport-security state is the verify_ssl toggle.

export type ProxmoxServer = {
  id: string
  name: string
  base_url: string
  api_token_id: string
  has_api_token_secret: boolean
  verify_ssl: boolean
  created_at?: string
  updated_at?: string
}

export type ValidateProxmoxServerResponse = {
  ok: boolean
  error_code?: string | null
  message: string
}
