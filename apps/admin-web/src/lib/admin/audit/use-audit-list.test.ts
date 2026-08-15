import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'
import { AuthRedirectError } from '@/lib/auth/session'

import { useAuditList, type AuditListPage } from './use-audit-list'

type Item = { id: string }
type Filters = { toolName: string }

const DEFAULT_FILTERS: Filters = { toolName: '' }

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const buildQuery = (filters: Filters, cursor: string | null) => {
  const params = new URLSearchParams()
  if (filters.toolName) params.set('toolName', filters.toolName)
  if (cursor) params.set('cursor', cursor)
  return params.toString()
}

function setup(fetchPage: (query: string) => Promise<AuditListPage<Item>>) {
  return renderHook(() =>
    useAuditList<Item, Filters>({
      enabled: true,
      defaultFilters: DEFAULT_FILTERS,
      errorFallback: 'Unable to load',
      buildQuery,
      fetchPage,
    }),
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('useAuditList paging', () => {
  it('loads the first page on mount and exposes next availability', async () => {
    const fetchPage = vi.fn(async () => ({ items: [{ id: '1' }], nextCursor: 'CUR2' }))
    const { result } = setup(fetchPage)
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.items).toEqual([{ id: '1' }])
    expect(result.current.canGoNext).toBe(true)
    expect(result.current.canGoPrev).toBe(false)
    expect(result.current.pageIndex).toBe(1)
  })

  it('steps forward with the next cursor and back with the cursor stack', async () => {
    const calls: string[] = []
    const fetchPage = vi.fn(async (query: string) => {
      calls.push(query)
      const cursor = new URLSearchParams(query).get('cursor')
      if (cursor === 'CUR2') return { items: [{ id: '2' }], nextCursor: null }
      return { items: [{ id: '1' }], nextCursor: 'CUR2' }
    })
    const { result } = setup(fetchPage)
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.goNext()
    })
    await waitFor(() => expect(result.current.items).toEqual([{ id: '2' }]))
    expect(result.current.pageIndex).toBe(2)
    expect(result.current.canGoPrev).toBe(true)
    expect(result.current.canGoNext).toBe(false)
    expect(calls.some((q) => q.includes('cursor=CUR2'))).toBe(true)

    await act(async () => {
      result.current.goPrev()
    })
    await waitFor(() => expect(result.current.items).toEqual([{ id: '1' }]))
    expect(result.current.pageIndex).toBe(1)
    expect(result.current.canGoPrev).toBe(false)
  })

  it('resets to the first page when filters are applied', async () => {
    const fetchPage = vi.fn(async (query: string) => {
      const cursor = new URLSearchParams(query).get('cursor')
      if (cursor === 'CUR2') return { items: [{ id: '2' }], nextCursor: 'CUR3' }
      return { items: [{ id: '1' }], nextCursor: 'CUR2' }
    })
    const { result } = setup(fetchPage)
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.goNext()
    })
    await waitFor(() => expect(result.current.pageIndex).toBe(2))

    act(() => {
      result.current.setDraft({ toolName: 'whm_create_account' })
    })
    await act(async () => {
      result.current.applyFilters()
    })
    await waitFor(() => expect(result.current.pageIndex).toBe(1))
    expect(result.current.canGoPrev).toBe(false)
    // The applied filter is now part of the query.
    const lastQuery = fetchPage.mock.calls.at(-1)?.[0] as string
    expect(lastQuery).toContain('toolName=whm_create_account')
    expect(lastQuery).not.toContain('cursor=')
  })

  it('clears filters back to the default first page', async () => {
    const fetchPage = vi.fn(async () => ({ items: [{ id: '1' }], nextCursor: null }))
    const { result } = setup(fetchPage)
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.setDraft({ toolName: 'x' }))
    await act(async () => result.current.applyFilters())
    await act(async () => result.current.clearFilters())

    await waitFor(() => expect(result.current.activeFilters).toEqual(DEFAULT_FILTERS))
    expect(result.current.draft).toEqual(DEFAULT_FILTERS)
  })

  it('surfaces a stable error and recovers on reload', async () => {
    const fetchPage = vi
      .fn<(query: string) => Promise<AuditListPage<Item>>>()
      .mockRejectedValueOnce(new ApiError(500, 'boom'))
      .mockResolvedValueOnce({ items: [{ id: '1' }], nextCursor: null })
    const { result } = setup(fetchPage)
    await waitFor(() => expect(result.current.loadError).toBe('boom'))
    expect(result.current.items).toEqual([])

    await act(async () => {
      result.current.reload()
    })
    await waitFor(() => expect(result.current.loadError).toBeNull())
    expect(result.current.items).toEqual([{ id: '1' }])
  })

  it('swallows a 401 redirect without surfacing an error', async () => {
    const fetchPage = vi.fn(async () => {
      throw new AuthRedirectError('session_expired')
    })
    const { result } = setup(fetchPage)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.loadError).toBeNull()
  })

  it('drops a stale page whose response lands after a newer one', async () => {
    const first = deferred<AuditListPage<Item>>()
    const second = deferred<AuditListPage<Item>>()
    const fetchPage = vi
      .fn<(query: string) => Promise<AuditListPage<Item>>>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    const { result } = setup(fetchPage)

    // Reload before the mount load resolves; the reload (newer) resolves first.
    act(() => result.current.reload())
    await act(async () => {
      second.resolve({ items: [{ id: 'new' }], nextCursor: null })
      await second.promise
    })
    await act(async () => {
      first.resolve({ items: [{ id: 'stale' }], nextCursor: null })
      await first.promise
    })
    // The stale mount response must not overwrite the newer reload result.
    expect(result.current.items).toEqual([{ id: 'new' }])
  })
})

describe('useAuditList enablement', () => {
  it('does not fetch while disabled (the inactive tab)', async () => {
    const fetchPage = vi.fn(async () => ({ items: [], nextCursor: null }))
    renderHook(() =>
      useAuditList<Item, Filters>({
        enabled: false,
        defaultFilters: DEFAULT_FILTERS,
        errorFallback: 'Unable to load',
        buildQuery,
        fetchPage,
      }),
    )
    await Promise.resolve()
    expect(fetchPage).not.toHaveBeenCalled()
  })
})
