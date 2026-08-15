'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { toMessage } from '@/lib/admin/shared/error-message'
import { isAuthRedirectError } from '@/lib/auth/session'

import {
  createRole as apiCreateRole,
  deleteRole as apiDeleteRole,
  fetchRoleTools,
  fetchRolesAndTools,
  setRoleTools as apiSetRoleTools,
} from './roles-api'

// The Roles data controller (issue #107), built on the Users vertical's proven
// composition. It owns the race guards the vertical depends on:
//
//  - stale load: every load carries a sequence number; a response whose sequence
//    is no longer current is dropped, and unmount bumps the sequence so an
//    in-flight load never lands on a dead component.
//  - stale count: per-role tool counts load in the background after the list.
//    Each role carries a count version; an authoritative update (open, save,
//    delete) bumps it, so a slow background count can never overwrite a fresher
//    saved count.
//  - panel close / stale selection: opening/closing the detail panel bumps a
//    panel sequence and records the selected name; the tool load and every
//    mutation capture both at dispatch and only touch panel state when it still
//    shows the same role.
//  - mutation vs refresh: a mutation bumps a mutation epoch, so a refresh whose
//    request straddles the mutation is dropped and the immediate mutation result
//    stays authoritative.
//
// The redirect (401) case is swallowed: fetchWithAuth already cleared identity
// and started the session-expiry navigation.

export type MutationResult =
  | { ok: true; current: boolean }
  | { ok: false; message: string; current: boolean }

const REDIRECTING: MutationResult = { ok: false, message: '', current: false }

export type RolesController = {
  roles: string[]
  availableTools: string[]
  roleToolCounts: Record<string, number>
  loading: boolean
  loadError: string | null
  reload: () => Promise<void>
  selectedRole: string | null
  selectRole: (name: string | null) => void
  roleTools: string[]
  roleToolsLoading: boolean
  roleToolsError: string | null
  createRole: (name: string) => Promise<MutationResult>
  deleteRole: (name: string) => Promise<MutationResult>
  saveRoleTools: (name: string, tools: string[]) => Promise<MutationResult>
}

