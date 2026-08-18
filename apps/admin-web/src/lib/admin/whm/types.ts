// WHM administration domain types (issue #108). Mirrors the FastAPI admin
// contract (apps/api .../api/routes/admin_servers.py) field-for-field: same
// shapes in, same shapes out, same stable error surface.
//
// The safe view deliberately carries NO secret material — the API never returns
// api_token, ssh_password or ssh_private_key. Presence of the SSH secrets is
// exposed only as the booleans has_ssh_password / has_ssh_private_key, which
// drive the write-only "keep or replace" form copy.
//
// Per-reseller WHM API tokens are NOT part of this contract. The legacy vertical
// had a whm_server_tokens table and a token sub-resource; NOA's schema has no
// such table and §I.admin-api names no such route, so adding one is a spec change
// rather than a build decision (docs/integrations/whm.md, "Not built yet"). The
// dead surface was removed with §T.54, following §T.65's precedent for the
// direct-grant controls.

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

export type SshAuthMode = 'private_key' | 'password'
