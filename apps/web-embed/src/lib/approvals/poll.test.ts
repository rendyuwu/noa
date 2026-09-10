import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  POLL_INTERVAL_PENDING_MS,
  POLL_INTERVAL_RUN_MS,
  RUN_POLL_LIMIT,
  fetchApprovalCard,
  isRunning,
  isStalled,
  isTerminal,
  pollIntervalMs,
  pollPath,
} from './poll'
import { CARD_ID, approvalCard, approvedBody, cardBody, runBody } from '../../../tests/support/approval-card'

/**
 * When the card asks again, and when it stops (§T.42 — V29, V34, V38, V27).
 *
 * The predicates here are the whole loop: everything `card-view.tsx` does is wait
 * `pollIntervalMs`, call `fetchApprovalCard`, and stop when `isTerminal` or `isStalled` says so. So
 * they are tested as values, where every state can be named, and the component spec then proves the
 * loop actually obeys them.
 */

describe('pollPath', () => {
  it('is a path on this origin, never the API', () => {
    // `NOA_API_URL` is server-only and a browser could not reach it anyway (§T.44, AGENTS.md). The
    // proxy's allowlist carries this exact entry, planted for this row.
    expect(pollPath(CARD_ID)).toBe(`/api/action-requests/${CARD_ID}`)
  })

  it('encodes the id it was handed', () => {
    expect(pollPath('a/../b')).toBe('/api/action-requests/a%2F..%2Fb')
  })
})

describe('isRunning', () => {
  it.each([
    ['a run still going', approvedBody({ status: 'STARTED' })],
    // V29 puts the run row in the decision's transaction, so this is a read that straddled the
    // commit, not a change that will never run — asking again is the right answer to it.
    ['an approval whose run has not been read yet', cardBody({ status: 'APPROVED', csrf: null })],
  ])('is true for %s', (_label: string, body: Record<string, unknown>) => {
    expect(isRunning(approvalCard(body))).toBe(true)
  })

  it.each([
    ['a finished run', approvedBody({ status: 'COMPLETED' })],
    ['a failed run', approvedBody({ status: 'FAILED' })],
    ['a pending request', cardBody()],
    ['a denial', cardBody({ status: 'DENIED', csrf: null })],
  ])('is false for %s', (_label: string, body: Record<string, unknown>) => {
    expect(isRunning(approvalCard(body))).toBe(false)
  })
})

describe('isTerminal', () => {
  it.each([
    ['PENDING', cardBody()],
    ['APPROVED with a run still going', approvedBody({ status: 'STARTED' })],
  ])('keeps asking while %s', (_label: string, body: Record<string, unknown>) => {
    expect(isTerminal(approvalCard(body))).toBe(false)
  })

  it.each([
    ['APPROVED and finished', approvedBody({ status: 'COMPLETED' })],
    ['APPROVED and failed', approvedBody({ status: 'FAILED' })],
    ['DENIED', cardBody({ status: 'DENIED', csrf: null })],
    ['EXPIRED', cardBody({ status: 'EXPIRED', csrf: null })],
  ])('stops once %s', (_label: string, body: Record<string, unknown>) => {
    expect(isTerminal(approvalCard(body))).toBe(true)
  })

  it('stops on a status this build has never heard of', () => {
    // The conservative direction: a build that cannot say what a status means also cannot say what
    // would end it, and a loop with no stopping condition is worse than a card someone reloads.
    expect(isTerminal(approvalCard({ status: 'SOMETHING_NEW', csrf: null }))).toBe(true)
  })
})

describe('pollIntervalMs', () => {
  it('waits longer while nothing is executing', () => {
    // The only transition available to a PENDING request is the expiry sweep, an hour out by
    // default. Nobody is standing by for it.
    expect(pollIntervalMs(approvalCard())).toBe(POLL_INTERVAL_PENDING_MS)
  })

  it('waits briefly while a run is in flight', () => {
    expect(pollIntervalMs(approvalCard(approvedBody()))).toBe(POLL_INTERVAL_RUN_MS)
  })

  it('separates — the two speeds are not the same number', () => {
    // Without this the two assertions above pass just as well against one constant used twice.
    expect(POLL_INTERVAL_RUN_MS).toBeLessThan(POLL_INTERVAL_PENDING_MS)
  })
})

