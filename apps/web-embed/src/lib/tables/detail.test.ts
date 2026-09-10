import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { loadResultTable } from './detail'

/**
 * The server-side table read (§T.56), with `fetch` stubbed so every assertion is about what this
 * app sends upstream and what it makes of the answer.
 *
 * Two of these are the same claims §T.44 makes about the proxy and §T.41 makes about the card's
 * loader, re-proven against *this* loader rather than cited from either: the cookie is
 * forwarded, and `Authorization` is not. Three separate code paths, so a guarantee held in two of
 * them says nothing about the third.
 */

const TOKEN = 'table-token-1'

const BODY = {
  token: TOKEN,
  tool_name: 'whm_list_accounts',
  columns: [{ key: 'user', label: 'Account' }],
  rows: [{ user: 'acmeco' }],
  total_rows: 1,
  stored_rows: 1,
  truncated: false,
  created_at: '2026-08-09T09:00:00+00:00',
  expires_at: '2026-08-10T09:00:00+00:00',
}

type SeenRequest = { url: string; init: RequestInit | undefined }

function stubFetch(response: Response): SeenRequest[] {
  const seen: SeenRequest[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    seen.push({ url: String(input), init })
    return response.clone()
  })
  return seen
}

/** A `fetch` that never answers — the API being unreachable from this server. */
function stubUnreachableFetch(): void {
  vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
    throw new TypeError('fetch failed')
  })
}

function headersOf(seen: SeenRequest[]): Headers {
  return new Headers(seen[0]?.init?.headers)
}

describe('loadResultTable', () => {
  const original = process.env.NOA_API_URL

  beforeEach(() => {
    process.env.NOA_API_URL = 'http://backend.test'
    vi.restoreAllMocks()
  })

  afterEach(() => {
    if (original === undefined) delete process.env.NOA_API_URL
    else process.env.NOA_API_URL = original
    vi.restoreAllMocks()
  })

  it('asks the API for this table and parses it', async () => {
    const seen = stubFetch(Response.json(BODY))

    const load = await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })

    expect(seen[0]?.url).toBe(`http://backend.test/tables/${TOKEN}`)
    expect(load).toEqual({
      kind: 'table',
      table: expect.objectContaining({ token: TOKEN, toolName: 'whm_list_accounts' }),
    })
  })

  it('forwards the browser’s session cookie', async () => {
    // Without this the read authenticates as nobody and every operator sees the 401 state.
    const seen = stubFetch(Response.json(BODY))

    await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })

    expect(headersOf(seen).get('cookie')).toBe('noa_session=abc')
  })

  it('never sends an Authorization header (C5, §T.44(d))', async () => {
    // Every surface this app reaches is cookie-authenticated. A bearer token is LibreChat's to
    // send, and a loader that relayed one would make this origin a relay for it.
    const seen = stubFetch(Response.json(BODY))

    await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })

    expect(headersOf(seen).has('authorization')).toBe(false)
  })

  it('encodes the token into the path', async () => {
    const seen = stubFetch(Response.json(BODY))

    await loadResultTable('a/../b', { cookie: null })

    expect(seen[0]?.url).toBe('http://backend.test/tables/a%2F..%2Fb')
  })

  it('never caches the answer', async () => {
    // One operator's rows behind one operator's session: a cached copy would be somebody else's.
    const seen = stubFetch(Response.json(BODY))

    await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })

    expect(seen[0]?.init?.cache).toBe('no-store')
  })

  it('reports a 401 as its own state', async () => {
    stubFetch(new Response(null, { status: 401 }))

    expect(await loadResultTable(TOKEN, { cookie: null })).toEqual({ kind: 'unauthenticated' })
  })

  it('reports a 404 as its own state', async () => {
    // Unknown, another operator's, a deleted requester's and an expired one all arrive here.
    stubFetch(new Response(null, { status: 404 }))

    expect(await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'not-found',
    })
  })

  it('reports an unreachable API separately from a 404', async () => {
    // "Does not exist" and "could not be asked" send an operator to different people.
    stubUnreachableFetch()

    expect(await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'unavailable',
      status: 0,
    })
  })

  it('treats a 200 that is not a table as unavailable, never as an empty table', async () => {
    // Rendering headings over nothing would tell an operator the listing came back empty.
    stubFetch(Response.json({ nothing: 'useful' }))

    expect(await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'unavailable',
      status: 200,
    })
  })

  it('treats a 200 that is not JSON as unavailable', async () => {
    stubFetch(new Response('<html>maintenance</html>', { status: 200 }))

    expect(await loadResultTable(TOKEN, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'unavailable',
      status: 200,
    })
  })
})
