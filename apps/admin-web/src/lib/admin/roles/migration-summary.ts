// Normalise the legacy direct-grant migration summary (issue #107). The endpoint
// reports counts under varying key casings across API versions, so we read every
// known alias and fall back gracefully — the operator always gets a definite,
// human sentence rather than raw JSON or a silent success.

function coerceFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function pickCount(record: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = coerceFiniteNumber(record[key])
    if (value !== null) return value
  }
  return null
}

function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

export function summarizeDirectGrantsMigration(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return 'Migration completed.'
  const record = payload as Record<string, unknown>

  const usersMigrated = pickCount(record, [
    'users_migrated',
    'usersMigrated',
    'migrated_users',
    'migratedUsers',
  ])
  const rolesCreated = pickCount(record, [
    'roles_created',
    'rolesCreated',
    'created_roles',
    'createdRoles',
  ])
  const rolesReused = pickCount(record, [
    'roles_reused',
    'rolesReused',
    'reused_roles',
    'reusedRoles',
  ])

  const parts: string[] = []
  if (usersMigrated !== null) parts.push(`${pluralize(usersMigrated, 'user')} migrated`)
  if (rolesCreated !== null) parts.push(`${pluralize(rolesCreated, 'role')} created`)
  if (rolesReused !== null) parts.push(`${pluralize(rolesReused, 'role')} reused`)

  if (parts.length === 0) return 'Migration completed.'
  return `Migration complete: ${parts.join('; ')}.`
}
