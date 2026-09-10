import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'
import { AuthRedirectError } from '@/lib/auth/session'

import { looksLikeTokenPlaintext } from './token-plaintext'
import type { McpToken, MintedToken, TokenScope } from './types'
import { TOKEN_ABSENT_MESSAGE, useTokens, type MintOutcome, type RevokeOutcome } from './use-tokens'

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
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const PLAINTEXT = `noa_${'k3Y'.repeat(14)}z`

const row = (over: Partial<McpToken> = {}): McpToken => ({
  id: 't1',
  user_id: 'u1',
  token_prefix: 'noa_abcd1234',
  label: null,
  librechat_user_id: null,
  last_used_at: null,
  last_ldap_check_at: null,
  expires_at: null,
  created_at: '2026-09-01T00:00:00Z',
  ...over,
})

const tokenA = row({ id: 'A', token_prefix: 'noa_aaaa1111' })
const tokenB = row({ id: 'B', token_prefix: 'noa_bbbb2222' })
const tokenC = row({ id: 'C', token_prefix: 'noa_cccc3333' })

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

async function mounted(scope: TokenScope = SELF) {
  const view = renderTokens(scope)
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

// The detector is `./token-plaintext`, shared with the dialog lane, and its own
// specs prove it still separates a plaintext from a `token_prefix`. What
// stays here is the reachability control the write-once-display rule demands: this file's
// `false` assertions only mean something if the same call is shown answering `true` for
// the value that was actually minted.

describe('useTokens loading', () => {
  it('loads on mount for the scope it was given', async () => {
    const { result } = await mounted(U1)
    expect(fetchTokens).toHaveBeenCalledWith(U1)
    expect(result.current.tokens).toEqual([tokenA, tokenB])
    expect(result.current.loadError).toBeNull()
  })

  it('surfaces a load failure as loadError, keeping the backend wording', async () => {
    fetchTokens.mockRejectedValueOnce(new ApiError(500, 'Upstream is down'))
    const { result } = renderTokens()
    await waitFor(() => expect(result.current.loadError).toBe('Upstream is down'))
  })

  it('says nothing when the load failed because the session is being redirected', async () => {
    fetchTokens.mockRejectedValueOnce(new AuthRedirectError())
    const { result } = renderTokens()
    await act(async () => {})
    expect(result.current.loadError).toBeNull()
  })

  it('reload retries and clears the previous error', async () => {
    fetchTokens.mockRejectedValueOnce(new ApiError(500, 'Upstream is down'))
    const { result } = renderTokens()
    await waitFor(() => expect(result.current.loadError).toBe('Upstream is down'))

    await act(async () => {
      await result.current.reload()
    })
    expect(result.current.loadError).toBeNull()
    expect(result.current.tokens).toEqual([tokenA, tokenB])
  })
})

// A concurrency test proves overlap: two callers have to genuinely OVERLAP, the assertion is
// on ORDER, and a negative control shows the forbidden interleaving is deliverable. Resolving
// two promises with `Promise.all` would pass with every guard deleted.
describe('stale load', () => {
  it('drops the older load when it lands after the newer one', async () => {
    const older = deferred<McpToken[]>()
    const newer = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)

    const { result } = renderTokens()
    await act(async () => {
      void result.current.reload()
    })

    // The window is open: both loads are out and neither has answered. This is
    // the fact that makes the interleaving below a race rather than a sequence.
    expect(fetchTokens).toHaveBeenCalledTimes(2)
    expect(result.current.tokens).toEqual([])

    const landed: string[] = []
    await act(async () => {
      newer.resolve([tokenB])
      landed.push('newer')
    })
    await act(async () => {
      older.resolve([tokenA])
      landed.push('older')
    })

    expect(landed).toEqual(['newer', 'older'])
    expect(result.current.tokens).toEqual([tokenB])
  })

  it('negative control: the same late-landing response DOES land when it is the current one', async () => {
    // Without this, the assertion above passes for a controller that ignores
    // every deferred response, or for a harness that never delivers one.
    const only = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(only.promise)

    const { result } = renderTokens()
    expect(fetchTokens).toHaveBeenCalledTimes(1)
    expect(result.current.loading).toBe(true)

    await act(async () => {
      only.resolve([tokenA])
    })
    expect(result.current.tokens).toEqual([tokenA])
  })
})

