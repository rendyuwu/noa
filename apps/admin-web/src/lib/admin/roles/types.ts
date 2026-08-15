// Admin role domain types (issue #107). Mirrors the FastAPI admin contract
// (apps/api .../api/admin/role_routes.py + schemas.py) field-for-field so the
// BIGSU frontend keeps contract parity with the legacy web app: roles arrive as
// bare strings or `{ name }` objects, tools are string arrays, and the stable
// error surface (ROLE_EXISTS / UNKNOWN_ROLES / INTERNAL_ROLE_FORBIDDEN / …)
// stays authoritative on the server.

export type AdminRole = {
  name: string
}

export type AdminRolesResponse = {
  roles: AdminRole[] | string[]
}

export type AdminToolsResponse = {
  tools: string[]
}

export type AdminRoleToolsResponse = {
  tools: string[]
}

// The direct-grant migration endpoint returns a free-form summary object; the
// exact key casing varies, so it is normalised at read time (see
// migration-summary.ts) rather than typed field-by-field here.
export type DirectGrantsMigrationResponse = Record<string, unknown>