describe('isStalled', () => {
  const running = approvalCard(approvedBody())

  it('is false while the run has been watched for less than the limit', () => {
    expect(isStalled(running, RUN_POLL_LIMIT - 1)).toBe(false)
  })

  it('is true once it has been watched for the limit', () => {
    // §T.38's executor is unbuilt, so a STARTED run never moves today; without this cap an open
    // frame would poll every two seconds for as long as it stays open.
    expect(isStalled(running, RUN_POLL_LIMIT)).toBe(true)
  })

  it('never caps a PENDING request, however long it has been open', () => {
    // Only the run is capped: a PENDING request has a server-side terminator in the sweep, so its
    // loop ends whether or not anyone is looking.
    expect(isStalled(approvalCard(), RUN_POLL_LIMIT * 10)).toBe(false)
  })
})

describe('fetchApprovalCard', () => {
  function stub(answer: () => Response | Promise<Response>): { calls: RequestInit[] } {
    const calls: RequestInit[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      calls.push({ ...init, method: init?.method ?? 'GET' })
      expect(String(input)).toBe(pollPath(CARD_ID))
      return answer()
    })
    return { calls }
  }

  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reads a card back, with the cookie and no cache', async () => {
    const { calls } = stub(() => Response.json(approvedBody({ status: 'COMPLETED' })))

    const load = await fetchApprovalCard(CARD_ID)

    expect(load.kind).toBe('card')
    expect(load.kind === 'card' && load.card.run?.status).toBe('COMPLETED')
    expect(calls[0]?.method).toBe('GET')
    // The mechanism, asserted: the session cookie is what authenticates this, and a cached answer
    // would be one operator's session state served to the next read.
    expect(calls[0]?.credentials).toBe('same-origin')
    expect(calls[0]?.cache).toBe('no-store')
  })

  it('reports a 401 as its own state', async () => {
    // A session can expire under an open frame. That must become "cannot authenticate here", not a
    // card left standing with a live Approve button on it.
    stub(() => Response.json({ error_code: 'session_invalid' }, { status: 401 }))

    expect((await fetchApprovalCard(CARD_ID)).kind).toBe('unauthenticated')
  })

  it('reports a 404 as its own state', async () => {
    stub(() =>
      Response.json({ error_code: 'action_request_not_found' }, { status: 404 }),
    )

    expect((await fetchApprovalCard(CARD_ID)).kind).toBe('not-found')
  })

  it.each([500, 502, 503])('reports a %d as unavailable, not as an answer', async (status) => {
    // Deliberately not terminal: the caller keeps asking. "NOA could not be reached just now" and
    // "there is nothing more to wait for" are different facts.
    stub(() => new Response('', { status }))

    expect(await fetchApprovalCard(CARD_ID)).toEqual({ kind: 'unavailable', status })
  })

  it('reports a network failure as unavailable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      throw new TypeError('fetch failed')
    })

    expect(await fetchApprovalCard(CARD_ID)).toEqual({ kind: 'unavailable', status: 0 })
  })

  it('refuses a 200 whose body is not a card', async () => {
    // A broken deployment, not an empty card: rendering the fields as blanks would put an Approve
    // button on top of nothing (V38's family).
    stub(() => Response.json({ not: 'a card' }))

    expect(await fetchApprovalCard(CARD_ID)).toEqual({ kind: 'unavailable', status: 200 })
  })

  it('refuses a 200 that is not JSON at all', async () => {
    stub(() => new Response('<html>gateway</html>', { status: 200 }))

    expect(await fetchApprovalCard(CARD_ID)).toEqual({ kind: 'unavailable', status: 200 })
  })

  it('never carries a reason back', async () => {
    // The API sends none. This asserts that a body which smuggled one in still cannot reach a
    // render path through this door.
    stub(() => Response.json({ ...cardBody({ run: runBody() }), reason: 'smuggled' }))
    const load = await fetchApprovalCard(CARD_ID)

    expect(JSON.stringify(load)).not.toContain('smuggled')
  })
})
