'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { toMessage } from '@/lib/admin/shared/error-message'
import { isAuthRedirectError } from '@/lib/auth/session'

import type { AdminUser } from './types'
import {
  deleteUser as apiDeleteUser,
  fetchUsersAndRoles,
  setUserActive as apiSetUserActive,
  setUserRoles as apiSetUserRoles,
} from './users-api'

// The Users data controller (issue #106). It owns three race guards the vertical
// depends on, and keeps them in one testable unit:
//
//  - stale load: every load carries a sequence number; a response whose sequence
//    is no longer current is dropped, and unmount bumps the sequence so an
//    in-flight load never lands on a dead component.
//  - panel close: opening/closing the detail panel bumps a panel sequence and
//    records the selected id; a mutation captures both at dispatch.
//  - mutation vs refresh: a mutation ALWAYS applies its result to the list (the
//    server outcome is authoritative), but panel side effects — closing on
//    success — only fire when the panel still shows the same user it was
//    dispatched for. Selecting a different row, or a refresh, can't be undone by
//    a late mutation.
//
// The redirect (401) case is swallowed silently: fetchWithAuth has already
// cleared identity and started the session-expiry navigation.

export type MutationResult =
  | { ok: true; current: boolean }
  | { ok: false; message: string; current: boolean }

const REDIRECTING: MutationResult = { ok: false, message: '', current: false }

export type UsersController = {
  users: AdminUser[]
  availableRoles: string[]
  loading: boolean
  loadError: string | null
  reload: () => Promise<void>
  selectedUser: AdminUser | null
  selectUser: (userId: string | null) => void
  saveRoles: (userId: string, roles: string[]) => Promise<MutationResult>
  setActive: (userId: string, isActive: boolean) => Promise<MutationResult>
  removeUser: (userId: string) => Promise<MutationResult>
}

