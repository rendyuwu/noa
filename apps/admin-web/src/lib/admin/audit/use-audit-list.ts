'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import { toMessage } from '@/lib/admin/shared/error-message'
import { isAuthRedirectError } from '@/lib/auth/session'

// Generic server-paged, server-filtered list controller for the audit surfaces
// (issue #111). Both audit tabs (action-requests, tool-runs) share this: the
// DataTable stays presentational while THIS owns the server query state and the
// standalone cursor pagination the API drives. It carries the race guard the
// verticals need — every load carries a sequence number, and a response whose
// sequence is no longer current is dropped, so a slow page never lands after a
// filter change, a page step, or unmount. A 401 is swallowed (fetchWithAuth
// already cleared identity and started the session-expiry navigation).
//
// Cursor model: `cursor` is the current page's cursor (null = first page);
// `cursorStack` holds the cursors of the pages behind us. Next pushes the
// current cursor and moves to `nextCursor`; Prev pops back. Applying or clearing
// filters resets to the first page — a stale cursor against a new filter set
// would read as a broken page.

export type AuditListPage<TItem> = {
  items: TItem[]
  nextCursor?: string | null
}

export type AuditListController<TItem, TFilters> = {
  draft: TFilters
  setDraft: Dispatch<SetStateAction<TFilters>>
  activeFilters: TFilters
  items: TItem[]
  loading: boolean
  loadError: string | null
  pageIndex: number
  canGoPrev: boolean
  canGoNext: boolean
  applyFilters: () => void
  clearFilters: () => void
  goPrev: () => void
  goNext: () => void
  reload: () => void
}

export function useAuditList<TItem, TFilters>({
  enabled,
  defaultFilters,
  errorFallback,
  buildQuery,
  fetchPage,
}: {
  enabled: boolean
  defaultFilters: TFilters
  errorFallback: string
  buildQuery: (filters: TFilters, cursor: string | null) => string
  fetchPage: (query: string) => Promise<AuditListPage<TItem>>
}): AuditListController<TItem, TFilters> {
  const [draft, setDraft] = useState<TFilters>(defaultFilters)
  const [filters, setFilters] = useState<TFilters>(defaultFilters)
  const [items, setItems] = useState<TItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const loadSeq = useRef(0)
  const query = useMemo(() => buildQuery(filters, cursor), [buildQuery, filters, cursor])

  // The load never sets state synchronously — the sequence number is bumped in
  // the caller and every `setState` lands only after the fetch awaits, so the
  // effect body stays side-effect free at render time. Loading is switched on by
  // the user-triggered navigation handlers (and the initial state), never inside
  // the effect. A response whose sequence is no longer current is dropped.
  const runLoad = useCallback(
    async (seq: number) => {
      try {
        const page = await fetchPage(query)
        if (seq !== loadSeq.current) return
        setItems(page.items)
        setNextCursor(page.nextCursor ?? null)
        setLoadError(null)
      } catch (error) {
        if (seq !== loadSeq.current || isAuthRedirectError(error)) return
        setLoadError(toMessage(error, errorFallback))
        setItems([])
        setNextCursor(null)
      } finally {
        if (seq === loadSeq.current) setLoading(false)
      }
    },
    [errorFallback, fetchPage, query],
  )

  useEffect(() => {
    if (!enabled) return
    const seq = ++loadSeq.current
    void runLoad(seq)
    return () => {
      // Drop any in-flight load whose query no longer matches (or on unmount).
      loadSeq.current += 1
    }
  }, [enabled, runLoad])

  const applyFilters = useCallback(() => {
    setLoading(true)
    setLoadError(null)
    setCursor(null)
    setCursorStack([])
    setFilters(draft)
  }, [draft])

  const clearFilters = useCallback(() => {
    setLoading(true)
    setLoadError(null)
    setCursor(null)
    setCursorStack([])
    setDraft(defaultFilters)
    setFilters(defaultFilters)
  }, [defaultFilters])

  const goPrev = useCallback(() => {
    if (cursorStack.length === 0) return
    setLoading(true)
    setCursor(cursorStack[cursorStack.length - 1] ?? null)
    setCursorStack((prev) => prev.slice(0, -1))
  }, [cursorStack])

  const goNext = useCallback(() => {
    if (!nextCursor) return
    setLoading(true)
    setCursorStack((prev) => [...prev, cursor])
    setCursor(nextCursor)
  }, [cursor, nextCursor])

  const reload = useCallback(() => {
    setLoading(true)
    const seq = ++loadSeq.current
    void runLoad(seq)
  }, [runLoad])

  return {
    draft,
    setDraft,
    activeFilters: filters,
    items,
    loading,
    loadError,
    pageIndex: cursorStack.length + 1,
    canGoPrev: cursorStack.length > 0,
    canGoNext: Boolean(nextCursor),
    applyFilters,
    clearFilters,
    goPrev,
    goNext,
    reload,
  }
}