describe('mutation versus refresh', () => {
  it('a refresh in flight cannot undo a mutation that settled during it', async () => {
    const { result } = await mounted()
    const slowLoad = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(slowLoad.promise)
    revokeToken.mockResolvedValue(undefined)

    const settled: string[] = []
    let loadDone!: Promise<void>
    await act(async () => {
      loadDone = result.current.reload().then(() => {
        settled.push('load')
      })
    })

    // Window held open: the refresh is out and unanswered while the mutation runs.
    expect(fetchTokens).toHaveBeenCalledTimes(2)

    await act(async () => {
      await result.current.revoke('A')
      settled.push('mutation')
    })
    expect(ids(result.current.tokens)).toEqual(['B'])

    // The refresh answers LAST, carrying the pre-revoke list.
    await act(async () => {
      slowLoad.resolve([tokenA, tokenB])
      await loadDone
    })

    expect(settled).toEqual(['mutation', 'load'])
    expect(ids(result.current.tokens)).toEqual(['B'])
  })

  it('negative control: with no mutation in the window, that same late refresh wins', async () => {
    const { result } = await mounted()
    const slowLoad = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(slowLoad.promise)

    await act(async () => {
      void result.current.reload()
    })
    expect(fetchTokens).toHaveBeenCalledTimes(2)

    await act(async () => {
      slowLoad.resolve([tokenA, tokenB, tokenC])
    })
    // The forbidden order is reachable — it is the epoch, not the harness, that
    // stops it in the test above.
    expect(ids(result.current.tokens)).toEqual(['A', 'B', 'C'])
  })
})

describe('mint (the plaintext is never controller state)', () => {
  it('returns the plaintext, and keeps only the row', async () => {
    const { result } = await mounted()
    const minted: MintedToken = { token: tokenC, plaintext: PLAINTEXT }
    mintToken.mockResolvedValue(minted)

    let outcome!: MintOutcome
    await act(async () => {
      outcome = await result.current.mint('laptop')
    })

    expect(outcome).toEqual({ ok: true, minted })
    expect(mintToken).toHaveBeenCalledWith(SELF, 'laptop')
    // Newest first, matching the order the list endpoint returns.
    expect(ids(result.current.tokens)).toEqual(['C', 'A', 'B'])

    // The whole of the write-once-display rule here: nothing token-shaped is reachable through
    // the controller after a successful mint — and the same call demonstrably sees
    // the value that WAS minted, so the two `false`s are the controller's doing.
    expect(looksLikeTokenPlaintext(minted)).toBe(true)
    expect(looksLikeTokenPlaintext(result.current)).toBe(false)
    expect(looksLikeTokenPlaintext(result.current.tokens)).toBe(false)
  })

  it('still leaves nothing behind after a reload following the mint', async () => {
    const { result } = await mounted()
    mintToken.mockResolvedValue({ token: tokenC, plaintext: PLAINTEXT })
    await act(async () => {
      await result.current.mint(null)
    })
    await act(async () => {
      await result.current.reload()
    })
    expect(looksLikeTokenPlaintext(result.current)).toBe(false)
  })

  it('surfaces the backend wording on a refusal', async () => {
    const { result } = await mounted()
    mintToken.mockRejectedValue(
      new ApiError(400, 'Token label must be at most 255 characters', {
        errorCode: 'invalid_token_label',
      }),
    )

    let outcome!: MintOutcome
    await act(async () => {
      outcome = await result.current.mint('x')
    })
    expect(outcome).toEqual({ ok: false, message: 'Token label must be at most 255 characters' })
    // The failure outcome carries an ApiError's wording; the detector unpacks an
    // Error rather than stringifying it to `{}`, so this is a real check.
    expect(looksLikeTokenPlaintext(outcome)).toBe(false)
  })

  it('reports a redirect rather than an error when the session expired mid-mint', async () => {
    const { result } = await mounted()
    mintToken.mockRejectedValue(new AuthRedirectError())

    let outcome!: MintOutcome
    await act(async () => {
      outcome = await result.current.mint(null)
    })
    expect(outcome).toEqual({ redirecting: true })
  })
})

