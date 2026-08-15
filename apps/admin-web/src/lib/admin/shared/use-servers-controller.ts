'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { toMessage } from '@/lib/admin/shared/error-message'
import { isAuthRedirectError } from '@/lib/auth/session'

import type {
  MutationResult,
  ServerLike,
  ServersController,
  ServersControllerApi,
  ValidateResultLike,
} from './servers-controller-types'

// The shared admin "servers" data controller, extracted from the byte-identical
// WHM (#108), Proxmox (#109), and PMG (#110) hooks (#131 finding #15). It owns
// the race guards every server vertical depends on:
//
//  - stale load: every load carries a sequence number; a response whose sequence
//    is no longer current is dropped, and unmount bumps the sequence so an
//    in-flight load never lands on a dead component.
//  - close-while-validating / selected-server replacement: opening/closing the
//    detail drawer bumps a panel sequence and records the selected id;
//    validate/delete capture both at dispatch, so a late result never surfaces
//    inline in a drawer that moved on (the validation result still updates the
//    table — it is server-authoritative).
//  - duplicate mutation: same-server mutations serialize through a per-id queue,
//    so the API's authoritative snapshots apply in dispatch order.
//  - mutation vs refresh: a mutation bumps a mutation epoch, so a refresh whose
//    request straddles the mutation is dropped and the mutation result stays
//    authoritative.
//
// The redirect (401) case is swallowed: fetchWithAuth already cleared identity
// and started the session-expiry navigation. Secrets never enter this module —
// the safe views the API returns carry none. Per-service wording is supplied
// through `messages`, and the domain shapes through the two type parameters. The
// public type surface lives in ./servers-controller-types (re-exported below so
// the per-vertical wrappers import everything from one place).
export type { MutationResult, ServersController } from './servers-controller-types'

const REDIRECTING: MutationResult = { ok: false, message: '', current: false }

export function useServersController<
  TServer extends ServerLike,
  TValidate extends ValidateResultLike,
