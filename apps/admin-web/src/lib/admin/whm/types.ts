// WHM administration domain types (issue #108). Mirrors the FastAPI admin
// contract (apps/api .../api/routes/whm_admin.py + api/whm_admin/schemas.py)
// field-for-field so the BIGSU frontend keeps contract parity with the legacy
// web app: same shapes in, same shapes out, same stable error surface.
//
// The safe views deliberately carry NO secret material — the API never returns
// api_token, ssh_password, ssh_private_key, or reseller token values. Presence
// of the SSH secrets is exposed only as the booleans has_ssh_password /
// has_ssh_private_key, which drive the write-only "keep or replace" form copy.

export type WhmServer = {
  id: string
  name: string
  base_url: string
  api_username: string
  ssh_username: string | null
  ssh_port: number | null
  ssh_host_key_fingerprint: string | null
  has_ssh_password: boolean
  has_ssh_private_key: boolean
  verify_ssl: boolean
  created_at?: string
  updated_at?: string
}

export type WhmServerToken = {
  id: string
  server_id: string
  owner_username: string
  api_username: string
  created_at?: string
  updated_at?: string
}

export type ValidateWhmServerResponse = {
  ok: boolean
  error_code?: string | null
  message: string
}

export type ListWhmServersResponse = {
  servers: WhmServer[]
}

export type WhmServerResponse = {
  server: WhmServer
}

export type ListWhmServerTokensResponse = {
  tokens: WhmServerToken[]
}

export type WhmServerTokenResponse = {
  token: WhmServerToken
}

export type SshAuthMode = 'private_key' | 'password'
