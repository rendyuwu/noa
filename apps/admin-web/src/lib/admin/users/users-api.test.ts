import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import {
  coerceRoleNames,
  coerceStringArray,
  deleteUser,
  fetchUsersAndRoles,
  setUserActive,
  setUserRoles,
} from './users-api'

// Keep the real jsonOrThrow / ApiError (so we exercise the stable-error contract)
// and only stub the network boundary.
vi.mock('@/lib/auth/fetch-helper', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/auth/fetch-helper')>()
  return { ...actual, fetchWithAuth: vi.fn() }
})

const { fetchWithAuth } = await import('@/lib/auth/fetch-helper')
const mockFetch = fetchWithAuth as unknown as Mock

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}): Response {
  const { ok = true, status = 200 } = init
  return {
    ok,
    status,
    json: async () => body,
    headers: { get: () => null },
  } as unknown as Response
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe('coercers', () => {
  it('coerceStringArray keeps only strings', () => {
    expect(coerceStringArray(['a', 1, 'b', null, {}])).toEqual(['a', 'b'])
    expect(coerceStringArray('nope')).toEqual([])
  })

  it('coerceRoleNames accepts both bare strings and { name } objects', () => {
    expect(coerceRoleNames(['admin', 'member'])).toEqual(['admin', 'member'])
    expect(coerceRoleNames([{ name: 'admin' }, { name: 'member' }, { other: 1 }])).toEqual([
      'admin',
      'member',
    ])
    expect(coerceRoleNames(undefined)).toEqual([])
  })
})

describe('fetchUsersAndRoles', () => {
  it('loads users + roles and returns a sorted, de-duplicated role list', async () => {
    mockFetch.mockImplementation((path: string) => {
      if (path === '/admin/users') {
        return Promise.resolve(jsonResponse({ users: [{ id: '1', email: 'a@x.io' }] }))
      }
      return Promise.resolve(jsonResponse({ roles: ['member', 'admin', 'member'] }))
    })

    const result = await fetchUsersAndRoles()

    expect(mockFetch).toHaveBeenCalledWith('/admin/users')
    expect(mockFetch).toHaveBeenCalledWith('/admin/roles')
    expect(result.users).toHaveLength(1)
    expect(result.roles).toEqual(['admin', 'member'])
  })
})

describe('mutations', () => {
  it('setUserRoles PUTs the role set and returns the updated user', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ user: { id: '1', email: 'a@x.io', roles: ['admin'] } }))

    const user = await setUserRoles('1', ['admin'])

    expect(mockFetch).toHaveBeenCalledWith('/admin/users/1/roles', {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ roles: ['admin'] }),
    })
    expect(user.roles).toEqual(['admin'])
  })

  it('setUserActive PATCHes is_active and returns the updated user', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ user: { id: '1', email: 'a@x.io', is_active: false } }))

    const user = await setUserActive('1', false)

    expect(mockFetch).toHaveBeenCalledWith('/admin/users/1', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ is_active: false }),
    })
    expect(user.is_active).toBe(false)
  })

  it('deleteUser issues a DELETE', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ok: true }))
    await deleteUser('1')
    expect(mockFetch).toHaveBeenCalledWith('/admin/users/1', { method: 'DELETE' })
  })

  it('preserves the stable backend detail/error_code on a rejected mutation', async () => {
    mockFetch.mockResolvedValue(
      jsonResponse(
        { detail: 'Cannot deactivate the last active admin', error_code: 'last_active_admin' },
        { ok: false, status: 409 },
      ),
    )

    await expect(setUserActive('1', false)).rejects.toMatchObject({
      detail: 'Cannot deactivate the last active admin',
      errorCode: 'last_active_admin',
      status: 409,
    })
    await expect(setUserActive('1', false)).rejects.toBeInstanceOf(ApiError)
  })
})
