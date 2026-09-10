import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { loadApprovalCard } from './detail'

/**
 * The server-side card read (§T.41), with `fetch` stubbed so every assertion is about what this
 * app sends upstream and what it makes of the answer.
 *
 * Two of these are the same claims §T.44 makes about the proxy, re-proven against this loader
 * rather than cited from it: the cookie is forwarded, and `Authorization` is not. They are
 * separate code paths — the proxy filters a browser's headers, this one builds its own — so a
 * guarantee held in one place says nothing about the other.
 */

const ID = '9f1c2b7e-0000-4000-8000-000000000000'

const BODY = {
  action_request_id: ID,
  tool_name: 'whm_suspend_account',
  status: 'PENDING',
  conversation_ref: null,
  requester: { email: 'operator@noa.internal', librechat_user_id: 'librechat-user-1' },
  arguments: { account: 'acmeco' },
  evidence: { suspended: false },
  created_at: '2026-08-08T09:00:00+00:00',
  expires_at: '2026-08-08T10:00:00+00:00',
  decided_at: null,
  run: null,
  csrf: 'v1.1786000000.signature',
}

type SeenRequest = { url: string; init: RequestInit | undefined }

function stubFetch(response: Response): SeenRequest[] {
  const seen: SeenRequest[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    seen.push({ url: String(input), init })
    // Cloned, so a test that calls twice reads the body twice.
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

describe('loadApprovalCard', () => {
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

  it('asks the API for this request and parses the card', async () => {
    const seen = stubFetch(Response.json(BODY))

    const load = await loadApprovalCard(ID, { cookie: 'noa_session=abc' })

    expect(seen[0]?.url).toBe(`http://backend.test/action-requests/${ID}`)
    expect(load).toEqual({
      kind: 'card',
      card: expect.objectContaining({ actionRequestId: ID, csrf: BODY.csrf }),
    })
  })

  it('forwards the browser’s session cookie', async () => {
    // Without this the card authenticates as nobody and every operator sees the 401 state.
    const seen = stubFetch(Response.json(BODY))

    await loadApprovalCard(ID, { cookie: 'noa_session=abc.def.ghi' })

    expect(new Headers(seen[0]?.init?.headers).get('cookie')).toBe('noa_session=abc.def.ghi')
  })

  it('sends no Authorization header (C5, §T.44(d))', async () => {
    // MCP bearer tokens are LibreChat's to send. This origin must not be a relay for one, and
    // that includes the server-side read — not only the browser-facing proxy.
    const seen = stubFetch(Response.json(BODY))

    await loadApprovalCard(ID, { cookie: 'noa_session=abc' })

    expect(new Headers(seen[0]?.init?.headers).has('authorization')).toBe(false)
  })

  it('never caches the answer', async () => {
    // It is one operator's session state plus a freshly minted CSRF token. A cached copy is
    // another operator's card, or a stale token, or both.
    const seen = stubFetch(Response.json(BODY))

    await loadApprovalCard(ID, { cookie: 'noa_session=abc' })

    expect(seen[0]?.init?.cache).toBe('no-store')
  })

  it('omits the cookie header entirely when the browser sent none', async () => {
    const seen = stubFetch(Response.json(BODY))

    await loadApprovalCard(ID, { cookie: null })

    expect(new Headers(seen[0]?.init?.headers).has('cookie')).toBe(false)
  })

  it('encodes the id rather than letting it shape the path', async () => {
    // The id is not shape-checked (V27 owns that, and answers absent/malformed/foreign alike), so
    // what matters is that it cannot become extra path segments.
    const seen = stubFetch(Response.json(BODY))

    await loadApprovalCard('../auth/login', { cookie: null })

    expect(seen[0]?.url).toBe('http://backend.test/action-requests/..%2Fauth%2Flogin')
  })

  it('maps 401 to the “cannot authenticate here” state, and asks upstream first', async () => {
    // The `toHaveLength(1)` is the part worth keeping: this loader does not shortcut a missing
    // cookie into a 401 of its own. Whether a session is valid is the API's judgement — V6's row
    // re-read lives there — and a client-side guess would answer "not signed in" for reasons that
    // have nothing to do with the session.
    const seen = stubFetch(Response.json({ error_code: 'session_invalid' }, { status: 401 }))

    const load = await loadApprovalCard(ID, { cookie: null })

    expect(seen).toHaveLength(1)
    expect(load).toEqual({ kind: 'unauthenticated' })
  })

  it('maps 404 to one not-found state', async () => {
    stubFetch(Response.json({ error_code: 'action_request_not_found' }, { status: 404 }))

    expect(await loadApprovalCard(ID, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'not-found',
    })
  })

  it.each([403, 409, 500, 502])('maps %i to unavailable, not to a card', async (status: number) => {
    // "Could not be asked" is neither "does not exist" nor "not signed in", and rendering it as
    // either sends an operator to the wrong person.
    stubFetch(Response.json({ error_code: 'nope' }, { status }))

    expect(await loadApprovalCard(ID, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'unavailable',
      status,
    })
  })

  it('maps an unreachable API to unavailable rather than throwing', async () => {
    stubUnreachableFetch()

    expect(await loadApprovalCard(ID, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'unavailable',
      status: 0,
    })
  })

  it('refuses a 200 whose body is not a card', async () => {
    // A broken deployment, not an empty card: rendering blank fields would put an Approve button
    // on top of nothing (V38's family).
    stubFetch(Response.json({ unexpected: true }))

    expect(await loadApprovalCard(ID, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'unavailable',
      status: 200,
    })
  })

  it('refuses a 200 that is not JSON at all', async () => {
    stubFetch(new Response('<html>login page</html>', { status: 200 }))

    expect(await loadApprovalCard(ID, { cookie: 'noa_session=abc' })).toEqual({
      kind: 'unavailable',
      status: 200,
    })
  })
})
