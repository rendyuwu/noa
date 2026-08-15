'use client'

import { useCallback, useRef, useState } from 'react'

import { toMessage } from '@/lib/admin/shared/error-message'
import { isAuthRedirectError } from '@/lib/auth/session'

import type { ValidateWhmServerResponse, WhmServerToken } from './types'
import {
  createWhmServerToken as apiCreate,
  deleteWhmServerToken as apiDelete,
  fetchWhmServerTokens,
  updateWhmServerToken as apiUpdate,
  validateWhmServerToken as apiValidate,
} from './whm-api'

// The reseller-tokens data controller (issue #108). Scoped to one server; the
// detail drawer mounts it keyed by server id so switching servers re-seeds it.
// Race guards mirror the servers controller, plus a load-once guard so an empty
// list is not re-fetched on re-expand:
//
//  - load-once: hasLoaded gates the lazy load so re-expanding never refetches.
//  - stale load: a load sequence drops a superseded response.
//  - duplicate mutation: per-token mutation queue serializes create/rotate/
//    delete/validate so authoritative snapshots apply in dispatch order.
//
// The token value is write-only: it lives only in the dialog form, is sent in
// request bodies, and is never returned by the API or stored here.

export type MutationResult =
  | { ok: true }
  | { ok: false; message: string }

const REDIRECTING: MutationResult = { ok: false, message: '' }

const sortTokens = (tokens: WhmServerToken[]): WhmServerToken[] =>
  tokens.slice().sort((a, b) => a.owner_username.localeCompare(b.owner_username))

export type ResellerTokensController = {
  tokens: WhmServerToken[]
  loading: boolean
  loadError: string | null
  hasLoaded: boolean
  load: () => Promise<void>
  reload: () => Promise<void>
  validateResultById: Record<string, ValidateWhmServerResponse>
  validateBusyId: string | null
  deleteBusyId: string | null
  createToken: (body: {
    owner_username: string
    api_username: string
    api_token: string
  }) => Promise<MutationResult>
  rotateToken: (
    tokenId: string,
    body: { api_username: string; api_token?: string },
  ) => Promise<MutationResult>
  deleteToken: (tokenId: string) => Promise<MutationResult>
  validateToken: (tokenId: string) => Promise<MutationResult>
}

export function useResellerTokens(serverId: string): ResellerTokensController {
  const [tokens, setTokens] = useState<WhmServerToken[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [validateResultById, setValidateResultById] = useState<
    Record<string, ValidateWhmServerResponse>
  >({})
  const [validateBusyId, setValidateBusyId] = useState<string | null>(null)
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null)

  const loadSeq = useRef(0)
  const hasLoadedRef = useRef(false)
  const mutationQueues = useRef(new Map<string, Promise<void>>())

  const enqueueMutation = useCallback(<T,>(tokenId: string, task: () => Promise<T>): Promise<T> => {
    const previous = mutationQueues.current.get(tokenId) ?? Promise.resolve()
    const result = previous.catch(() => undefined).then(task)
    const settled = result.then(
      () => undefined,
      () => undefined,
    )
    mutationQueues.current.set(tokenId, settled)
    void settled.finally(() => {
      if (mutationQueues.current.get(tokenId) === settled) mutationQueues.current.delete(tokenId)
    })
    return result
  }, [])

  const runLoad = useCallback(
    async (seq: number) => {
      setLoading(true)
      setLoadError(null)
      try {
        const next = await fetchWhmServerTokens(serverId)
        if (seq !== loadSeq.current) return
        setTokens(sortTokens(next))
        hasLoadedRef.current = true
        setHasLoaded(true)
      } catch (error) {
        if (seq !== loadSeq.current || isAuthRedirectError(error)) return
        setLoadError(toMessage(error, 'Unable to load reseller tokens'))
      } finally {
        if (seq === loadSeq.current) setLoading(false)
      }
    },
    [serverId],
  )

  // Lazy load, gated so an already-loaded (even empty) list never refetches on
  // re-expand.
  const load = useCallback(async () => {
    if (hasLoadedRef.current || loading) return
    const seq = ++loadSeq.current
    await runLoad(seq)
  }, [loading, runLoad])

  // Explicit retry after a load failure.
  const reload = useCallback(async () => {
    const seq = ++loadSeq.current
    await runLoad(seq)
  }, [runLoad])

  const createToken = useCallback(
    async (body: {
      owner_username: string
      api_username: string
      api_token: string
    }): Promise<MutationResult> => {
      try {
        const token = await apiCreate(serverId, body)
        setTokens((prev) => sortTokens([...prev.filter((t) => t.id !== token.id), token]))
        return { ok: true }
      } catch (error) {
        if (isAuthRedirectError(error)) return REDIRECTING
        return { ok: false, message: toMessage(error, 'Unable to save reseller token') }
      }
    },
    [serverId],
  )

  const rotateToken = useCallback(
    async (
      tokenId: string,
      body: { api_username: string; api_token?: string },
    ): Promise<MutationResult> => {
      return enqueueMutation(tokenId, async () => {
        try {
          const token = await apiUpdate(serverId, tokenId, body)
          setTokens((prev) => sortTokens(prev.map((t) => (t.id === token.id ? token : t))))
          // A rotated credential invalidates any stored validation badge.
          setValidateResultById((prev) => {
            const next = { ...prev }
            delete next[tokenId]
            return next
          })
          return { ok: true }
        } catch (error) {
          if (isAuthRedirectError(error)) return REDIRECTING
          return { ok: false, message: toMessage(error, 'Unable to save reseller token') }
        }
      })
    },
    [enqueueMutation, serverId],
  )

  const deleteToken = useCallback(
    async (tokenId: string): Promise<MutationResult> => {
      setDeleteBusyId(tokenId)
      return enqueueMutation(tokenId, async () => {
        try {
          await apiDelete(serverId, tokenId)
          setTokens((prev) => prev.filter((t) => t.id !== tokenId))
          setValidateResultById((prev) => {
            const next = { ...prev }
            delete next[tokenId]
            return next
          })
          return { ok: true }
        } catch (error) {
          if (isAuthRedirectError(error)) return REDIRECTING
          return { ok: false, message: toMessage(error, 'Unable to delete reseller token') }
        } finally {
          setDeleteBusyId((prev) => (prev === tokenId ? null : prev))
        }
      })
    },
    [enqueueMutation, serverId],
  )

  const validateToken = useCallback(
    async (tokenId: string): Promise<MutationResult> => {
      setValidateBusyId(tokenId)
      return enqueueMutation(tokenId, async () => {
        try {
          const result = await apiValidate(serverId, tokenId)
          setValidateResultById((prev) => ({ ...prev, [tokenId]: result }))
          return result.ok
            ? { ok: true }
            : { ok: false, message: result.message || 'Validation failed' }
        } catch (error) {
          if (isAuthRedirectError(error)) return REDIRECTING
          const message = toMessage(error, 'Validation failed')
          setValidateResultById((prev) => ({ ...prev, [tokenId]: { ok: false, message } }))
          return { ok: false, message }
        } finally {
          setValidateBusyId((prev) => (prev === tokenId ? null : prev))
        }
      })
    },
    [enqueueMutation, serverId],
  )

  return {
    tokens,
    loading,
    loadError,
    hasLoaded,
    load,
    reload,
    validateResultById,
    validateBusyId,
    deleteBusyId,
    createToken,
    rotateToken,
    deleteToken,
    validateToken,
  }
}
