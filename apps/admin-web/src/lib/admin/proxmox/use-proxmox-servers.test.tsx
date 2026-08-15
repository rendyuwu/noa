import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'
import { AuthRedirectError } from '@/lib/auth/session'

import type { ProxmoxServer } from './types'
import { useProxmoxServers } from './use-proxmox-servers'

vi.mock('./proxmox-api', () => ({
  fetchProxmoxServers: vi.fn(),
  createProxmoxServer: vi.fn(),
  updateProxmoxServer: vi.fn(),
  deleteProxmoxServer: vi.fn(),
  validateProxmoxServer: vi.fn(),
}))

const api = await import('./proxmox-api')
const fetchProxmoxServers = api.fetchProxmoxServers as unknown as Mock
const createProxmoxServer = api.createProxmoxServer as unknown as Mock
const updateProxmoxServer = api.updateProxmoxServer as unknown as Mock
const deleteProxmoxServer = api.deleteProxmoxServer as unknown as Mock
const validateProxmoxServer = api.validateProxmoxServer as unknown as Mock

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const serverA: ProxmoxServer = {
  id: 'a',
  name: 'alpha',
  base_url: 'https://a:8006',
  api_token_id: 'root@pam!noa',
  has_api_token_secret: true,
  verify_ssl: false,
}
const serverB: ProxmoxServer = { ...serverA, id: 'b', name: 'bravo', base_url: 'https://b:8006' }

beforeEach(() => {
  fetchProxmoxServers.mockReset()
  createProxmoxServer.mockReset()
  updateProxmoxServer.mockReset()
  deleteProxmoxServer.mockReset()
  validateProxmoxServer.mockReset()
  fetchProxmoxServers.mockResolvedValue([serverB, serverA])
})

async function mounted() {
  const view = renderHook(() => useProxmoxServers())
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

describe('useProxmoxServers loading', () => {
  it('loads and sorts servers by name on mount', async () => {
    const { result } = await mounted()
    expect(result.current.servers.map((s) => s.id)).toEqual(['a', 'b'])
  })

  it('surfaces a stable error and recovers on reload', async () => {
    fetchProxmoxServers.mockReset()
    fetchProxmoxServers.mockRejectedValueOnce(new ApiError(500, 'boom'))
    const { result } = await mounted()
    expect(result.current.loadError).toBe('boom')

    fetchProxmoxServers.mockResolvedValueOnce([serverA])
    await act(async () => {
      await result.current.reload()
    })
    expect(result.current.loadError).toBeNull()
    expect(result.current.servers.map((s) => s.id)).toEqual(['a'])
  })

  it('drops a stale load whose response lands after a newer one', async () => {
    fetchProxmoxServers.mockReset()
    const first = deferred<ProxmoxServer[]>()
    const second = deferred<ProxmoxServer[]>()
    fetchProxmoxServers.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = renderHook(() => useProxmoxServers())
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

describe('useProxmoxServers mutations and races', () => {
  it('applies the created server to the list', async () => {
    const created: ProxmoxServer = { ...serverA, id: 'c', name: 'charlie' }
    createProxmoxServer.mockResolvedValueOnce(created)
    const { result } = await mounted()
    await act(async () => {
      const res = await result.current.createServer({ name: 'charlie' })
      expect(res.ok).toBe(true)
    })
    expect(result.current.servers.map((s) => s.name)).toEqual(['alpha', 'bravo', 'charlie'])
  })

  it('preserves the selection through an update and drops the stale validation badge', async () => {
    updateProxmoxServer.mockResolvedValueOnce({ ...serverA, name: 'alpha2' })
    validateProxmoxServer.mockResolvedValueOnce({ ok: true, message: 'ok' })
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
    deleteProxmoxServer.mockReturnValueOnce(del.promise)
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
    validateProxmoxServer.mockReturnValueOnce(val.promise)
    const { result } = await mounted()
    act(() => result.current.selectServer('a'))

    let validatePromise: Promise<{ ok: boolean; current: boolean }>
    act(() => {
      validatePromise = result.current.validateServer('a') as never
    })
    // Close the drawer while validate is in flight (close-while-validating).
    act(() => result.current.selectServer(null))
    await act(async () => {
      val.resolve({ ok: false, message: 'Proxmox validation failed' })
      const res = await validatePromise
      expect(res.current).toBe(false)
    })
    // The badge still updates the table row even though the drawer closed.
    expect(result.current.validateResultById.a?.message).toBe('Proxmox validation failed')
  })

  it('serializes same-server mutations in dispatch order', async () => {
    const first = deferred<ProxmoxServer>()
    const second = deferred<ProxmoxServer>()
    updateProxmoxServer.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { result } = await mounted()

    let p1: Promise<unknown>
    let p2: Promise<unknown>
    act(() => {
      p1 = result.current.updateServer('a', { name: 'first' })
      p2 = result.current.updateServer('a', { name: 'second' })
    })
    // The first task runs on the next microtask; while it stays pending (deferred),
    // the second must not start — that is the serialization guarantee.
    await waitFor(() => expect(updateProxmoxServer).toHaveBeenCalledTimes(1))
    expect(updateProxmoxServer).toHaveBeenCalledTimes(1)
    await act(async () => {
      first.resolve({ ...serverA, name: 'first' })
      await p1
    })
    await waitFor(() => expect(updateProxmoxServer).toHaveBeenCalledTimes(2))
    await act(async () => {
      second.resolve({ ...serverA, name: 'second' })
      await p2
    })
    expect(result.current.servers.find((s) => s.id === 'a')?.name).toBe('second')
  })

  it('drops a refresh whose request straddles a mutation (mutation stays authoritative)', async () => {
    const load = deferred<ProxmoxServer[]>()
    const { result } = await mounted()

    // Start a reload that will resolve with a stale list...
    fetchProxmoxServers.mockReturnValueOnce(load.promise)
    let reloadPromise: Promise<void>
    act(() => {
      reloadPromise = result.current.reload()
    })
    // ...then a create bumps the mutation epoch mid-flight.
    createProxmoxServer.mockResolvedValueOnce({ ...serverA, id: 'c', name: 'charlie' })
    await act(async () => {
      await result.current.createServer({ name: 'charlie' })
    })
    await act(async () => {
      // The straddling reload resolves last with a list that never saw the create.
      load.resolve([serverA, serverB])
      await reloadPromise
    })
    // The mutation snapshot (including charlie) stays authoritative.
    expect(result.current.servers.map((s) => s.id).sort()).toEqual(['a', 'b', 'c'])
  })

  it('swallows a 401 as a redirecting result', async () => {
    updateProxmoxServer.mockRejectedValueOnce(new AuthRedirectError('session_expired'))
    const { result } = await mounted()
    await act(async () => {
      const res = await result.current.updateServer('a', { name: 'x' })
      expect(res).toEqual({ ok: false, message: '', current: false })
    })
  })
})
