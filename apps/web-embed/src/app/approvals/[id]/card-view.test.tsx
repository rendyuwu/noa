import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApprovalCardLoad } from '@/lib/approvals/card'
import { POLL_INTERVAL_PENDING_MS, POLL_INTERVAL_RUN_MS, RUN_POLL_LIMIT } from '@/lib/approvals/poll'

import { CardView } from './card-view'
import {
  CARD_ID,
  approvalCard,
  approvedBody,
  cardBody,
} from '../../../../tests/support/approval-card'

/**
 * The card asking again until there is nothing left to wait for (§T.42 — V27, V29, V34, V38, V80).
 *
 * **What jsdom can prove is the loop**: that it starts, that it uses the right door and the right
 * interval, that every answer moves it to the right place, and — the half that matters most — that
 * it *stops*. A poll with no stopping condition passes every "it polled" assertion ever written.
 *
 * Fake timers throughout, so the 15-second pending wait costs nothing and so "no further calls"
 * can be asserted against a clock that has actually moved rather than against a `waitFor` that
 * merely ran out (V90's family, one lane over).
 */

const RESULT = 'Account acmeco suspended on alpha.'

function load(body: Record<string, unknown>): ApprovalCardLoad {
  return { kind: 'card', card: approvalCard(body) }
}

/** Answers to hand back, one per poll. The last one repeats, so a loop that will not stop shows. */
function stubPolls(...answers: (() => Response)[]): { calls: number } {
  const state = { calls: 0 }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    expect(String(input)).toBe(`/api/action-requests/${CARD_ID}`)
    const answer = answers[Math.min(state.calls, answers.length - 1)]!
    state.calls += 1
    return answer()
  })
  return state
}

/**
 * One turn of the loop: advance the clock, let the answer land, let the effect re-arm.
 *
 * At most one poll happens per call, whatever the clock is advanced by — React flushes passive
 * effects at the `act` boundary, so the timer for the *next* poll is only scheduled once this
 * returns. That is why "it stopped" is asserted over several of these rather than over one long
 * advance: a single big jump cannot tell a loop that stopped from a loop that had no chance to
 * re-arm (V87).
 */
async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function ticks(count: number, ms: number): Promise<void> {
  for (let index = 0; index < count; index += 1) await tick(ms)
}

