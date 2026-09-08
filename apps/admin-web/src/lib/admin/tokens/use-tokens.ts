'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { toMessage } from '@/lib/admin/shared/error-message'
import { ApiError } from '@/lib/auth/fetch-helper'
import { isAuthRedirectError } from '@/lib/auth/session'

import {
  fetchTokens,
  mintToken as apiMintToken,
  revokeToken as apiRevokeToken,
} from './tokens-api'
import type { McpToken, MintedToken, TokenScope } from './types'

// The MCP token data controller (§T76). It owns the same two race guards the
// Users controller documents at `use-users.ts:16-31` — stale load by sequence,
// mutation-beats-refresh by epoch — plus a third this vertical needs:
//
//  - SCOPE tag: every loaded row set is stamped with the scope it was loaded
//    under. The same panel serves `/me/tokens` and `/admin/users/{id}/tokens`,
//    so a `userId` change must INVALIDATE the rows rather than leave them on
//    screen: a stale row clicked after the scope moved would send one operator's
//    token id down another operator's path. Rows whose stamp does not match the
//    current scope are not returned at all, and a mutation only applies to state
//    while the stamp still matches the scope it was dispatched for.
//
// The stamp is also what makes a mutation dispatched BEFORE the scope on screen
// settled a dead end, so every mutation ends by re-reading a scope that still
// has no stamp — see `settleCurrentScope`, and §V104 for why half a guard is
// worse than none.
//
// The redirect (401) case is swallowed: fetchWithAuth has already cleared
// identity and started the session-expiry navigation.

export type MintOutcome =
  | { ok: true; minted: MintedToken }
  | { ok: false; message: string }
  | { redirecting: true }

export type RevokeOutcome = { ok: true } | { ok: false; message: string } | { redirecting: true }

const REDIRECTING = { redirecting: true } as const

// The backend answers 404 `mcp_token_not_found` for an id that never existed,
// for one already revoked, and for one belonging to a colleague — deliberately
// the same answer (mcp_tokens.py:191-197, V2). So this wording resolves none of
// them: it reports what the response supports and nothing more. "Already
// revoked" would be the UI inventing a history it did not witness.
export const TOKEN_ABSENT_MESSAGE = 'This token is no longer present'

const EMPTY_TOKENS: McpToken[] = []

// Identity of a scope as a comparable value. `TokenScope` arrives as a fresh
// object literal on every render of the host page, so the object itself can
// never be a dependency or a comparison key.
function scopeKey(scope: TokenScope): string {
  return scope.kind === 'self' ? 'self' : `user:${scope.userId}`
}

// Everything a load produces, stamped with the scope it describes. Held as one
// value so rows, error and spinner can never disagree about which operator they
// belong to. `''` is the pre-first-load stamp and matches no real scope.
type LoadState = {
  scopeKey: string
  tokens: McpToken[]
  loading: boolean
  loadError: string | null
}

const INITIAL: LoadState = { scopeKey: '', tokens: EMPTY_TOKENS, loading: true, loadError: null }

export type TokensController = {
  tokens: McpToken[]
  loading: boolean
  loadError: string | null
  reload: () => Promise<void>
  mint: (label: string | null) => Promise<MintOutcome>
  revoke: (tokenId: string) => Promise<RevokeOutcome>
}

