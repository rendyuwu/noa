// Admin user domain types (issue #106). Mirrors the FastAPI admin contract
// (apps/api .../api/admin/schemas.py + user_routes.py) field-for-field so the
// BIGSU frontend keeps contract parity with the legacy web app: same shapes in,
// same shapes out, same stable error surface (see users-api.ts).

export type AdminUser = {
  id: string
  email: string
  display_name?: string | null
  created_at?: string
  last_login_at?: string | null
  is_active?: boolean
  roles?: string[]
  tools?: string[]
  // No `direct_tools`. `noa-old`'s response carried it; NOA's `AdminUserResponse`
  // does not send it — direct per-user grants answer 410.
}

export type AdminUsersResponse = {
  users: AdminUser[]
}

export type UpdateUserResponse = {
  user: AdminUser
}

export type AdminRole = {
  name: string
}

export type AdminRolesResponse = {
  roles: AdminRole[] | string[]
}
