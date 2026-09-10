// WHM administration domain types (issue #108). Mirrors the FastAPI admin
// contract (apps/api .../api/routes/admin_servers.py) field-for-field: same
// shapes in, same shapes out, same stable error surface.
//
// The safe view deliberately carries NO secret material — the API never returns
// api_token, ssh_password or ssh_private_key. Presence of the SSH secrets is
// exposed only as the booleans has_ssh_password / has_ssh_private_key, which
// drive the write-only "keep or replace" form copy.
//
// Per-reseller WHM API tokens are still NOT part of this contract. The legacy
// vertical had a whm_server_tokens table and a token sub-resource; NOA's schema
// has no such table and the admin API's contract names no such route, so
// adding one is a spec change rather than a build decision. The dead surface
// was removed with the admin CRUD-and-validate work, following the precedent
// set by retiring direct per-user grants.
//
// `is_reseller_credential` is a different thing: a flag on an
// ordinary row, not a sub-resource. It marks that the row's api_username
// belongs to a reseller rather than to root — visibility/naming only, never
// authorization (the owner compare is the only write gate) — and it is
// why a `true` row's `name` must equal its `api_username` (enforced server-side;
// the form mirrors the check for legible client-side feedback).

export type WhmServer = {
  id: string
  name: string
  base_url: string
  api_username: string
  is_reseller_credential: boolean
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