export function useUsers(): UsersController {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [availableRoles, setAvailableRoles] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null)

  const loadSeq = useRef(0)
  const mutationEpoch = useRef(0)
  const panelSeq = useRef(0)
  const selectedIdRef = useRef<string | null>(null)
  // Same-user mutations serialize through this map. Each task starts after the
  // previous one settles, so the API's full-user snapshots are applied in server
  // dispatch order and an older response can never overwrite a newer action.
  const mutationQueues = useRef(new Map<string, Promise<void>>())

  const enqueueMutation = useCallback(<T,>(userId: string, task: () => Promise<T>): Promise<T> => {
    const previous = mutationQueues.current.get(userId) ?? Promise.resolve()
    const result = previous.catch(() => undefined).then(task)
    const settled = result.then(
      () => undefined,
      () => undefined,
    )
    mutationQueues.current.set(userId, settled)
    void settled.finally(() => {
      if (mutationQueues.current.get(userId) === settled) mutationQueues.current.delete(userId)
    })
    return result
  }, [])

  // The shared load body. It only touches state after the await, so it is safe to
  // launch from the mount effect without a synchronous set-state. A load also
  // captures the mutation epoch: any mutation starting or settling while the
  // request is in flight makes the response stale. This prevents refresh from
  // overwriting an immediate mutation result with an older server snapshot.
  const runLoad = useCallback(async (seq: number, epoch: number) => {
    try {
      const { users: nextUsers, roles } = await fetchUsersAndRoles()
      if (seq !== loadSeq.current || epoch !== mutationEpoch.current) return
      setUsers(nextUsers)
      setAvailableRoles(roles)
    } catch (error) {
      if (
        seq !== loadSeq.current ||
        epoch !== mutationEpoch.current ||
        isAuthRedirectError(error)
      ) {
        return
      }
      setLoadError(toMessage(error, 'Unable to load users'))
    } finally {
      if (seq === loadSeq.current && epoch === mutationEpoch.current) setLoading(false)
    }
  }, [])

  // Manual reload (Refresh / retry). Called only from event handlers, so the
  // leading loading reset is fine here.
  const reload = useCallback(async () => {
    const seq = ++loadSeq.current
    const epoch = mutationEpoch.current
    setLoading(true)
    setLoadError(null)
    await runLoad(seq, epoch)
  }, [runLoad])

  useEffect(() => {
    // `loading` already starts true, so the mount load needs no synchronous set.
    const seq = ++loadSeq.current
    const epoch = mutationEpoch.current
    void runLoad(seq, epoch)
    return () => {
      // Invalidate any in-flight load so its response is ignored after unmount.
      loadSeq.current += 1
    }
  }, [runLoad])

  const selectUser = useCallback((userId: string | null) => {
    panelSeq.current += 1
    selectedIdRef.current = userId
    setSelectedUserId(userId)
  }, [])

  const stillMatches = useCallback((seq: number, userId: string) => {
    return panelSeq.current === seq && selectedIdRef.current === userId
  }, [])

  const saveRoles = useCallback(
    async (userId: string, roles: string[]): Promise<MutationResult> => {
      const seq = panelSeq.current
      mutationEpoch.current += 1
      setLoading(false)
      return enqueueMutation(userId, async () => {
        try {
          const updated = await apiSetUserRoles(userId, roles)
          mutationEpoch.current += 1
          setLoading(false)
          setUsers((prev) => prev.map((user) => (user.id === userId ? updated : user)))
          const current = stillMatches(seq, userId)
          if (current) selectUser(null)
          return { ok: true, current }
        } catch (error) {
          mutationEpoch.current += 1
          setLoading(false)
          if (isAuthRedirectError(error)) return REDIRECTING
          return {
            ok: false,
            message: toMessage(error, 'Unable to save roles'),
            current: stillMatches(seq, userId),
          }
        }
      })
    },
    [enqueueMutation, selectUser, stillMatches],
  )

  const setActive = useCallback(
    async (userId: string, isActive: boolean): Promise<MutationResult> => {
      const seq = panelSeq.current
      mutationEpoch.current += 1
      setLoading(false)
      return enqueueMutation(userId, async () => {
        try {
          const updated = await apiSetUserActive(userId, isActive)
          mutationEpoch.current += 1
          setLoading(false)
          setUsers((prev) => prev.map((user) => (user.id === userId ? updated : user)))
          return { ok: true, current: stillMatches(seq, userId) }
        } catch (error) {
          mutationEpoch.current += 1
          setLoading(false)
          if (isAuthRedirectError(error)) return REDIRECTING
          return {
            ok: false,
            message: toMessage(error, 'Unable to update user status'),
            current: stillMatches(seq, userId),
          }
        }
      })
    },
    [enqueueMutation, stillMatches],
  )

  const removeUser = useCallback(
    async (userId: string): Promise<MutationResult> => {
      const seq = panelSeq.current
      mutationEpoch.current += 1
      setLoading(false)
      return enqueueMutation(userId, async () => {
        try {
          await apiDeleteUser(userId)
          mutationEpoch.current += 1
          setLoading(false)
          setUsers((prev) => prev.filter((user) => user.id !== userId))
          const current = stillMatches(seq, userId)
          if (current) selectUser(null)
          return { ok: true, current }
        } catch (error) {
          mutationEpoch.current += 1
          setLoading(false)
          if (isAuthRedirectError(error)) return REDIRECTING
          return {
            ok: false,
            message: toMessage(error, 'Unable to delete user'),
            current: stillMatches(seq, userId),
          }
        }
      })
    },
    [enqueueMutation, selectUser, stillMatches],
  )

  const selectedUser = selectedUserId
    ? (users.find((user) => user.id === selectedUserId) ?? null)
    : null

  return {
    users,
    availableRoles,
    loading,
    loadError,
    reload,
    selectedUser,
    selectUser,
    saveRoles,
    setActive,
    removeUser,
  }
}
