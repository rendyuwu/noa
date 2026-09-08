import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import { fetchTokens, mintToken, revokeToken, tokenBasePath } from './tokens-api'
import type { TokenScope } from './types'

// Keep the real jsonOrThrow / ApiError (so the stable-error contract is actually
// exercised) and only stub the network boundary — the shape used by
// `users-api.test.ts:16-19`.
vi.mock('@/lib/auth/fetch-helper', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/auth/fetch-helper')>()
  return { ...actual, fetchWithAuth: vi.fn() }
})

const { fetchWithAuth } = await import('@/lib/auth/fetch-helper')
const mockFetch = fetchWithAuth as unknown as Mock

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}): Response {
  const { ok = true, status = 200 } = init
  return {
    ok,
    status,
    json: async () => body,
    headers: { get: () => null },
  } as unknown as Response
}

// Typed views over the last call, so the assertions below read the arguments
// without the test itself reaching through `any`.
const lastCall = (): [string, RequestInit | undefined] | undefined =>
  mockFetch.mock.lastCall as [string, RequestInit | undefined] | undefined
const lastPath = (): string | undefined => lastCall()?.[0]
const lastBody = (): BodyInit | null | undefined => lastCall()?.[1]?.body

const SELF: TokenScope = { kind: 'self' }
const USER: TokenScope = { kind: 'user', userId: 'u1' }

const row = {
  id: 't1',
  user_id: 'u1',
  token_prefix: 'noa_abcd1234',
  label: 'laptop',
  librechat_user_id: null,
  last_used_at: null,
  last_ldap_check_at: null,
  expires_at: null,
  created_at: '2026-09-01T00:00:00Z',
}

beforeEach(() => {
  mockFetch.mockReset()
})

// The scope union is the only thing that picks a path, and it has to pick
// the same one for all three verbs. A self action reaching an admin path (or the
// reverse) is the failure this pins.
describe('tokenBasePath', () => {
  it('maps each scope to its router prefix', () => {
    expect(tokenBasePath(SELF)).toBe('/me/mcp-tokens')
    expect(tokenBasePath(USER)).toBe('/admin/users/u1/tokens')
  })

  it('is the path every verb uses, for both scopes', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ tokens: [], token: row, plaintext: 'x', ok: true }))

    await fetchTokens(SELF)
    expect(mockFetch).toHaveBeenLastCalledWith('/me/mcp-tokens')
    await fetchTokens(USER)
    expect(mockFetch).toHaveBeenLastCalledWith('/admin/users/u1/tokens')

    await mintToken(SELF, null)
    expect(lastPath()).toBe('/me/mcp-tokens')
    await mintToken(USER, null)
    expect(lastPath()).toBe('/admin/users/u1/tokens')

    await revokeToken(SELF, 't1')
    expect(mockFetch).toHaveBeenLastCalledWith('/me/mcp-tokens/t1', { method: 'DELETE' })
    await revokeToken(USER, 't1')
    expect(mockFetch).toHaveBeenLastCalledWith('/admin/users/u1/tokens/t1', { method: 'DELETE' })
  })
})

describe('fetchTokens', () => {
  it('unwraps { tokens: [...] }', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ tokens: [row] }))
    await expect(fetchTokens(SELF)).resolves.toEqual([row])
  })

  it('answers an empty list, not a crash, when the payload has no array', async () => {
    mockFetch.mockResolvedValue(jsonResponse({}))
    await expect(fetchTokens(SELF)).resolves.toEqual([])
  })
})

// The label is trimmed and a blank one is sent as null. The service
// normalises blanks itself (`_validate_label`, mcp_token_service.py:276-290);
// this asserts the client sends what an unnamed token MEANS rather than leaning
// on that.
describe('mintToken', () => {
  beforeEach(() => {
    mockFetch.mockResolvedValue(jsonResponse({ token: row, plaintext: 'noa_secret' }))
  })

  it('POSTs JSON with the trimmed label', async () => {
    await mintToken(SELF, '  laptop  ')
    expect(mockFetch).toHaveBeenCalledWith('/me/mcp-tokens', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ label: 'laptop' }),
    })
  })

  it('sends label null for a blank, whitespace-only or absent label', async () => {
    const bodies: unknown[] = []
    for (const label of ['', '   ', '\t\n', null]) {
      await mintToken(SELF, label)
      bodies.push(lastBody())
    }
    expect(bodies).toEqual([
      JSON.stringify({ label: null }),
      JSON.stringify({ label: null }),
      JSON.stringify({ label: null }),
      JSON.stringify({ label: null }),
    ])
  })

  it('returns the row and the plaintext exactly as the API sent them', async () => {
    await expect(mintToken(SELF, 'laptop')).resolves.toEqual({
      token: row,
      plaintext: 'noa_secret',
    })
  })
})

// Revoke = delete; `{ok: true}` is the whole result.
describe('revokeToken', () => {
  it('resolves on { ok: true }', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ok: true }))
    await expect(revokeToken(USER, 't1')).resolves.toBeUndefined()
  })

  it('rejects with the 404 the backend gives for absent, foreign and fabricated ids alike', async () => {
    mockFetch.mockResolvedValue(
      jsonResponse(
        { message: 'MCP token not found', error_code: 'mcp_token_not_found' },
        { ok: false, status: 404 },
      ),
    )
    await expect(revokeToken(SELF, 'nope')).rejects.toMatchObject({
      status: 404,
      errorCode: 'mcp_token_not_found',
    })
  })
})

// The backend's wording is what the operator reads. `jsonOrThrow` prefers
// `message`, which is the field NOA's error envelope actually carries (V8).
describe('error surfacing', () => {
  it('preserves the backend message and error_code on a 400 invalid_token_label', async () => {
    mockFetch.mockResolvedValue(
      jsonResponse(
        {
          message: 'Token label must be at most 255 characters',
          error_code: 'invalid_token_label',
          request_id: 'req-7',
        },
        { ok: false, status: 400 },
      ),
    )

    const failure = await mintToken(SELF, 'x'.repeat(256)).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(ApiError)
    expect(failure).toMatchObject({
      status: 400,
      detail: 'Token label must be at most 255 characters',
      errorCode: 'invalid_token_label',
      requestId: 'req-7',
    })
  })
})
