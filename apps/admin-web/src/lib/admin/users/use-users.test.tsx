import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import type { AdminUser } from './types'
import { useUsers } from './use-users'

vi.mock('./users-api', () => ({
  fetchUsersAndRoles: vi.fn(),
  setUserRoles: vi.fn(),
  setUserActive: vi.fn(),
  deleteUser: vi.fn(),
}))

const api = await import('./users-api')
const fetchUsersAndRoles = api.fetchUsersAndRoles as unknown as Mock
const setUserRoles = api.setUserRoles as unknown as Mock
const setUserActive = api.setUserActive as unknown as Mock
const deleteUser = api.deleteUser as unknown as Mock

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const userA: AdminUser = { id: 'A', email: 'a@x.io', roles: ['member'], is_active: true }
const userB: AdminUser = { id: 'B', email: 'b@x.io', roles: [], is_active: true }

beforeEach(() => {
  fetchUsersAndRoles.mockReset()
  setUserRoles.mockReset()
  setUserActive.mockReset()
  deleteUser.mockReset()
  fetchUsersAndRoles.mockResolvedValue({ users: [userA, userB], roles: ['admin', 'member'] })
})

async function mounted() {
  const view = renderHook(() => useUsers())
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

describe('useUsers loading', () => {
  it('loads users and roles on mount', async () => {
    const { result } = await mounted()
    expect(result.current.users).toHaveLength(2)
    expect(result.current.availableRoles).toEqual(['admin', 'member'])
    expect(result.current.loadError).toBeNull()
  })

  it('surfaces a load failure as loadError', async () => {
    fetchUsersAndRoles.mockRejectedValueOnce(new ApiError(500, 'Upstream is down'))
    const { result } = renderHook(() => useUsers())
    await waitFor(() => expect(result.current.loadError).toBe('Upstream is down'))
  })

  it('ignores a stale load whose sequence is no longer current', async () => {
    const first = deferred<{ users: AdminUser[]; roles: string[] }>()
    const second = deferred<{ users: AdminUser[]; roles: string[] }>()
    fetchUsersAndRoles.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = renderHook(() => useUsers())
    await act(async () => {
      void result.current.reload()
    })

    // The newer load resolves first, then the stale one lands late and is dropped.
    await act(async () => {
      second.resolve({ users: [userB], roles: ['member'] })
    })
    await act(async () => {
      first.resolve({ users: [userA], roles: ['admin'] })
    })

    expect(result.current.users).toEqual([userB])
  })
})

describe('useUsers mutations and panel guards', () => {
  it('saveRoles applies the server result to the list and closes the matching panel', async () => {
    const { result } = await mounted()
    const updated: AdminUser = { ...userA, roles: ['admin', 'member'] }
    setUserRoles.mockResolvedValue(updated)

    act(() => result.current.selectUser('A'))
    expect(result.current.selectedUser?.id).toBe('A')

    let outcome: unknown
    await act(async () => {
      outcome = await result.current.saveRoles('A', ['admin', 'member'])
    })

    expect(outcome).toEqual({ ok: true, current: true })
    expect(result.current.users.find((u) => u.id === 'A')?.roles).toEqual(['admin', 'member'])
    expect(result.current.selectedUser).toBeNull() // panel closed on success
  })

  it('still applies a mutation result but does NOT close the panel when the selection changed mid-flight', async () => {
    const { result } = await mounted()
    const pending = deferred<AdminUser>()
    setUserRoles.mockReturnValue(pending.promise)

    act(() => result.current.selectUser('A'))
    let outcomePromise!: Promise<unknown>
    act(() => {
      outcomePromise = result.current.saveRoles('A', ['admin'])
    })

    // Operator navigates to a different user before the save resolves.
    act(() => result.current.selectUser('B'))

    let outcome: unknown
    await act(async () => {
      pending.resolve({ ...userA, roles: ['admin'] })
      outcome = await outcomePromise
    })

    // List reflects the server outcome, but the panel stays on B and detached
    // content receives `current:false`, so it suppresses obsolete feedback.
    expect(outcome).toEqual({ ok: true, current: false })
    expect(result.current.users.find((u) => u.id === 'A')?.roles).toEqual(['admin'])
    expect(result.current.selectedUser?.id).toBe('B')
  })

  it('keeps the panel closed when a mutation resolves after manual close', async () => {
    const { result } = await mounted()
    const pending = deferred<AdminUser>()
    setUserRoles.mockReturnValue(pending.promise)

    act(() => result.current.selectUser('A'))
    let outcomePromise!: Promise<unknown>
    act(() => {
      outcomePromise = result.current.saveRoles('A', ['admin'])
    })
    act(() => result.current.selectUser(null))

    await act(async () => {
      pending.resolve({ ...userA, roles: ['admin'] })
      await outcomePromise
    })

    expect(result.current.selectedUser).toBeNull()
    expect(result.current.users.find((u) => u.id === 'A')?.roles).toEqual(['admin'])
  })

  it('setActive updates the row and keeps the panel open', async () => {
    const { result } = await mounted()
    setUserActive.mockResolvedValue({ ...userA, is_active: false })

    act(() => result.current.selectUser('A'))
    await act(async () => {
      await result.current.setActive('A', false)
    })

    expect(result.current.users.find((u) => u.id === 'A')?.is_active).toBe(false)
    expect(result.current.selectedUser?.id).toBe('A')
  })

  it('drops a refresh response made stale by a mutation', async () => {
    const { result } = await mounted()
    const refresh = deferred<{ users: AdminUser[]; roles: string[] }>()
    fetchUsersAndRoles.mockReturnValueOnce(refresh.promise)
    setUserActive.mockResolvedValue({ ...userA, is_active: false })

    act(() => {
      void result.current.reload()
    })
    await act(async () => {
      await result.current.setActive('A', false)
    })
    await act(async () => {
      // Old refresh snapshot says A was active. Mutation result must win.
      refresh.resolve({ users: [userA, userB], roles: ['admin', 'member'] })
    })

    expect(result.current.users.find((u) => u.id === 'A')?.is_active).toBe(false)
    expect(result.current.loading).toBe(false)
  })

  it('clears loading when a refresh becomes stale after a pending mutation settles', async () => {
    const { result } = await mounted()
    const mutation = deferred<AdminUser>()
    const refresh = deferred<{ users: AdminUser[]; roles: string[] }>()
    setUserActive.mockReturnValue(mutation.promise)
    fetchUsersAndRoles.mockReturnValueOnce(refresh.promise)

    let mutationPromise!: Promise<unknown>
    act(() => {
      mutationPromise = result.current.setActive('A', false)
    })
    act(() => {
      void result.current.reload()
    })
    expect(result.current.loading).toBe(true)

    await act(async () => {
      mutation.resolve({ ...userA, is_active: false })
      await mutationPromise
    })
    expect(result.current.loading).toBe(false)

    await act(async () => {
      refresh.resolve({ users: [userA, userB], roles: ['admin', 'member'] })
    })
    expect(result.current.users.find((u) => u.id === 'A')?.is_active).toBe(false)
    expect(result.current.loading).toBe(false)
  })

  it('serializes same-user mutations so full-user snapshots apply in dispatch order', async () => {
    const { result } = await mounted()
    const roles = deferred<AdminUser>()
    const status = deferred<AdminUser>()
    setUserRoles.mockReturnValue(roles.promise)
    setUserActive.mockReturnValue(status.promise)

    let rolesPromise!: Promise<unknown>
    let statusPromise!: Promise<unknown>
    act(() => {
      rolesPromise = result.current.saveRoles('A', ['admin'])
      statusPromise = result.current.setActive('A', false)
    })

    // Queue prevents the second API call until the first response has applied.
    expect(setUserActive).not.toHaveBeenCalled()
    await act(async () => {
      roles.resolve({ ...userA, roles: ['admin'] })
      await rolesPromise
    })
    expect(setUserActive).toHaveBeenCalledWith('A', false)

    await act(async () => {
      status.resolve({ ...userA, roles: ['admin'], is_active: false })
      await statusPromise
    })
    expect(result.current.users.find((u) => u.id === 'A')).toMatchObject({
      roles: ['admin'],
      is_active: false,
    })
  })

  it('removeUser drops the row and closes the matching panel', async () => {
    const { result } = await mounted()
    deleteUser.mockResolvedValue(undefined)

    act(() => result.current.selectUser('A'))
    await act(async () => {
      await result.current.removeUser('A')
    })

    expect(result.current.users.find((u) => u.id === 'A')).toBeUndefined()
    expect(result.current.selectedUser).toBeNull()
  })

  it('returns the stable backend detail on a failed mutation and leaves the list untouched', async () => {
    const { result } = await mounted()
    setUserActive.mockRejectedValue(
      new ApiError(409, 'Cannot deactivate the last active admin', {
        errorCode: 'last_active_admin',
      }),
    )

    act(() => result.current.selectUser('A'))
    let outcome: unknown
    await act(async () => {
      outcome = await result.current.setActive('A', false)
    })

    expect(outcome).toEqual({ ok: false, message: 'Cannot deactivate the last active admin', current: true })
    expect(result.current.users.find((u) => u.id === 'A')?.is_active).toBe(true)
    expect(result.current.selectedUser?.id).toBe('A') // panel stays open for retry
  })
})
