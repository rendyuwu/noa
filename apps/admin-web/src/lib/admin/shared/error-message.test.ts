import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import { toMessage } from './error-message'

describe('toMessage', () => {
  it('surfaces the stable backend detail from an ApiError', () => {
    const error = new ApiError(409, 'Role already exists', { errorCode: 'role_exists' })
    expect(toMessage(error, 'fallback')).toBe('Role already exists')
  })

  it('falls back when an ApiError carries no detail', () => {
    const error = new ApiError(500, '')
    expect(toMessage(error, 'Unable to load roles')).toBe('Unable to load roles')
  })

  it('uses a plain Error message when present', () => {
    expect(toMessage(new Error('boom'), 'fallback')).toBe('boom')
  })

  it('falls back for unknown thrown values', () => {
    expect(toMessage('nope', 'fallback')).toBe('fallback')
    expect(toMessage(null, 'fallback')).toBe('fallback')
  })
})
