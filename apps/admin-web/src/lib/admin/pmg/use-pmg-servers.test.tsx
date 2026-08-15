import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'
import { AuthRedirectError } from '@/lib/auth/session'

import type { PmgServer } from './types'
import { usePmgServers } from './use-pmg-servers'

vi.mock('./pmg-api', () => ({
  fetchPmgServers: vi.fn(),
  createPmgServer: vi.fn(),
  updatePmgServer: vi.fn(),
  deletePmgServer: vi.fn(),
  validatePmgServer: vi.fn(),
}))

const api = await import('./pmg-api')
const fetchPmgServers = api.fetchPmgServers as unknown as Mock
const createPmgServer = api.createPmgServer as unknown as Mock
const updatePmgServer = api.updatePmgServer as unknown as Mock
const deletePmgServer = api.deletePmgServer as unknown as Mock
const validatePmgServer = api.validatePmgServer as unknown as Mock

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const serverA: PmgServer = {
  id: 'a',
  name: 'alpha',
  ssh_host: 'a.example.com',
  ssh_username: null,
  ssh_port: null,
  ssh_host_key_fingerprint: null,
  has_ssh_password: false,
  has_ssh_private_key: true,
}
const serverB: PmgServer = { ...serverA, id: 'b', name: 'bravo', ssh_host: 'b.example.com' }

beforeEach(() => {
  fetchPmgServers.mockReset()
  createPmgServer.mockReset()
  updatePmgServer.mockReset()
  deletePmgServer.mockReset()
  validatePmgServer.mockReset()
  fetchPmgServers.mockResolvedValue([serverB, serverA])
})

async function mounted() {
  const view = renderHook(() => usePmgServers())
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

describe('usePmgServers loading', () => {
  it('loads and sorts servers by name on mount', async () => {
    const { result } = await mounted()
    expect(result.current.servers.map((s) => s.id)).toEqual(['a', 'b'])
  })

  it('surfaces a stable error and recovers on reload', async () => {
    fetchPmgServers.mockReset()
    fetchPmgServers.mockRejectedValueOnce(new ApiError(500, 'boom'))
    const { result } = await mounted()
    expect(result.current.loadError).toBe('boom')

    fetchPmgServers.mockResolvedValueOnce([serverA])
    await act(async () => {
      await result.current.reload()
    })
    expect(result.current.loadError).toBeNull()
    expect(result.current.servers.map((s) => s.id)).toEqual(['a'])
  })

  it('drops a stale load whose response lands after a newer one', async () => {
    fetchPmgServers.mockReset()
    const first = deferred<PmgServer[]>()
    const second = deferred<PmgServer[]>()
    fetchPmgServers.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = renderHook(() => usePmgServers())
    // Kick a manual reload before the mount load resolves.
    let reloadPromise: Promise<void>
    act(() => {
      reloadPromise = result.current.reload()
    })
    // Newer (reload) resolves first with [serverA]; older mount load resolves last.
    await act(async () => {
      second.resolve([serverA])
      await reloadPromise
    })
    await act(async () => {
      first.resolve([serverB])
      await first.promise
    })
    // The stale mount response must not overwrite the newer reload result.
    expect(result.current.servers.map((s) => s.id)).toEqual(['a'])
  })
})

describe('usePmgServers mutations', () => {
  it('applies the created server to the list', async () => {
    const created: PmgServer = { ...serverA, id: 'c', name: 'charlie' }
    createPmgServer.mockResolvedValueOnce(created)
    const { result } = await mounted()
    await act(async () => {
      const res = await result.current.createServer({ name: 'charlie' })
      expect(res.ok).toBe(true)
    })
    expect(result.current.servers.map((s) => s.name)).toEqual(['alpha', 'bravo', 'charlie'])
  })

  it('preserves the selection through an update and drops the stale validation badge', async () => {
    updatePmgServer.mockResolvedValueOnce({ ...serverA, name: 'alpha2' })
    validatePmgServer.mockResolvedValueOnce({ ok: true, message: 'ok' })
    const { result } = await mounted()
    act(() => result.current.selectServer('a'))
    await act(async () => {
      await result.current.validateServer('a')
    })
    expect(result.current.validateResultById.a?.ok).toBe(true)
    await act(async () => {
      await result.current.updateServer('a', { name: 'alpha2' })
    })
    // Update invalidates the prior validation result.
    expect(result.current.validateResultById.a).toBeUndefined()
    expect(result.current.servers.find((s) => s.id === 'a')?.name).toBe('alpha2')
  })

  it('returns current=false for a delete after the drawer moved to another server', async () => {
    const del = deferred<void>()
    deletePmgServer.mockReturnValueOnce(del.promise)
    const { result } = await mounted()
    act(() => result.current.selectServer('a'))

    let deletePromise: Promise<{ ok: boolean; current: boolean }>
    act(() => {
      deletePromise = result.current.deleteServer('a') as never
    })
    // Move the drawer to B while the delete is in flight (close-while-mutating).
    act(() => result.current.selectServer('b'))
    await act(async () => {
      del.resolve()
      const res = await deletePromise
      expect(res.current).toBe(false)
    })
    // Row is still removed (server-authoritative), and the drawer stayed on B.
    expect(result.current.servers.map((s) => s.id)).toEqual(['b'])
    expect(result.current.selectedServer?.id).toBe('b')
  })

  it('always records a server-authoritative validation result, even after the drawer closes', async () => {
    const val = deferred<{ ok: boolean; message: string }>()
    validatePmgServer.mockReturnValueOnce(val.promise)
    const { result } = await mounted()
    act(() => result.current.selectServer('a'))

    let validatePromise: Promise<{ ok: boolean; current: boolean }>
    act(() => {
      validatePromise = result.current.validateServer('a') as never
    })
    act(() => result.current.selectServer(null))
    await act(async () => {
      val.resolve({ ok: false, message: 'SSH authentication failed' })
      const res = await validatePromise
      expect(res.current).toBe(false)
    })
    // The badge still updates the table row even though the drawer closed.
    expect(result.current.validateResultById.a?.message).toBe('SSH authentication failed')
  })

  it('serializes same-server mutations in dispatch order', async () => {
    const first = deferred<PmgServer>()
    const second = deferred<PmgServer>()
    updatePmgServer.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { result } = await mounted()

    let p1: Promise<unknown>
    let p2: Promise<unknown>
    act(() => {
      p1 = result.current.updateServer('a', { name: 'first' })
      p2 = result.current.updateServer('a', { name: 'second' })
    })
    // The first task runs on the next microtask; while it stays pending (deferred),
    // the second must not start — that is the serialization guarantee.
    await waitFor(() => expect(updatePmgServer).toHaveBeenCalledTimes(1))
    expect(updatePmgServer).toHaveBeenCalledTimes(1)
    await act(async () => {
      first.resolve({ ...serverA, name: 'first' })
      await p1
    })
    await waitFor(() => expect(updatePmgServer).toHaveBeenCalledTimes(2))
    await act(async () => {
      second.resolve({ ...serverA, name: 'second' })
      await p2
    })
    expect(result.current.servers.find((s) => s.id === 'a')?.name).toBe('second')
  })

  it('swallows a 401 as a redirecting result', async () => {
    updatePmgServer.mockRejectedValueOnce(new AuthRedirectError('session_expired'))
    const { result } = await mounted()
    await act(async () => {
      const res = await result.current.updateServer('a', { name: 'x' })
      expect(res).toEqual({ ok: false, message: '', current: false })
    })
  })
})
