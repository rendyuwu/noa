import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'
import { AuthRedirectError } from '@/lib/auth/session'

import type { McpToken, MintedToken, TokenScope } from './types'
import { useTokens, type MintOutcome, type RevokeOutcome } from './use-tokens'

// The pre-settle window §V104 is about: a mutation dispatched before the first
// load for its scope has answered. It lives in its own file because
// `use-tokens.test.tsx` is at 424 of the 450 lines C14 allows a `.tsx`, and
// because every test there enters through a helper that waits for
// `loading === false` first — which is §V104(c) exactly, and precisely why this
// window went unentered until it was found by execution.
//
// Each case holds the initial `fetchTokens` OPEN, dispatches the mutation into
// that window, and only then lets the load answer (V89: the two parties have to
// genuinely overlap, and the assertion is on ORDER). The observable being
// defended is not a row count — it is that the panel is still USABLE afterwards:
// `loading` false, and the list equal to what the server last said. A panel
// stuck at `loading: true` renders a skeleton with `Refresh` disabled, so the
// one control that could recover it is unreachable.

vi.mock('./tokens-api', () => ({
  fetchTokens: vi.fn(),
  mintToken: vi.fn(),
  revokeToken: vi.fn(),
}))

const api = await import('./tokens-api')
const fetchTokens = api.fetchTokens as unknown as Mock
const mintToken = api.mintToken as unknown as Mock
const revokeToken = api.revokeToken as unknown as Mock

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

const PLAINTEXT = `noa_${'k3Y'.repeat(14)}z`

const row = (id: string): McpToken => ({
  id,
  user_id: 'u1',
  token_prefix: `noa_${id.toLowerCase().repeat(8)}`,
  label: null,
  librechat_user_id: null,
  last_used_at: null,
  last_ldap_check_at: null,
  expires_at: null,
  created_at: '2026-09-01T00:00:00Z',
})

const tokenA = row('A')
const tokenB = row('B')
const tokenC = row('C')

const SELF: TokenScope = { kind: 'self' }
const U1: TokenScope = { kind: 'user', userId: 'u1' }
const U2: TokenScope = { kind: 'user', userId: 'u2' }

const ids = (tokens: McpToken[]): string[] => tokens.map((token) => token.id)

beforeEach(() => {
  fetchTokens.mockReset()
  mintToken.mockReset()
  revokeToken.mockReset()
  fetchTokens.mockResolvedValue([tokenA, tokenB])
})

function renderTokens(scope: TokenScope = SELF) {
  return renderHook(({ scope: current }: { scope: TokenScope }) => useTokens(current), {
    initialProps: { scope },
  })
}

