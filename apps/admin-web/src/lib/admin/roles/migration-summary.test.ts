import { describe, expect, it } from 'vitest'

import { summarizeDirectGrantsMigration } from './migration-summary'

describe('summarizeDirectGrantsMigration', () => {
  it('summarizes snake_case counts with correct pluralization', () => {
    expect(
      summarizeDirectGrantsMigration({ users_migrated: 2, roles_created: 1, roles_reused: 3 }),
    ).toBe('Migration complete: 2 users migrated; 1 role created; 3 roles reused.')
  })

  it('accepts camelCase aliases', () => {
    expect(summarizeDirectGrantsMigration({ usersMigrated: 1, rolesCreated: 0 })).toBe(
      'Migration complete: 1 user migrated; 0 roles created.',
    )
  })

  it('falls back for a payload with no recognized counts', () => {
    expect(summarizeDirectGrantsMigration({ unrelated: true })).toBe('Migration completed.')
    expect(summarizeDirectGrantsMigration(null)).toBe('Migration completed.')
    expect(summarizeDirectGrantsMigration('nope')).toBe('Migration completed.')
  })

  it('ignores non-finite numeric values', () => {
    expect(summarizeDirectGrantsMigration({ users_migrated: Number.NaN })).toBe(
      'Migration completed.',
    )
  })
})
