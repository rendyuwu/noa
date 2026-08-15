import { describe, expect, it } from 'vitest'

import { coerceRoleNames, coerceStringArray } from './coerce'

describe('coerceStringArray', () => {
  it('keeps only string members', () => {
    expect(coerceStringArray(['a', 1, 'b', null, {}])).toEqual(['a', 'b'])
  })

  it('returns an empty array for non-arrays', () => {
    expect(coerceStringArray('nope')).toEqual([])
    expect(coerceStringArray(undefined)).toEqual([])
  })
})

describe('coerceRoleNames', () => {
  it('accepts bare string arrays', () => {
    expect(coerceRoleNames(['admin', 'member'])).toEqual(['admin', 'member'])
  })

  it('accepts { name } object arrays and skips objects without a string name', () => {
    expect(coerceRoleNames([{ name: 'admin' }, { name: 'member' }, { other: 1 }])).toEqual([
      'admin',
      'member',
    ])
  })

  it('returns an empty array for non-arrays', () => {
    expect(coerceRoleNames(undefined)).toEqual([])
  })
})
