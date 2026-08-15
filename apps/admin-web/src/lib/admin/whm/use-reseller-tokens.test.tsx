import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import type { WhmServerToken } from './types'
import { useResellerTokens } from './use-reseller-tokens'

vi.mock('./whm-api', () => ({
  fetchWhmServerTokens: vi.fn(),
  createWhmServerToken: vi.fn(),
  updateWhmServerToken: vi.fn(),
  deleteWhmServerToken: vi.fn(),
  validateWhmServerToken: vi.fn(),
}))

const api = await import('./whm-api')
const fetchWhmServerTokens = api.fetchWhmServerTokens as unknown as Mock
const createWhmServerToken = api.createWhmServerToken as unknown as Mock
const updateWhmServerToken = api.updateWhmServerToken as unknown as Mock
const deleteWhmServerToken = api.deleteWhmServerToken as unknown as Mock
const validateWhmServerToken = api.validateWhmServerToken as unknown as Mock

const token: WhmServerToken = {
  id: 'token-1',
  server_id: 'server-1',
  owner_username: 'reseller1',
  api_username: 'reseller1',
  updated_at: '2026-01-02T03:04:05.000Z',
}

beforeEach(() => {
  fetchWhmServerTokens.mockReset()
  createWhmServerToken.mockReset()
  updateWhmServerToken.mockReset()
  deleteWhmServerToken.mockReset()
  validateWhmServerToken.mockReset()
  fetchWhmServerTokens.mockResolvedValue([token])
})

describe('useResellerTokens', () => {
  it('does not load until load() is called', async () => {
    renderHook(() => useResellerTokens('server-1'))
    expect(fetchWhmServerTokens).not.toHaveBeenCalled()
  })

  it('loads once and never refetches on repeated load() (re-expand)', async () => {
    fetchWhmServerTokens.mockResolvedValue([])
    const { result } = renderHook(() => useResellerTokens('server-1'))
    await act(async () => {
      await result.current.load()
    })
    await act(async () => {
      await result.current.load()
      await result.current.load()
    })
    expect(fetchWhmServerTokens).toHaveBeenCalledTimes(1)
    expect(result.current.hasLoaded).toBe(true)
  })

  it('surfaces a stable load error and retries on reload()', async () => {
    fetchWhmServerTokens.mockReset()
    fetchWhmServerTokens.mockRejectedValueOnce(new ApiError(500, 'no tokens'))
    const { result } = renderHook(() => useResellerTokens('server-1'))
    await act(async () => {
      await result.current.load()
    })
    expect(result.current.loadError).toBe('no tokens')

    fetchWhmServerTokens.mockResolvedValueOnce([token])
    await act(async () => {
      await result.current.reload()
    })
    expect(result.current.loadError).toBeNull()
    expect(result.current.tokens).toHaveLength(1)
  })

  it('creates a token and applies it to the list', async () => {
    fetchWhmServerTokens.mockResolvedValue([])
    createWhmServerToken.mockResolvedValueOnce(token)
    const { result } = renderHook(() => useResellerTokens('server-1'))
    await act(async () => {
      await result.current.load()
    })
    await act(async () => {
      const res = await result.current.createToken({
        owner_username: 'reseller1',
        api_username: 'reseller1',
        api_token: 'SECRET',
      })
      expect(res.ok).toBe(true)
    })
    expect(result.current.tokens.map((t) => t.owner_username)).toEqual(['reseller1'])
  })

  it('drops the validation badge after a rotate', async () => {
    validateWhmServerToken.mockResolvedValueOnce({ ok: true, message: 'ok' })
    updateWhmServerToken.mockResolvedValueOnce({ ...token, updated_at: '2026-02-02T00:00:00Z' })
    const { result } = renderHook(() => useResellerTokens('server-1'))
    await act(async () => {
      await result.current.load()
    })
    await act(async () => {
      await result.current.validateToken('token-1')
    })
    expect(result.current.validateResultById['token-1']?.ok).toBe(true)
    await act(async () => {
      await result.current.rotateToken('token-1', { api_username: 'reseller1', api_token: 'NEW' })
    })
    expect(result.current.validateResultById['token-1']).toBeUndefined()
  })

  it('serializes same-token mutations in dispatch order', async () => {
    fetchWhmServerTokens.mockResolvedValue([token])
    let resolveFirst!: () => void
    const first = new Promise<WhmServerToken>((res) => {
      resolveFirst = () => res({ ...token, api_username: 'first' })
    })
    updateWhmServerToken.mockReturnValueOnce(first).mockResolvedValueOnce({
      ...token,
      api_username: 'second',
    })
    const { result } = renderHook(() => useResellerTokens('server-1'))
    await act(async () => {
      await result.current.load()
    })
    let p1: Promise<unknown>
    let p2: Promise<unknown>
    act(() => {
      p1 = result.current.rotateToken('token-1', { api_username: 'first' })
      p2 = result.current.rotateToken('token-1', { api_username: 'second' })
    })
    // The first task runs on the next microtask; while it stays pending, the
    // second must not start — that is the serialization guarantee.
    await waitFor(() => expect(updateWhmServerToken).toHaveBeenCalledTimes(1))
    expect(updateWhmServerToken).toHaveBeenCalledTimes(1)
    await act(async () => {
      resolveFirst()
      await p1
    })
    await waitFor(() => expect(updateWhmServerToken).toHaveBeenCalledTimes(2))
    await act(async () => {
      await p2
    })
    expect(result.current.tokens[0]?.api_username).toBe('second')
  })

  it('removes a token on delete', async () => {
    deleteWhmServerToken.mockResolvedValueOnce(undefined)
    const { result } = renderHook(() => useResellerTokens('server-1'))
    await act(async () => {
      await result.current.load()
    })
    await act(async () => {
      const res = await result.current.deleteToken('token-1')
      expect(res.ok).toBe(true)
    })
    expect(result.current.tokens).toHaveLength(0)
  })
})