>(api: ServersControllerApi<TServer, TValidate>): ServersController<TServer, TValidate> {
  // Destructure into individual bindings: the api transport fns are stable
  // module imports (stable by reference) and the message strings are stable by
  // value, so they can drive the useCallback/useEffect dep arrays directly —
  // the object literal the wrapper builds each render never destabilizes them.
  const {
    fetchServers,
    createServer: apiCreate,
    updateServer: apiUpdate,
    deleteServer: apiDelete,
    validateServer: apiValidate,
    messages,
  } = api
  const {
    load: loadMessage,
    create: createMessage,
    update: updateMessage,
    delete: deleteMessage,
    validate: validateMessage,
  } = messages

  const sortServers = useCallback(
    (servers: TServer[]): TServer[] => servers.slice().sort((a, b) => a.name.localeCompare(b.name)),
    [],
  )

  const [servers, setServers] = useState<TServer[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedServerId, setSelectedServerId] = useState<string | null>(null)
  const [validateResultById, setValidateResultById] = useState<Record<string, TValidate>>({})
  const [validateBusyId, setValidateBusyId] = useState<string | null>(null)
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null)

  const loadSeq = useRef(0)
  const mutationEpoch = useRef(0)
  const panelSeq = useRef(0)
  const selectedIdRef = useRef<string | null>(null)
  const mutationQueues = useRef(new Map<string, Promise<void>>())

  const enqueueMutation = useCallback(<T,>(serverId: string, task: () => Promise<T>): Promise<T> => {
    const previous = mutationQueues.current.get(serverId) ?? Promise.resolve()
    const result = previous.catch(() => undefined).then(task)
    const settled = result.then(
      () => undefined,
      () => undefined,
    )
    mutationQueues.current.set(serverId, settled)
    void settled.finally(() => {
      if (mutationQueues.current.get(serverId) === settled) mutationQueues.current.delete(serverId)
    })
    return result
  }, [])

  const runLoad = useCallback(
    async (seq: number, epoch: number) => {
      try {
        const next = await fetchServers()
        if (seq !== loadSeq.current || epoch !== mutationEpoch.current) return
        setServers(sortServers(next))
      } catch (error) {
        if (seq !== loadSeq.current || epoch !== mutationEpoch.current || isAuthRedirectError(error)) {
          return
        }
        setLoadError(toMessage(error, loadMessage))
      } finally {
        if (seq === loadSeq.current && epoch === mutationEpoch.current) setLoading(false)
      }
    },
    [fetchServers, loadMessage, sortServers],
  )

  const reload = useCallback(async () => {
    const seq = ++loadSeq.current
    const epoch = mutationEpoch.current
    setLoading(true)
    setLoadError(null)
    await runLoad(seq, epoch)
  }, [runLoad])

  useEffect(() => {
    const seq = ++loadSeq.current
    const epoch = mutationEpoch.current
    void runLoad(seq, epoch)
    return () => {
      loadSeq.current += 1
    }
  }, [runLoad])

  const selectServer = useCallback((serverId: string | null) => {
    panelSeq.current += 1
    selectedIdRef.current = serverId
    setSelectedServerId(serverId)
  }, [])

  const stillMatches = useCallback((seq: number, serverId: string) => {
    return panelSeq.current === seq && selectedIdRef.current === serverId
  }, [])

  // A silent refresh (no loading flash) that picks up any server-side changes
  // after a validate (e.g. a refreshed SSH host-key fingerprint). Guarded by the
  // same seq/epoch checks so it never clobbers a newer load or mutation snapshot.
  const silentRefresh = useCallback(async () => {
    const seq = ++loadSeq.current
    const epoch = mutationEpoch.current
    try {
      const next = await fetchServers()
      if (seq !== loadSeq.current || epoch !== mutationEpoch.current) return
      setServers(sortServers(next))
    } catch {
      // Swallow: the validate result is already surfaced; the list stays as-is.
    }
  }, [fetchServers, sortServers])

  const createServer = useCallback(
    async (body: Record<string, unknown>): Promise<MutationResult> => {
      mutationEpoch.current += 1
      setLoading(false)
      try {
        const server = await apiCreate(body)
        mutationEpoch.current += 1
        setServers((prev) => sortServers([...prev.filter((s) => s.id !== server.id), server]))
        return { ok: true, current: true }
      } catch (error) {
        mutationEpoch.current += 1
        if (isAuthRedirectError(error)) return REDIRECTING
        return { ok: false, message: toMessage(error, createMessage), current: true }
      }
    },
    [apiCreate, createMessage, sortServers],
  )

  const updateServer = useCallback(
    async (serverId: string, body: Record<string, unknown>): Promise<MutationResult> => {
      const seq = panelSeq.current
      mutationEpoch.current += 1
      setLoading(false)
      return enqueueMutation(serverId, async () => {
        try {
          const server = await apiUpdate(serverId, body)
          mutationEpoch.current += 1
          setServers((prev) => sortServers(prev.map((s) => (s.id === serverId ? server : s))))
          setValidateResultById((prev) => {
            const next = { ...prev }
            delete next[serverId]
            return next
          })
          return { ok: true, current: stillMatches(seq, serverId) }
        } catch (error) {
          mutationEpoch.current += 1
          if (isAuthRedirectError(error)) return REDIRECTING
          return {
            ok: false,
            message: toMessage(error, updateMessage),
            current: stillMatches(seq, serverId),
          }
        }
      })
    },
    [apiUpdate, enqueueMutation, sortServers, stillMatches, updateMessage],
  )

  const deleteServer = useCallback(
    async (serverId: string): Promise<MutationResult> => {
      const seq = panelSeq.current
      mutationEpoch.current += 1
      setLoading(false)
      setDeleteBusyId(serverId)
      return enqueueMutation(serverId, async () => {
        try {
          await apiDelete(serverId)
          mutationEpoch.current += 1
          setServers((prev) => prev.filter((s) => s.id !== serverId))
          setValidateResultById((prev) => {
            const next = { ...prev }
            delete next[serverId]
            return next
          })
          const current = stillMatches(seq, serverId)
          if (current) selectServer(null)
          return { ok: true, current }
        } catch (error) {
          mutationEpoch.current += 1
          if (isAuthRedirectError(error)) return REDIRECTING
          return {
            ok: false,
            message: toMessage(error, deleteMessage),
            current: stillMatches(seq, serverId),
          }
        } finally {
          setDeleteBusyId((prev) => (prev === serverId ? null : prev))
        }
      })
    },
    [apiDelete, deleteMessage, enqueueMutation, selectServer, stillMatches],
  )

  const validateServer = useCallback(
    async (serverId: string): Promise<MutationResult> => {
      const seq = panelSeq.current
      setValidateBusyId(serverId)
      return enqueueMutation(serverId, async () => {
        try {
          const result = await apiValidate(serverId)
          // The validation badge is server-authoritative and shown in the table,
          // so it always applies — even if the drawer moved on.
          setValidateResultById((prev) => ({ ...prev, [serverId]: result }))
          await silentRefresh()
          const current = stillMatches(seq, serverId)
          return result.ok
            ? { ok: true, current }
            : { ok: false, message: result.message || 'Validation failed', current }
        } catch (error) {
          if (isAuthRedirectError(error)) return REDIRECTING
          const message = toMessage(error, validateMessage)
          setValidateResultById((prev) => ({
            ...prev,
            [serverId]: { ok: false, message } as TValidate,
          }))
          return { ok: false, message, current: stillMatches(seq, serverId) }
        } finally {
          setValidateBusyId((prev) => (prev === serverId ? null : prev))
        }
      })
    },
    [apiValidate, enqueueMutation, silentRefresh, stillMatches, validateMessage],
  )

  const selectedServer = selectedServerId
    ? (servers.find((server) => server.id === selectedServerId) ?? null)
    : null

  return {
    servers,
    loading,
    loadError,
    reload,
    selectedServer,
    selectServer,
    validateResultById,
    validateBusyId,
    deleteBusyId,
    createServer,
    updateServer,
    deleteServer,
    validateServer,
  }
}