describe('revoke (a 404 is not knowledge, the re-read is)', () => {
  const notFound = () =>
    new ApiError(404, 'MCP token not found', { errorCode: 'mcp_token_not_found' })

  it('removes the row on a plain success', async () => {
    const { result } = await mounted()
    revokeToken.mockResolvedValue(undefined)

    let outcome!: RevokeOutcome
    await act(async () => {
      outcome = await result.current.revoke('A')
    })
    expect(outcome).toEqual({ ok: true })
    expect(revokeToken).toHaveBeenCalledWith(SELF, 'A')
    expect(ids(result.current.tokens)).toEqual(['B'])
  })

  it('re-reads on a 404 and KEEPS the row when the server still lists it', async () => {
    const { result } = await mounted()
    revokeToken.mockRejectedValue(notFound())
    fetchTokens.mockResolvedValueOnce([tokenA, tokenB])

    let outcome!: RevokeOutcome
    await act(async () => {
      outcome = await result.current.revoke('A')
    })

    // The re-read happened, and it is the authority: the UI did not infer a
    // deletion the server never confirmed.
    expect(fetchTokens).toHaveBeenCalledTimes(2)
    expect(ids(result.current.tokens)).toEqual(['A', 'B'])
    expect(outcome).toEqual({ ok: false, message: TOKEN_ABSENT_MESSAGE })
  })

  it('lets the re-read remove the row when the server no longer lists it', async () => {
    const { result } = await mounted()
    revokeToken.mockRejectedValue(notFound())
    fetchTokens.mockResolvedValueOnce([tokenB])

    await act(async () => {
      await result.current.revoke('A')
    })
    expect(ids(result.current.tokens)).toEqual(['B'])
  })

  it('never claims the token was already revoked', () => {
    // The same 404 answers an absent id, a colleague's id and a fabricated one
    // (mcp_tokens.py:191-197). Wording that picks one of those is the UI
    // inventing a history it did not witness.
    expect(TOKEN_ABSENT_MESSAGE).toBe('This token is no longer present')
    expect(TOKEN_ABSENT_MESSAGE).not.toMatch(/already/i)
    expect(TOKEN_ABSENT_MESSAGE).not.toMatch(/revoked/i)
  })

  it('surfaces other failures verbatim and does not re-read', async () => {
    const { result } = await mounted()
    revokeToken.mockRejectedValue(new ApiError(500, 'Upstream is down'))

    let outcome!: RevokeOutcome
    await act(async () => {
      outcome = await result.current.revoke('A')
    })
    expect(outcome).toEqual({ ok: false, message: 'Upstream is down' })
    expect(fetchTokens).toHaveBeenCalledTimes(1)
    expect(ids(result.current.tokens)).toEqual(['A', 'B'])
  })

  it('reports a redirect rather than an error when the session expired mid-revoke', async () => {
    const { result } = await mounted()
    revokeToken.mockRejectedValue(new AuthRedirectError())

    let outcome!: RevokeOutcome
    await act(async () => {
      outcome = await result.current.revoke('A')
    })
    expect(outcome).toEqual({ redirecting: true })
  })
})

describe('scope tag — rows belong to the scope they loaded under', () => {
  it('invalidates the rows the moment the scope changes, before the new load answers', async () => {
    const view = await mounted(U1)
    expect(ids(view.result.current.tokens)).toEqual(['A', 'B'])

    const pending = deferred<McpToken[]>()
    fetchTokens.mockReturnValueOnce(pending.promise)
    view.rerender({ scope: U2 })

    // No rows from u1 are visible under u2, and the panel reads as loading
    // rather than as an empty list belonging to nobody.
    expect(view.result.current.tokens).toEqual([])
    expect(view.result.current.loading).toBe(true)

    await act(async () => {
      pending.resolve([tokenC])
    })
    expect(fetchTokens).toHaveBeenLastCalledWith(U2)
    expect(ids(view.result.current.tokens)).toEqual(['C'])
  })

  it('sends a mutation to the scope on screen now, never the one it replaced', async () => {
    const view = await mounted(U1)
    view.rerender({ scope: U2 })
    await waitFor(() => expect(view.result.current.loading).toBe(false))

    revokeToken.mockResolvedValue(undefined)
    await act(async () => {
      await view.result.current.revoke('A')
    })
    expect(revokeToken).toHaveBeenCalledWith(U2, 'A')
    expect(revokeToken).not.toHaveBeenCalledWith(U1, 'A')
  })

  it('does not apply a mutation result to a scope that changed while it was in flight', async () => {
    const view = await mounted(U1)
    const slowRevoke = deferred<void>()
    revokeToken.mockReturnValueOnce(slowRevoke.promise)

    let done!: Promise<RevokeOutcome>
    await act(async () => {
      done = view.result.current.revoke('A')
    })
    expect(revokeToken).toHaveBeenCalledWith(U1, 'A')

    // The operator moves to another user while the DELETE is out.
    fetchTokens.mockResolvedValueOnce([tokenC])
    view.rerender({ scope: U2 })
    await waitFor(() => expect(view.result.current.loading).toBe(false))

    await act(async () => {
      slowRevoke.resolve()
      await done
    })

    // u2's list is untouched: the revoke belonged to u1 and its row-removal
    // cannot cross into another operator's table.
    expect(ids(view.result.current.tokens)).toEqual(['C'])
  })
})