export function useTokens(scope: TokenScope): TokensController {
  const key = scopeKey(scope)
  const [state, setState] = useState<LoadState>(INITIAL)

  const loadSeq = useRef(0)
  const mutationEpoch = useRef(0)
  const scopeRef = useRef(scope)
  // A ref shadow of `state.scopeKey`, written wherever that field is written.
  // The mutation callbacks have to know whether the scope on screen has any
  // settled row set describing it, and they cannot read `state`: adding it to
  // their dependency arrays would rebuild `mint`/`revoke` on every load, and a
  // closure captured at an older render would answer for the wrong one.
  const settledKey = useRef(INITIAL.scopeKey)

  // No dependency array on purpose, and declared before the load effect so it
  // runs first on the same commit: the ref must follow the caller's scope
  // without the scope object's per-render identity becoming a load trigger.
  useEffect(() => {
    scopeRef.current = scope
  })

  // A mutation invalidates any load already in flight, so it also has to clear
  // the spinner itself — the superseded load's own guard will decline to.
  const stopLoading = useCallback(() => {
    setState((prev) => (prev.loading ? { ...prev, loading: false } : prev))
  }, [])

  const runLoad = useCallback(async (seq: number, epoch: number, target: TokenScope) => {
    const targetKey = scopeKey(target)
    try {
      const tokens = await fetchTokens(target)
      if (seq !== loadSeq.current || epoch !== mutationEpoch.current) return
      settledKey.current = targetKey
      setState({ scopeKey: targetKey, tokens, loading: false, loadError: null })
    } catch (error) {
      if (seq !== loadSeq.current || epoch !== mutationEpoch.current) return
      // A 401 is already navigating. State stays as it is: "still loading" is
      // the honest description of a request that never answered, and the page
      // is on its way out anyway.
      if (isAuthRedirectError(error)) return
      const message = toMessage(error, 'Unable to load MCP tokens')
      // A load error IS a settled description of the scope — the panel renders
      // it with a Retry, which is usable. Only a load that never answered for
      // this scope leaves nothing behind.
      settledKey.current = targetKey
      setState((prev) => ({
        scopeKey: targetKey,
        tokens: prev.scopeKey === targetKey ? prev.tokens : EMPTY_TOKENS,
        loading: false,
        loadError: message,
      }))
    }
  }, [])

  const reloadScope = useCallback(
    async (target: TokenScope) => {
      const seq = ++loadSeq.current
      const epoch = mutationEpoch.current
      const targetKey = scopeKey(target)
      setState((prev) =>
        prev.scopeKey === targetKey ? { ...prev, loading: true, loadError: null } : prev,
      )
      await runLoad(seq, epoch, target)
    },
    [runLoad],
  )

  // Manual reload (Refresh / retry), and the authoritative re-read behind a 404.
  const reload = useCallback(async () => {
    await reloadScope(scopeRef.current)
  }, [reloadScope])

  // Recovery every mutation owes, and the whole of §V104: a guard that CANCELS
  // in-flight work has to guarantee that work is re-issued. A mutation
  // dispatched before the scope on screen ever settled leaves the panel with
  // nothing describing it — the load it invalidated by epoch declines to write
  // a row set, and the mutation's own write is gated on a stamp that scope
  // never received. `settled` below then stays false forever: `loading: true`,
  // no rows, no error, and the load effect will not re-fire because
  // `[key, runLoad]` did not change. Worse, the one control that would recover
  // it is `disabled={loading}` — disabled by the state it would fix. So
  // re-read, and let the server's list be the answer: the same move the 404
  // revoke makes below, for the same reason — the controller must not describe
  // a scope it has not read.
  //
  // It asks about `scopeRef`, not about the mutation's own target, because that
  // covers the second shape of the same defect: a mutation for the PREVIOUS
  // scope settling while the new scope's load is still out bumps the epoch that
  // makes that load decline, and the scope on screen is the one left blank.
  const settleCurrentScope = useCallback(async () => {
    const target = scopeRef.current
    if (settledKey.current === scopeKey(target)) return
    await reloadScope(target)
  }, [reloadScope])

  useEffect(() => {
    // A scope change re-enters here with a new key. `loading` for a scope that
    // has not settled is derived below, so there is no synchronous set here.
    const seq = ++loadSeq.current
    const epoch = mutationEpoch.current
    void runLoad(seq, epoch, scopeRef.current)
    return () => {
      // Invalidate any in-flight load so its response is ignored after unmount
      // or after the scope moved.
      loadSeq.current += 1
    }
  }, [key, runLoad])

  const mint = useCallback(
    async (label: string | null): Promise<MintOutcome> => {
      const target = scopeRef.current
      const targetKey = scopeKey(target)
      mutationEpoch.current += 1
      stopLoading()
      try {
        const minted = await apiMintToken(target, label)
        mutationEpoch.current += 1
        // The ROW joins the list. The plaintext does not, and this is the whole
        // of §V103 in one line: a secret written into controller state is
        // re-readable for as long as the controller lives, so it is only ever
        // this function's return value, owned by the one component that shows it.
        //
        // A scope that moved mid-flight keeps the outcome and drops the row: the
        // token was really minted, and throwing away the one chance to read it
        // because the operator navigated would be the worse failure.
        setState((prev) =>
          prev.scopeKey === targetKey
            ? { ...prev, loading: false, tokens: [minted.token, ...prev.tokens] }
            : prev,
        )
        // Dispatched, not awaited — mint is the one caller that does not wait.
        // The plaintext is readable exactly once and is unrecoverable, so this
        // return must not sit behind a second round trip that could be slow or
        // never answer. The re-read lands on its own and the table catches up.
        void settleCurrentScope()
        return { ok: true, minted }
      } catch (error) {
        mutationEpoch.current += 1
        stopLoading()
        // A 401 is already navigating; a re-read would only earn another one.
        if (isAuthRedirectError(error)) return REDIRECTING
        void settleCurrentScope()
        return { ok: false, message: toMessage(error, 'Unable to mint a token') }
      }
    },
    [settleCurrentScope, stopLoading],
  )

  const revoke = useCallback(
    async (tokenId: string): Promise<RevokeOutcome> => {
      const target = scopeRef.current
      const targetKey = scopeKey(target)
      mutationEpoch.current += 1
      stopLoading()
      try {
        await apiRevokeToken(target, tokenId)
        mutationEpoch.current += 1
        setState((prev) =>
          prev.scopeKey === targetKey
            ? { ...prev, loading: false, tokens: prev.tokens.filter((row) => row.id !== tokenId) }
            : prev,
        )
        await settleCurrentScope()
        return { ok: true }
      } catch (error) {
        mutationEpoch.current += 1
        stopLoading()
        if (isAuthRedirectError(error)) return REDIRECTING
        if (error instanceof ApiError && error.status === 404) {
          // Do not drop the row on inference. Re-read, and let the server's list
          // be the reason it is or is not there (§V104: the re-read is
          // authoritative, not a local patch). Skipped when the scope
          // moved: those rows are already invalidated by the stamp, so the
          // recovery re-reads whatever replaced them instead.
          if (scopeKey(scopeRef.current) === targetKey) await reloadScope(target)
          else await settleCurrentScope()
          return { ok: false, message: TOKEN_ABSENT_MESSAGE }
        }
        await settleCurrentScope()
        return { ok: false, message: toMessage(error, 'Unable to revoke the token') }
      }
    },
    [reloadScope, settleCurrentScope, stopLoading],
  )

  // Only a settled load for THIS scope is allowed to describe the UI. Anything
  // else reads as a fresh load in progress rather than as another scope's rows.
  const settled = state.scopeKey === key

  return {
    tokens: settled ? state.tokens : EMPTY_TOKENS,
    loading: settled ? state.loading : true,
    loadError: settled ? state.loadError : null,
    reload,
    mint,
    revoke,
  }
}
