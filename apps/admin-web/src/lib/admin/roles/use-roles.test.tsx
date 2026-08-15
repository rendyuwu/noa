import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import { useRoles } from './use-roles'

vi.mock('./roles-api', () => ({
  fetchRolesAndTools: vi.fn(),
  fetchRoleTools: vi.fn(),
  createRole: vi.fn(),
  deleteRole: vi.fn(),
  setRoleTools: vi.fn(),
}))

const api = await import('./roles-api')
const fetchRolesAndTools = api.fetchRolesAndTools as unknown as Mock
const fetchRoleTools = api.fetchRoleTools as unknown as Mock
const createRole = api.createRole as unknown as Mock
const deleteRole = api.deleteRole as unknown as Mock
const setRoleTools = api.setRoleTools as unknown as Mock

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  fetchRolesAndTools.mockReset()
  fetchRoleTools.mockReset()
  createRole.mockReset()
  deleteRole.mockReset()
  setRoleTools.mockReset()
  fetchRolesAndTools.mockResolvedValue({
    roles: ['admin', 'member'],
    tools: ['tool.read', 'tool.write'],
  })
  fetchRoleTools.mockResolvedValue([])
})

async function mounted() {
  const view = renderHook(() => useRoles())
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

describe('useRoles loading', () => {
  it('loads roles and tools on mount and resolves per-role counts in the background', async () => {
    fetchRoleTools.mockResolvedValueOnce(['tool.read']).mockResolvedValueOnce([])
    const { result } = await mounted()
    expect(result.current.roles).toEqual(['admin', 'member'])
    expect(result.current.availableTools).toEqual(['tool.read', 'tool.write'])
    await waitFor(() => expect(result.current.roleToolCounts.admin).toBe(1))
    expect(result.current.roleToolCounts.member).toBe(0)
  })

  it('surfaces a load failure as loadError', async () => {
    fetchRolesAndTools.mockRejectedValueOnce(new ApiError(500, 'Upstream is down'))
    const { result } = renderHook(() => useRoles())
    await waitFor(() => expect(result.current.loadError).toBe('Upstream is down'))
  })

  it('ignores a stale load whose sequence is no longer current', async () => {
    const first = deferred<{ roles: string[]; tools: string[] }>()
    const second = deferred<{ roles: string[]; tools: string[] }>()
    fetchRolesAndTools.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = renderHook(() => useRoles())
    await act(async () => {
      void result.current.reload()
    })
    await act(async () => {
      second.resolve({ roles: ['member'], tools: [] })
    })
    await act(async () => {
      first.resolve({ roles: ['admin'], tools: [] })
    })

    expect(result.current.roles).toEqual(['member'])
  })
})

describe('useRoles mutations and panel guards', () => {
  it('createRole adds the role and seeds a zero count', async () => {
    const { result } = await mounted()
    createRole.mockResolvedValue(undefined)

    let outcome: unknown
    await act(async () => {
      outcome = await result.current.createRole('support')
    })

    expect(outcome).toEqual({ ok: true, current: true })
    expect(result.current.roles).toContain('support')
    expect(result.current.roleToolCounts.support).toBe(0)
  })

  it('returns the stable backend detail when create is rejected', async () => {
    const { result } = await mounted()
    createRole.mockRejectedValue(new ApiError(409, 'Role already exists', { errorCode: 'role_exists' }))

    let outcome: unknown
    await act(async () => {
      outcome = await result.current.createRole('admin')
    })

    expect(outcome).toEqual({ ok: false, message: 'Role already exists', current: true })
  })

  it('saveRoleTools applies the count and closes the matching panel', async () => {
    const { result } = await mounted()
    setRoleTools.mockResolvedValue(undefined)

    act(() => result.current.selectRole('admin'))
    await waitFor(() => expect(result.current.roleToolsLoading).toBe(false))

    let outcome: unknown
    await act(async () => {
      outcome = await result.current.saveRoleTools('admin', ['tool.read', 'tool.write'])
    })

    expect(outcome).toEqual({ ok: true, current: true })
    expect(result.current.roleToolCounts.admin).toBe(2)
    expect(result.current.selectedRole).toBeNull()
  })

  it('does NOT close the panel when the selection changed mid-flight, but still applies the count', async () => {
    const { result } = await mounted()
    const pending = deferred<void>()
    setRoleTools.mockReturnValue(pending.promise)

    act(() => result.current.selectRole('admin'))
    let outcomePromise!: Promise<unknown>
    act(() => {
      outcomePromise = result.current.saveRoleTools('admin', ['tool.read'])
    })
    act(() => result.current.selectRole('member'))

    let outcome: unknown
    await act(async () => {
      pending.resolve()
      outcome = await outcomePromise
    })

    expect(outcome).toEqual({ ok: true, current: false })
    expect(result.current.roleToolCounts.admin).toBe(1)
    expect(result.current.selectedRole).toBe('member')
  })

  it('deleteRole drops the row and closes the matching panel', async () => {
    const { result } = await mounted()
    deleteRole.mockResolvedValue(undefined)

    act(() => result.current.selectRole('member'))
    await waitFor(() => expect(result.current.roleToolsLoading).toBe(false))

    await act(async () => {
      await result.current.deleteRole('member')
    })

    expect(result.current.roles).not.toContain('member')
    expect(result.current.roleToolCounts.member).toBeUndefined()
    expect(result.current.selectedRole).toBeNull()
  })

  it('returns the stable backend detail on a failed delete and keeps the panel open for retry', async () => {
    const { result } = await mounted()
    deleteRole.mockRejectedValue(
      new ApiError(409, 'Role is still assigned to users', { errorCode: 'role_in_use' }),
    )

    act(() => result.current.selectRole('member'))
    await waitFor(() => expect(result.current.roleToolsLoading).toBe(false))

    let outcome: unknown
    await act(async () => {
      outcome = await result.current.deleteRole('member')
    })

    expect(outcome).toEqual({
      ok: false,
      message: 'Role is still assigned to users',
      current: true,
    })
    expect(result.current.roles).toContain('member')
    expect(result.current.selectedRole).toBe('member')
  })

  it('drops a refresh response made stale by a mutation', async () => {
    const { result } = await mounted()
    const refresh = deferred<{ roles: string[]; tools: string[] }>()
    fetchRolesAndTools.mockReturnValueOnce(refresh.promise)
    setRoleTools.mockResolvedValue(undefined)

    act(() => {
      void result.current.reload()
    })
    await act(async () => {
      await result.current.saveRoleTools('admin', ['tool.read', 'tool.write'])
    })
    await act(async () => {
      // Old refresh snapshot would reset admin's count to 0; the mutation wins.
      refresh.resolve({ roles: ['admin', 'member'], tools: ['tool.read', 'tool.write'] })
    })

    expect(result.current.roleToolCounts.admin).toBe(2)
    expect(result.current.loading).toBe(false)
  })

  it('does not let a stale background count overwrite a saved count', async () => {
    const staleCount = deferred<string[]>()
    // Mount count for admin is deferred (stale); member resolves immediately.
    fetchRoleTools.mockReturnValueOnce(staleCount.promise).mockResolvedValueOnce([])
    const { result } = await mounted()
    setRoleTools.mockResolvedValue(undefined)

    act(() => result.current.selectRole('admin'))
    // The panel's own tool fetch resolves via the default mock ([]) → count 0…
    await waitFor(() => expect(result.current.roleToolsLoading).toBe(false))
    await act(async () => {
      await result.current.saveRoleTools('admin', ['tool.read', 'tool.write'])
    })
    expect(result.current.roleToolCounts.admin).toBe(2)

    // The stale background count from mount lands late and must be ignored.
    await act(async () => {
      staleCount.resolve(['tool.read'])
    })
    expect(result.current.roleToolCounts.admin).toBe(2)
  })
})