export function useRoles(): RolesController {
  const [roles, setRoles] = useState<string[]>([])
  const [availableTools, setAvailableTools] = useState<string[]>([])
  const [roleToolCounts, setRoleToolCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selectedRole, setSelectedRole] = useState<string | null>(null)
  const [roleTools, setRoleTools] = useState<string[]>([])
  const [roleToolsLoading, setRoleToolsLoading] = useState(false)
  const [roleToolsError, setRoleToolsError] = useState<string | null>(null)

  const loadSeq = useRef(0)
  const mutationEpoch = useRef(0)
  const panelSeq = useRef(0)
  const selectedRef = useRef<string | null>(null)
  const countVersion = useRef<Record<string, number>>({})

  // Record an authoritative tool count for a role and bump its version so any
  // in-flight background count for the same role is ignored when it lands.
  const setCount = useCallback((name: string, count: number) => {
    countVersion.current[name] = (countVersion.current[name] ?? 0) + 1
    setRoleToolCounts((prev) => ({ ...prev, [name]: count }))
  }, [])

  const loadCounts = useCallback(async (names: string[], seq: number) => {
    const requested = Object.fromEntries(names.map((name) => [name, countVersion.current[name] ?? 0]))
    const results = await Promise.allSettled(
      names.map(async (name) => [name, (await fetchRoleTools(name)).length] as const),
    )
    if (seq !== loadSeq.current) return
    setRoleToolCounts((prev) => {
      const next = { ...prev }
      for (const result of results) {
        if (result.status === 'fulfilled') {
          const [name, count] = result.value
          if ((countVersion.current[name] ?? 0) === requested[name]) next[name] = count
        }
      }
      return next
    })
  }, [])

  const runLoad = useCallback(
    async (seq: number, epoch: number) => {
      try {
        const { roles: nextRoles, tools } = await fetchRolesAndTools()
        if (seq !== loadSeq.current || epoch !== mutationEpoch.current) return
        setRoles(nextRoles)
        setAvailableTools(tools)
        // Keep any counts we already resolved for roles that still exist; drop
        // the rest so a deleted role's count never lingers.
        setRoleToolCounts((prev) => {
          const next: Record<string, number> = {}
          for (const name of nextRoles) if (typeof prev[name] === 'number') next[name] = prev[name]
          return next
        })
        void loadCounts(nextRoles, seq)
      } catch (error) {
        if (seq !== loadSeq.current || epoch !== mutationEpoch.current || isAuthRedirectError(error)) {
          return
        }
        setLoadError(toMessage(error, 'Unable to load roles'))
      } finally {
        if (seq === loadSeq.current && epoch === mutationEpoch.current) setLoading(false)
      }
    },
    [loadCounts],
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

  const stillMatches = useCallback((seq: number, name: string) => {
    return panelSeq.current === seq && selectedRef.current === name
  }, [])

  const loadRoleTools = useCallback(
    async (name: string, seq: number) => {
      setRoleToolsLoading(true)
      setRoleToolsError(null)
      try {
        const tools = await fetchRoleTools(name)
        if (!stillMatches(seq, name)) return
        setRoleTools(tools)
        setCount(name, tools.length)
      } catch (error) {
        if (!stillMatches(seq, name) || isAuthRedirectError(error)) return
        setRoleToolsError(toMessage(error, 'Unable to load role tools'))
        setRoleTools([])
      } finally {
        if (stillMatches(seq, name)) setRoleToolsLoading(false)
      }
    },
    [setCount, stillMatches],
  )

  const selectRole = useCallback(
    (name: string | null) => {
      panelSeq.current += 1
      selectedRef.current = name
      setSelectedRole(name)
      setRoleTools([])
      setRoleToolsError(null)
      setRoleToolsLoading(name !== null)
      if (name !== null) void loadRoleTools(name, panelSeq.current)
    },
    [loadRoleTools],
  )

  const createRole = useCallback(
    async (name: string): Promise<MutationResult> => {
      mutationEpoch.current += 1
      setLoading(false)
      try {
        await apiCreateRole(name)
        mutationEpoch.current += 1
        setRoles((prev) => Array.from(new Set([...prev, name])).sort((a, b) => a.localeCompare(b)))
        setCount(name, 0)
        return { ok: true, current: true }
      } catch (error) {
        mutationEpoch.current += 1
        if (isAuthRedirectError(error)) return REDIRECTING
        return { ok: false, message: toMessage(error, 'Unable to create role'), current: true }
      }
    },
    [setCount],
  )

  const deleteRole = useCallback(
    async (name: string): Promise<MutationResult> => {
      const seq = panelSeq.current
      mutationEpoch.current += 1
      setLoading(false)
      try {
        await apiDeleteRole(name)
        mutationEpoch.current += 1
        setRoles((prev) => prev.filter((role) => role !== name))
        countVersion.current[name] = (countVersion.current[name] ?? 0) + 1
        setRoleToolCounts((prev) => {
          const next = { ...prev }
          delete next[name]
          return next
        })
        const current = stillMatches(seq, name)
        if (current) selectRole(null)
        return { ok: true, current }
      } catch (error) {
        mutationEpoch.current += 1
        if (isAuthRedirectError(error)) return REDIRECTING
        return {
          ok: false,
          message: toMessage(error, 'Unable to delete role'),
          current: stillMatches(seq, name),
        }
      }
    },
    [selectRole, stillMatches],
  )

  const saveRoleTools = useCallback(
    async (name: string, tools: string[]): Promise<MutationResult> => {
      const seq = panelSeq.current
      mutationEpoch.current += 1
      setLoading(false)
      try {
        await apiSetRoleTools(name, tools)
        mutationEpoch.current += 1
        setCount(name, tools.length)
        const current = stillMatches(seq, name)
        if (current) selectRole(null)
        return { ok: true, current }
      } catch (error) {
        mutationEpoch.current += 1
        if (isAuthRedirectError(error)) return REDIRECTING
        return {
          ok: false,
          message: toMessage(error, 'Unable to save role tools'),
          current: stillMatches(seq, name),
        }
      }
    },
    [setCount, selectRole, stillMatches],
  )

  return {
    roles,
    availableTools,
    roleToolCounts,
    loading,
    loadError,
    reload,
    selectedRole,
    selectRole,
    roleTools,
    roleToolsLoading,
    roleToolsError,
    createRole,
    deleteRole,
    saveRoleTools,
  }
}
