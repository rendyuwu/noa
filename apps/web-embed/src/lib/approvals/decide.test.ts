import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { decisionPath, submitDecision } from './decide'
import { describeDecision } from './outcome'

/**
 * The decision POST (§T.41, §T.42 — V15, V22, V39, V80).
 *
 * What a browser actually does with this — a `fetch` from inside a frame whose sandbox omits
 * `allow-forms` — is `e2e/approvals.browser.e2e.ts`'s claim, because jsdom cannot make it. What
 * these prove is the request this app builds and what it makes of each answer.
 */

const ID = '9f1c2b7e-0000-4000-8000-000000000000'
const CSRF = 'v1.1786000000.signature'
const REASON = 'Customer confirmed the account is compromised; suspending per ticket NOC-4471.'

type SeenRequest = { url: string; init: RequestInit | undefined }

function stubFetch(response: Response): SeenRequest[] {
  const seen: SeenRequest[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    seen.push({ url: String(input), init })
    return response.clone()
  })
  return seen
}

describe('decisionPath', () => {
  it('is a same-origin path on this app, never the API origin', () => {
    // `NOA_API_URL` is server-only and a browser cannot reach it (AGENTS.md, §T.44). The hop is
    // the proxy's, and both of these paths are on its allowlist.
    expect(decisionPath(ID, 'approve')).toBe(`/api/action-requests/${ID}/approve`)
    expect(decisionPath(ID, 'deny')).toBe(`/api/action-requests/${ID}/deny`)
  })

  it('encodes the id rather than letting it add path segments', () => {
    expect(decisionPath('../auth/login', 'approve')).toBe(
      '/api/action-requests/..%2Fauth%2Flogin/approve',
    )
  })
})

describe('submitDecision', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('POSTs the typed reason and the server-minted token', async () => {
    const seen = stubFetch(Response.json({ action_request_id: ID, tool_run_id: 'run-1' }))

    const outcome = await submitDecision({
      actionRequestId: ID,
      decision: 'approve',
      reason: REASON,
      csrf: CSRF,
    })

    expect(seen[0]?.url).toBe(`/api/action-requests/${ID}/approve`)
    expect(seen[0]?.init?.method).toBe('POST')
    expect(JSON.parse(String(seen[0]?.init?.body))).toEqual({ reason: REASON, csrf: CSRF })
    expect(outcome).toEqual({ kind: 'recorded', decision: 'approve' })
  })

  it('sends the cookie, which is what authenticates it', async () => {
    const seen = stubFetch(Response.json({}))

    await submitDecision({ actionRequestId: ID, decision: 'deny', reason: REASON, csrf: CSRF })

    expect(seen[0]?.init?.credentials).toBe('same-origin')
    expect(seen[0]?.init?.cache).toBe('no-store')
  })

  it('carries no status claim in the body', async () => {
    // "May this run?" is read from `action_requests.status`. There is nowhere in this body to
    // assert that a request is already approved, and this asserts the body stays that shape.
    const seen = stubFetch(Response.json({}))

    await submitDecision({ actionRequestId: ID, decision: 'approve', reason: REASON, csrf: CSRF })

    expect(Object.keys(JSON.parse(String(seen[0]?.init?.body))).sort()).toEqual(['csrf', 'reason'])
  })

  it('sends a blank reason rather than refusing it locally', async () => {
    // The gate is the endpoint: 409 `change_reason_required`, checked under the row lock against
    // the same rule the database CHECK holds. A second definition of "blank" here is one that can
    // disagree with those two, and two spellings of blank is one too many.
    const seen = stubFetch(
      Response.json({ error_code: 'change_reason_required', message: 'A reason is required.' }, {
        status: 409,
      }),
    )

    const outcome = await submitDecision({
      actionRequestId: ID,
      decision: 'approve',
      reason: '   ',
      csrf: CSRF,
    })

    expect(JSON.parse(String(seen[0]?.init?.body)).reason).toBe('   ')
    expect(outcome).toEqual({
      kind: 'refused',
      decision: 'approve',
      errorCode: 'change_reason_required',
      message: 'A reason is required.',
    })
  })

  it.each([
    [403, 'csrf_token_invalid'],
    [404, 'action_request_not_found'],
    [409, 'action_request_already_decided'],
    [409, 'action_request_expired'],
  ])('reports the API’s own code for a %i (%s)', async (status: number, errorCode: string) => {
    // Never invented here: the codes are `core.approvals.errors`', and an operator reading one
    // and an administrator reading the log are looking at the same string.
    stubFetch(Response.json({ error_code: errorCode, message: '' }, { status }))

    const outcome = await submitDecision({
      actionRequestId: ID,
      decision: 'approve',
      reason: REASON,
      csrf: CSRF,
    })

    expect(outcome).toEqual({ kind: 'refused', decision: 'approve', errorCode, message: '' })
  })

  it('falls back to the status when a refusal carries no code', async () => {
    stubFetch(new Response('<html>gateway</html>', { status: 502 }))

    const outcome = await submitDecision({
      actionRequestId: ID,
      decision: 'deny',
      reason: REASON,
      csrf: CSRF,
    })

    expect(outcome).toEqual({
      kind: 'refused',
      decision: 'deny',
      errorCode: 'http_502',
      message: '',
    })
  })

  it('tells a failed POST apart from a refused one', async () => {
    // "NOA refused this" and "the request never arrived" are different things to say to someone
    // standing in front of a pending change: one is answered, the other is not.
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      throw new TypeError('fetch failed')
    })

    expect(
      await submitDecision({
        actionRequestId: ID,
        decision: 'approve',
        reason: REASON,
        csrf: CSRF,
      }),
    ).toEqual({ kind: 'unreachable', decision: 'approve' })
  })
})

describe('describeDecision', () => {
  it('prefers the API’s message, which already names the remedy', () => {
    expect(
      describeDecision({
        kind: 'refused',
        decision: 'approve',
        errorCode: 'csrf_token_invalid',
        message: 'This approval card is no longer valid. Reload it and try again.',
      }),
    ).toBe('This approval card is no longer valid. Reload it and try again.')
  })

  it('has a sentence of its own when a refusal arrives without one', () => {
    expect(
      describeDecision({
        kind: 'refused',
        decision: 'approve',
        errorCode: 'change_reason_required',
        message: '',
      }),
    ).toBe('Type why this change is being made or refused.')
  })

  it('names an unrecognised code rather than swallowing it', () => {
    expect(
      describeDecision({
        kind: 'refused',
        decision: 'deny',
        errorCode: 'something_new',
        message: '',
      }),
    ).toContain('something_new')
  })

  it('separates approve from deny, and both from a failure', () => {
    const approved = describeDecision({ kind: 'recorded', decision: 'approve' })
    const denied = describeDecision({ kind: 'recorded', decision: 'deny' })
    const failed = describeDecision({ kind: 'unreachable', decision: 'approve' })

    expect(new Set([approved, denied, failed]).size).toBe(3)
    expect(denied).toContain('Nothing was changed')
  })
})