describe('CardView', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('polls a running change until the run is terminal, then stops (V29, V34)', async () => {
    // The whole of V29 in one spec: the state lives in the database, so the frame re-reads the row
    // — and the outcome lands on the same URL that asked the question (V34).
    const polls = stubPolls(() =>
      Response.json(approvedBody({ status: 'COMPLETED', result_summary: RESULT })),
    )
    render(<CardView initial={load(approvedBody())} />)

    expect(screen.getByText('STARTED')).toBeTruthy()
    expect(polls.calls).toBe(0)

    await tick(POLL_INTERVAL_RUN_MS)

    expect(polls.calls).toBe(1)
    expect(screen.getByText('COMPLETED')).toBeTruthy()
    expect(screen.getByText(RESULT)).toBeTruthy()

    // The half that matters: a terminal run ends the loop.
    await ticks(5, POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(1)
  })

  it('never polls a card that is already terminal (V87)', async () => {
    // The separating case. Without it, "it polled until COMPLETED" passes just as well against a
    // component that polls forever and happened to be handed a terminal answer.
    const polls = stubPolls(() => Response.json(cardBody({ status: 'DENIED', csrf: null })))
    render(<CardView initial={load(cardBody({ status: 'DENIED', csrf: null }))} />)

    await ticks(5, POLL_INTERVAL_PENDING_MS)

    expect(polls.calls).toBe(0)
    expect(screen.getByText(/no longer awaiting a decision/i)).toBeTruthy()
  })

  it('waits the pending interval while nothing is executing', async () => {
    // Two speeds, and this is the one that separates them: at the run interval a PENDING card has
    // not asked yet.
    const polls = stubPolls(() => Response.json(cardBody()))
    render(<CardView initial={load(cardBody())} />)

    await tick(POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(0)

    await tick(POLL_INTERVAL_PENDING_MS - POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(1)
  })

  it('takes the Approve button away when the poll finds the request expired (V32, V38)', async () => {
    // An approval that expires under an open frame must stop offering a decision the door would
    // refuse — a button that cannot succeed reads as an action refused rather than never available.
    stubPolls(() => Response.json(cardBody({ status: 'EXPIRED', csrf: null })))
    render(<CardView initial={load(cardBody())} />)

    expect(screen.getByRole('button', { name: /approve/i })).toBeTruthy()

    await tick(POLL_INTERVAL_PENDING_MS)

    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.queryByLabelText(/why is this change/i)).toBeNull()
    expect(screen.getByText(/expired without an answer/i)).toBeTruthy()
  })

  it('replaces the card with the 401 state and no live button (V38)', async () => {
    stubPolls(() => Response.json({ error_code: 'session_invalid' }, { status: 401 }))
    render(<CardView initial={load(cardBody())} />)

    await tick(POLL_INTERVAL_PENDING_MS)

    expect(screen.getByText(/cannot authenticate here/i)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByLabelText(/why is this change/i)).toBeNull()
  })

  it('replaces the card with the one not-available sentence on a 404 (V27)', async () => {
    // Absent, another operator's and a deleted requester's all answer alike — one body from the
    // API, one sentence here.
    stubPolls(() => Response.json({ error_code: 'action_request_not_found' }, { status: 404 }))
    render(<CardView initial={load(cardBody())} />)

    await tick(POLL_INTERVAL_PENDING_MS)

    expect(screen.getByText(/does not exist, or it is not yours/i)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('keeps the card and keeps asking when NOA cannot be reached (V34, V87)', async () => {
    // The stop condition is "terminal", not "any answer". A transient failure must not blank a card
    // an operator is reading, and must not end the lifecycle this URL owns.
    const polls = stubPolls(
      () => new Response('', { status: 503 }),
      () => Response.json(approvedBody({ status: 'COMPLETED', result_summary: RESULT })),
    )
    render(<CardView initial={load(approvedBody())} />)

    await tick(POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(1)
    expect(screen.getByText('whm_suspend_account')).toBeTruthy()
    expect(screen.getByText('STARTED')).toBeTruthy()

    await tick(POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(2)
    expect(screen.getByText(RESULT)).toBeTruthy()
  })

  it('stops watching a run that never moves, and says so', async () => {
    // §T.38's executor is unbuilt: a STARTED run stays STARTED. Without the cap this loop would run
    // for as long as the frame is open. Giving up is not reported as a failure — NOA has no
    // evidence of one, only of not having been told.
    const polls = stubPolls(() => Response.json(approvedBody({ status: 'STARTED' })))
    render(<CardView initial={load(approvedBody())} />)

    await ticks(RUN_POLL_LIMIT, POLL_INTERVAL_RUN_MS)

    expect(polls.calls).toBe(RUN_POLL_LIMIT)
    expect(screen.getByText(/still running this change/i)).toBeTruthy()

    await ticks(5, POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(RUN_POLL_LIMIT)
  })

  it('renders no form, polling or not (V80)', async () => {
    // The sandbox LibreChat gives this frame omits `allow-forms` (R13, R29), so a native submit
    // dies silently in there. A re-render driven by a poll must not reintroduce one.
    stubPolls(() => Response.json(cardBody()))
    const { container } = render(<CardView initial={load(cardBody())} />)

    expect(container.querySelector('form')).toBeNull()
    await tick(POLL_INTERVAL_PENDING_MS)
    expect(container.querySelector('form')).toBeNull()
  })

  it.each([
    ['unauthenticated', /cannot authenticate here/i],
    ['not-found', /request not available/i],
    ['unavailable', /could not load this request/i],
  ] as const)('renders the %s state the server read, and polls nothing', async (kind, text) => {
    const polls = stubPolls(() => Response.json(cardBody()))
    const initial = (kind === 'unavailable' ? { kind, status: 503 } : { kind }) as ApprovalCardLoad
    render(<CardView initial={initial} />)

    expect(screen.getByText(text)).toBeTruthy()

    // Nothing to poll: there is no id in any of these answers, and inventing a retry here would be
    // a second definition of what the loader already decided.
    await ticks(5, POLL_INTERVAL_PENDING_MS)
    expect(polls.calls).toBe(0)
  })
})
