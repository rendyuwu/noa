// PMG administration domain types (issue #110). Mirrors the FastAPI admin
// contract (apps/api .../api/routes/pmg_admin.py + api/pmg_admin/schemas.py)
// field-for-field so the BIGSU frontend keeps contract parity with the legacy
// web app: same shapes in, same shapes out, same stable error surface.
//
// The safe view deliberately carries NO secret material — the API never returns
// ssh_password or ssh_private_key. Presence of the stored SSH secrets is exposed
// only as the booleans has_ssh_password / has_ssh_private_key, which drive the
// write-only "keep or replace" form copy. PMG is SSH-only: there is no API base
// URL, no API token, and no verify_ssl toggle (unlike WHM/Proxmox). Its only
// transport-security state is the pinned SSH host-key fingerprint.

export type PmgServer = {
  id: string
  name: string
  ssh_host: string
  ssh_username: string | null
  ssh_port: number | null
  ssh_host_key_fingerprint: string | null
  has_ssh_password: boolean
  has_ssh_private_key: boolean
  created_at?: string
  updated_at?: string
}

export type ValidatePmgServerResponse = {
  ok: boolean
  error_code?: string | null
  message: string
}

export type SshAuthMode = 'private_key' | 'password'
