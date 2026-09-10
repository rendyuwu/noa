import { beforeEach, describe, expect, it, vi } from 'vitest'

const clearAuth = vi.fn()
const isClearAuthInProgress = vi.fn().mockReturnValue(false)
const reportClientError = vi.fn()

vi.mock('@/lib/auth/session', () => {
  class _AuthRedirectError extends Error {
    constructor(reason = 'session_expired') {
      super(`Auth redirect in progress (${reason})`)
      this.name = 'AuthRedirectError'
    }
  }
  return {
    clearAuth: (...args: unknown[]) => clearAuth(...args),
    isClearAuthInProgress: () => isClearAuthInProgress(),
    AuthRedirectError: _AuthRedirectError,
  }
})

vi.mock('@/lib/observability/error-reporting', () => ({
  reportClientError: (...args: unknown[]) => reportClientError(...args),
}))

import { ApiError, fetchWithAuth, getApiUrl, jsonOrThrow } from './fetch-helper'

describe('getApiUrl', () => {
  it('is the same-origin /api prefix (NOA_API_URL never reaches the browser)', () => {
    expect(getApiUrl()).toBe('/api')
  })
})

describe('fetchWithAuth', () => {
  beforeEach(() => {
    clearAuth.mockReset()
    isClearAuthInProgress.mockReset().mockReturnValue(false)
    reportClientError.mockReset()
    vi.restoreAllMocks()
  })

  it('rejects absolute URLs', async () => {
    await expect(fetchWithAuth('http://evil.test/x')).rejects.toThrow(/absolute URL/)
  })

  it('short-circuits with AuthRedirectError while a logout is in flight', async () => {
    isClearAuthInProgress.mockReturnValue(true)
    await expect(fetchWithAuth('/threads')).rejects.toMatchObject({ name: 'AuthRedirectError' })
  })

  it('prefixes bare paths with /api and sends credentials', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
    await fetchWithAuth('/threads')
    expect(spy).toHaveBeenCalledWith('/api/threads', expect.objectContaining({ credentials: 'include' }))
  })

  it('leaves already-/api paths unchanged', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
    await fetchWithAuth('/api/assistant')
    expect(spy).toHaveBeenCalledWith('/api/assistant', expect.anything())
  })

  it('triggers the session-expiry flow and throws on a 401', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 401 }))
    await expect(fetchWithAuth('/auth/me')).rejects.toMatchObject({ name: 'AuthRedirectError' })
    expect(clearAuth).toHaveBeenCalledWith('session_expired')
  })

  it('reports rejected fetch failures and rethrows the original error', async () => {
    const networkError = new TypeError('Failed to fetch')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(networkError)
    await expect(fetchWithAuth('/threads')).rejects.toBe(networkError)
    expect(reportClientError).toHaveBeenCalledWith(networkError)
    expect(clearAuth).not.toHaveBeenCalled()
  })

  it('preserves the original fetch error even if reporting throws', async () => {
    const networkError = new TypeError('Failed to fetch')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(networkError)
    reportClientError.mockImplementationOnce(() => {
      throw new Error('reporting failed')
    })
    await expect(fetchWithAuth('/threads')).rejects.toBe(networkError)
  })
})

describe('jsonOrThrow', () => {
  beforeEach(() => {
    reportClientError.mockReset()
    vi.restoreAllMocks()
  })

  it('returns the parsed body on success', async () => {
    const res = new Response(JSON.stringify({ ok: true }), { status: 200 })
    await expect(jsonOrThrow<{ ok: boolean }>(res)).resolves.toEqual({ ok: true })
  })

  it('preserves snake_case detail/error_code/request_id as a typed ApiError', async () => {
    const res = new Response(
      JSON.stringify({ detail: 'User pending approval', error_code: 'user_pending_approval', request_id: 'req-123' }),
      { status: 403, headers: { 'content-type': 'application/json' } },
    )

    let thrown: unknown
    try {
      await jsonOrThrow(res)
    } catch (error) {
      thrown = error
    }

    expect(thrown).toBeInstanceOf(ApiError)
    expect(thrown).toMatchObject({
      status: 403,
      detail: 'User pending approval',
      errorCode: 'user_pending_approval',
      requestId: 'req-123',
    })
    expect(reportClientError).not.toHaveBeenCalled()
  })

  it("surfaces NOA's `message`, which is the field its error envelope actually carries", async () => {
    // The API's envelope is { error_code, message, request_id } — `detail` is the internal
    // diagnostic and is deliberately kept out of response bodies (the envelope carries only
    // error_code, message, request_id). Reading only `detail`
    // rendered every refusal as "Request failed (409)".
    const res = new Response(
      JSON.stringify({
        error_code: 'whm_server_name_exists',
        message: 'A WHM server with that name already exists. Choose a different name.',
        request_id: 'req-409',
      }),
      { status: 409, headers: { 'content-type': 'application/json' } },
    )

    await expect(jsonOrThrow(res)).rejects.toMatchObject({
      status: 409,
      detail: 'A WHM server with that name already exists. Choose a different name.',
      errorCode: 'whm_server_name_exists',
      requestId: 'req-409',
    })
  })

  it('still falls back to the status line when a body carries neither field', async () => {
    // The negative control: without it, "the message was surfaced" would pass against a
    // helper that surfaced anything at all.
    const res = new Response(JSON.stringify({ error_code: 'whm_server_not_found' }), {
      status: 404,
      headers: { 'content-type': 'application/json' },
    })

    await expect(jsonOrThrow(res)).rejects.toMatchObject({ detail: 'Request failed (404)' })
  })

  it('falls back to the x-request-id header when request_id is absent', async () => {
    const res = new Response(JSON.stringify({ detail: 'Forbidden' }), {
      status: 403,
      headers: { 'content-type': 'application/json', 'x-request-id': 'req-header-456' },
    })
    await expect(jsonOrThrow(res)).rejects.toMatchObject({ requestId: 'req-header-456' })
    expect(reportClientError).not.toHaveBeenCalled()
  })

  it('reports 5xx failures with normalized context', async () => {
    const res = new Response(
      JSON.stringify({ detail: 'Internal error', error_code: 'internal_server_error', request_id: 'req-500' }),
      { status: 503, headers: { 'content-type': 'application/json' } },
    )
    await expect(jsonOrThrow(res)).rejects.toMatchObject({ status: 503 })
    expect(reportClientError).toHaveBeenCalledWith(expect.any(ApiError), {
      errorCode: 'internal_server_error',
      requestId: 'req-500',
      status: 503,
    })
  })

  it('reports proxy-unreachable status 0 failures', async () => {
    const res = {
      headers: new Headers(),
      json: async () => ({ detail: 'Unable to reach API' }),
      ok: false,
      status: 0,
    } as Response
    await expect(jsonOrThrow(res)).rejects.toMatchObject({ status: 0 })
    expect(reportClientError).toHaveBeenCalled()
  })
})