// The entry `use-tokens.test.tsx` uses everywhere, kept here only for the two
// cases that deliberately start OUTSIDE the pre-settle window.
async function mountedAt(scope: TokenScope) {
  const view = renderTokens(scope)
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

// Every test below goes red by deleting `void settleCurrentScope()` /
// `await settleCurrentScope()` from the mutation it exercises in `use-tokens.ts`
// — named per case, because they are different call sites.

describe('a mutation before the first load settles (§V104)', () => {
  it('mint answers first: the panel recovers from the server, not from the dropped load', async () => {
    // Red without `void settleCurrentScope()` on mint's SUCCESS path.
    const initial = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(initial.promise)
    const minted: MintedToken = { token: tokenC, plaintext: PLAINTEXT }
    mintToken.mockResolvedValue(minted)

    const { result } = renderTokens()

    // The window is open: the first load is out, unanswered, and no scope has a
    // stamp yet. This is the fact that makes what follows a race.
    expect(fetchTokens).toHaveBeenCalledTimes(1)
    expect(result.current.loading).toBe(true)

    // Queued before the mint so the recovery re-read gets it: the server has the
    // minted row, which is how the operator sees a credential they can revoke.
    fetchTokens.mockResolvedValueOnce([tokenC, tokenA, tokenB])

    const order: string[] = []
    let outcome!: MintOutcome
    await act(async () => {
      outcome = await result.current.mint('laptop')
      order.push('mint')
    })

    // The mint's own outcome is never withheld pending the re-read.
    expect(outcome).toEqual({ ok: true, minted })

    await act(async () => {
      initial.resolve([tokenA, tokenB])
      order.push('initial-load')
    })

    expect(order).toEqual(['mint', 'initial-load'])
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(ids(result.current.tokens)).toEqual(['C', 'A', 'B'])
    expect(result.current.loadError).toBeNull()
  })

  it('initial load answers first: it is declined, and the mint still recovers', async () => {
    // Red without `void settleCurrentScope()` on mint's SUCCESS path.
    const initial = deferred<McpToken[]>()
    const slowMint = deferred<MintedToken>()
    fetchTokens.mockReturnValueOnce(initial.promise)
    mintToken.mockReturnValueOnce(slowMint.promise)

    const { result } = renderTokens()
    expect(fetchTokens).toHaveBeenCalledTimes(1)

    const order: string[] = []
    let done!: Promise<MintOutcome>
    await act(async () => {
      done = result.current.mint('laptop')
    })
    // Both are out at once. The mint has already bumped the epoch the in-flight
    // load captured, so that load is doomed before it answers.
    expect(mintToken).toHaveBeenCalledTimes(1)

    await act(async () => {
      initial.resolve([tokenA, tokenB])
      order.push('initial-load')
    })

    // The load answered and was declined — the state it would have written is
    // the one that never arrives. Without a recovery this is the terminal state.
    expect(result.current.loading).toBe(true)
    expect(result.current.tokens).toEqual([])
    expect(result.current.loadError).toBeNull()

    fetchTokens.mockResolvedValueOnce([tokenC, tokenA, tokenB])
    await act(async () => {
      slowMint.resolve({ token: tokenC, plaintext: PLAINTEXT })
      await done
      order.push('mint')
    })

    expect(order).toEqual(['initial-load', 'mint'])
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(ids(result.current.tokens)).toEqual(['C', 'A', 'B'])
  })

  it('a mint that FAILED in the window leaves the panel usable too', async () => {
    // Red without `void settleCurrentScope()` on mint's FAILURE path. The
    // failure message alone is not the fix: the table behind the dialog is what
    // stays dead.
    const initial = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(initial.promise)
    mintToken.mockRejectedValue(new ApiError(500, 'Upstream is down'))

    const { result } = renderTokens()
    expect(fetchTokens).toHaveBeenCalledTimes(1)
    fetchTokens.mockResolvedValueOnce([tokenA, tokenB])

    let outcome!: MintOutcome
    await act(async () => {
      outcome = await result.current.mint(null)
    })
    expect(outcome).toEqual({ ok: false, message: 'Upstream is down' })

    await act(async () => {
      initial.resolve([tokenC])
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(ids(result.current.tokens)).toEqual(['A', 'B'])
    expect(result.current.loadError).toBeNull()
  })

  it('a session that expired mid-mint does NOT re-read — it is already navigating', async () => {
    // The negative control for the three above: the recovery is not "always
    // fetch again". Deleting the `isAuthRedirectError` early return makes this
    // one red, and a 401 re-read would only earn a second 401.
    const initial = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(initial.promise)
    mintToken.mockRejectedValue(new AuthRedirectError())

    const { result } = renderTokens()
    let outcome!: MintOutcome
    await act(async () => {
      outcome = await result.current.mint(null)
    })

    expect(outcome).toEqual({ redirecting: true })
    expect(fetchTokens).toHaveBeenCalledTimes(1)
    initial.resolve([tokenA, tokenB])
  })

  it('revoke has the same defect shape, and the same server read answers it', async () => {
    // Red without `await settleCurrentScope()` on revoke's SUCCESS path. The UI
    // cannot reach this today (a revoke needs a visible row), which is exactly
    // why the controller has to hold it on its own.
    const initial = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(initial.promise)
    revokeToken.mockResolvedValue(undefined)

    const { result } = renderTokens()
    expect(fetchTokens).toHaveBeenCalledTimes(1)
    fetchTokens.mockResolvedValueOnce([tokenB])

    const order: string[] = []
    let outcome!: RevokeOutcome
    await act(async () => {
      outcome = await result.current.revoke('A')
      order.push('revoke')
    })
    expect(outcome).toEqual({ ok: true })

    await act(async () => {
      initial.resolve([tokenA, tokenB])
      order.push('initial-load')
    })

    expect(order).toEqual(['revoke', 'initial-load'])
    await waitFor(() => expect(result.current.loading).toBe(false))
    // ['B'] is the server's list. A row filter applied to a list that never
    // loaded would have produced [] — indistinguishable from an empty account.
    expect(ids(result.current.tokens)).toEqual(['B'])
  })

  it('recovers the scope ON SCREEN when a mutation for the previous one lands first', async () => {
    // Red without `await settleCurrentScope()` on revoke's SUCCESS path, and
    // also red if that call asks about the mutation's own target instead of
    // `scopeRef.current`: u1's revoke is what invalidates u2's load, and u2 is
    // the scope left with nothing to describe it.
    const view = await mountedAt(U1)

    const slowRevoke = deferred<void>()
    revokeToken.mockReturnValueOnce(slowRevoke.promise)
    let done!: Promise<RevokeOutcome>
    await act(async () => {
      done = view.result.current.revoke('A')
    })
    expect(revokeToken).toHaveBeenCalledWith(U1, 'A')

    // The operator moves to another user while the DELETE is out, and u2's load
    // is still unanswered when it settles.
    const u2Load = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(u2Load.promise)
    view.rerender({ scope: U2 })
    expect(view.result.current.loading).toBe(true)

    fetchTokens.mockResolvedValueOnce([tokenC])
    await act(async () => {
      slowRevoke.resolve()
      await done
    })
    await act(async () => {
      u2Load.resolve([tokenA, tokenB])
    })

    await waitFor(() => expect(view.result.current.loading).toBe(false))
    expect(fetchTokens).toHaveBeenLastCalledWith(U2)
    // u2's own rows, read after the epoch bump. Never u1's.
    expect(ids(view.result.current.tokens)).toEqual(['C'])
  })

  it('negative control: a settled scope costs no extra read, so this is not a fetch per mutation', async () => {
    // Without this, the recovery could be an unconditional re-read and every
    // test above would still pass — while doubling the requests the panel makes
    // and letting a stale list overwrite a fresh mutation result.
    const view = await mountedAt(SELF)
    mintToken.mockResolvedValue({ token: tokenC, plaintext: PLAINTEXT })

    await act(async () => {
      await view.result.current.mint(null)
    })

    expect(fetchTokens).toHaveBeenCalledTimes(1)
    expect(ids(view.result.current.tokens)).toEqual(['C', 'A', 'B'])
  })
})
