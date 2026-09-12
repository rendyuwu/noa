import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApprovalCardLoad } from '@/lib/approvals/card'
import { POLL_INTERVAL_PENDING_MS, POLL_INTERVAL_RUN_MS, RUN_POLL_LIMIT } from '@/lib/approvals/poll'

import { COPY_BLOCK_IGNORE } from '@/components/copy-summary'

import { CardView } from './card-view'
import {
  CARD_ID,
  approvalCard,
  approvedBody,
  cardBody,
  receiptBody,
} from '../../../../tests/support/approval-card'

/**
 * The card asking again until there is nothing left to wait for — requester-matched, state in the
 * DB, one URL through the receipt, JS `fetch` only.
 *
 * **What jsdom can prove is the loop**: that it starts, that it uses the right door and the right
 * interval, that every answer moves it to the right place, and — the half that matters most — that
 * it *stops*. A poll with no stopping condition passes every "it polled" assertion ever written.
 *
 * Fake timers throughout, so the 15-second pending wait costs nothing and so "no further calls"
 * can be asserted against a clock that has actually moved rather than against a `waitFor` that
 * merely ran out — the setup gate sits one layer below the subject, one lane over.
 */

const RESULT = 'Account acmeco suspended on alpha.'
const SIGN_IN = 'https://admin.noa.internal/login'

/**
 * What "the visible card says this" means, now that the copy control renders the record twice.
 *
 * The off-screen block is in the DOM deliberately — a selection cannot cover a `display: none`
 * element — and Testing Library does not filter on visibility, so an assertion that a value is
 * *not* printed matches the copy of it in there and reads as a pass. The selector is the copy
 * control's own export, because both halves of it are load-bearing and the reason is written
 * where the block is: a card keeping its own spelling of it is one edit away from the half that
 * does the work going missing.
 *
 * `script, style` is Testing Library's own default, restated because passing `ignore` replaces it.
 *
 * Used on the assertions that would otherwise pass or drift silently: absences, and counts. A
 * presence assertion needs no scoping — it throws on the ambiguity rather than swallowing it.
 */
const CARD_ONLY = { ignore: COPY_BLOCK_IGNORE } as const

function load(body: Record<string, unknown>): ApprovalCardLoad {
  return { kind: 'card', card: approvalCard(body) }
}

/**
 * The component under its real props.
 *
 * The id comes from the page's own URL parameter rather than off the card: a 401 answer
 * carries no card, and a retry offered from that state has to know what to re-read.
 */
function renderCardView(
  initial: ApprovalCardLoad,
  signInUrl: string | null = SIGN_IN,
  // No frame origin by default, which switches the frame sizer off: these specs are about the
  // loop, and the sizer's own rules have their own lane (`src/components/frame-sizer.test.tsx`).
  // One spec below passes an origin, because "the card renders a sizer at all" is a claim about
  // this file's subject and nothing else would catch its removal.
  frameOrigin: string | null = null,
) {
  return render(
    <CardView
      initial={initial}
      actionRequestId={CARD_ID}
      signInUrl={signInUrl}
      frameOrigin={frameOrigin}
    />,
  )
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
 * re-arm.
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

  it('polls a running change until the run is terminal, then stops', async () => {
    // The whole of state-in-DB in one spec: the state lives in the database, so the frame re-reads the row
    // — and the outcome lands on the same URL that asked the question.
    const polls = stubPolls(() =>
      Response.json(approvedBody({ status: 'COMPLETED', result_summary: RESULT })),
    )
    renderCardView(load(approvedBody()))

    expect(screen.getByText('STARTED')).toBeTruthy()
    expect(polls.calls).toBe(0)

    await tick(POLL_INTERVAL_RUN_MS)

    expect(polls.calls).toBe(1)
    expect(screen.getByText('COMPLETED')).toBeTruthy()
    // The envelope the poll carried is on the row and off the card: the run block stopped printing
    // `result_summary`, which is a JSON dump of the payload the receipt renders as rows instead.
    // The fixture still sends it, so this is the value arriving and not being printed.
    expect(screen.queryByText(RESULT, CARD_ONLY)).toBeNull()

    // The half that matters: a terminal run ends the loop.
    await ticks(5, POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(1)
  })

  it('never polls a card that is already terminal', async () => {
    // The separating case. Without it, "it polled until COMPLETED" passes just as well against a
    // component that polls forever and happened to be handed a terminal answer.
    const polls = stubPolls(() => Response.json(cardBody({ status: 'DENIED', csrf: null })))
    renderCardView(load(cardBody({ status: 'DENIED', csrf: null })))

    await ticks(5, POLL_INTERVAL_PENDING_MS)

    expect(polls.calls).toBe(0)
    expect(screen.getByText(/no longer awaiting a decision/i)).toBeTruthy()
  })

  it('waits the pending interval while nothing is executing', async () => {
    // Two speeds, and this is the one that separates them: at the run interval a PENDING card has
    // not asked yet.
    const polls = stubPolls(() => Response.json(cardBody()))
    renderCardView(load(cardBody()))

    await tick(POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(0)

    await tick(POLL_INTERVAL_PENDING_MS - POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(1)
  })

  it('takes the Approve button away when the poll finds the request expired', async () => {
    // An approval that expires under an open frame must stop offering a decision the door would
    // refuse — a button that cannot succeed reads as an action refused rather than never available.
    stubPolls(() => Response.json(cardBody({ status: 'EXPIRED', csrf: null })))
    renderCardView(load(cardBody()))

    expect(screen.getByRole('button', { name: /approve/i })).toBeTruthy()

    await tick(POLL_INTERVAL_PENDING_MS)

    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.queryByLabelText(/why is this change/i)).toBeNull()
    expect(screen.getByText(/expired without an answer/i, CARD_ONLY)).toBeTruthy()
  })

  it('replaces the card with the 401 state and no live decision', async () => {
    stubPolls(() => Response.json({ error_code: 'session_invalid' }, { status: 401 }))
    renderCardView(load(cardBody()))

    await tick(POLL_INTERVAL_PENDING_MS)

    expect(screen.getByText(/cannot authenticate here/i)).toBeTruthy()
    // Named rather than counted: the 401 state puts a link-out and a retry here, and neither is a
    // decision. What is forbidden is an Approve an operator cannot use, and the reason box beside it.
    expect(screen.queryByRole('button', { name: /approve|deny/i })).toBeNull()
    expect(screen.queryByLabelText(/why is this change/i)).toBeNull()
    expect(screen.getByRole('link', { name: /sign in to noa/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy()
  })

  it('Try again re-reads the card and hands the loop back', async () => {
    // The way out of a 401 that does not navigate the frame: the session is picked up in another
    // tab, this button re-reads, and the card that comes back resumes polling on its own.
    const polls = stubPolls(
      () => Response.json({ error_code: 'session_invalid' }, { status: 401 }),
      () => Response.json(approvedBody()),
      () => Response.json(approvedBody({ status: 'COMPLETED', result_summary: RESULT })),
    )
    renderCardView(load(cardBody()))

    await tick(POLL_INTERVAL_PENDING_MS)
    expect(polls.calls).toBe(1)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    })

    expect(polls.calls).toBe(2)
    // The heading is the humanised label now, with the raw name in its `title`; what this asserts
    // is unchanged, that a card came back rather than a notice.
    expect(screen.getByText('Suspend Account')).toBeTruthy()
    expect(screen.getByText('STARTED')).toBeTruthy()

    // And the loop is live again — the retry is not a one-shot read that leaves a static card.
    await tick(POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(3)
    expect(screen.getByText('COMPLETED')).toBeTruthy()
  })

  it('keeps the 401 state when a retry cannot reach NOA', async () => {
    // The same rule the poll follows: "could not be asked" is not an answer about the session. The
    // separating case for the spec above — without it, a retry that replaced the notice with
    // whatever came back would pass just as well.
    const polls = stubPolls(
      () => Response.json({ error_code: 'session_invalid' }, { status: 401 }),
      () => new Response('', { status: 503 }),
    )
    renderCardView(load(cardBody()))

    await tick(POLL_INTERVAL_PENDING_MS)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    })

    expect(polls.calls).toBe(2)
    expect(screen.getByText(/cannot authenticate here/i)).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('could not be reached')
  })

  it('offers no sign-in link when none is configured, and still offers the retry', async () => {
    stubPolls(() => Response.json({ error_code: 'session_invalid' }, { status: 401 }))
    const { container } = renderCardView(load(cardBody()), null)

    await tick(POLL_INTERVAL_PENDING_MS)

    expect(container.querySelector('a')).toBeNull()
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy()
  })

  it('replaces the card with the one not-available sentence on a 404', async () => {
    // Absent, another operator's and a deleted requester's all answer alike — one body from the
    // API, one sentence here.
    stubPolls(() => Response.json({ error_code: 'action_request_not_found' }, { status: 404 }))
    renderCardView(load(cardBody()))

    await tick(POLL_INTERVAL_PENDING_MS)

    expect(screen.getByText(/does not exist, or it is not yours/i)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('keeps the card and keeps asking when NOA cannot be reached', async () => {
    // The stop condition is "terminal", not "any answer". A transient failure must not blank a card
    // an operator is reading, and must not end the lifecycle this URL owns.
    const polls = stubPolls(
      () => new Response('', { status: 503 }),
      () => Response.json(approvedBody({ status: 'COMPLETED', result_summary: RESULT })),
    )
    renderCardView(load(approvedBody()))

    await tick(POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(1)
    // Still a card and not a notice: the heading is the humanised label, raw name in `title`.
    expect(screen.getByText('Suspend Account')).toBeTruthy()
    expect(screen.getByText('STARTED')).toBeTruthy()

    await tick(POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(2)
    expect(screen.getByText('COMPLETED')).toBeTruthy()
  })

  it('renders both halves of a finished change, never one "done"', async () => {
    // DECISIONS section 6.5, at the render: the state the operator authorised against and what the change
    // did to it are two blocks, and the fixture's halves share no value — so a card that showed one
    // of them twice, or collapsed the pair into the verdict word, goes red here.
    stubPolls(() =>
      Response.json(
        approvedBody({ status: 'COMPLETED', result_summary: RESULT }, receiptBody()),
      ),
    )
    renderCardView(load(approvedBody()))

    // Before the receipt lands there is no outcome section at all — an empty one over a change
    // nobody has recorded would be a claim NOA cannot make.
    expect(screen.queryByText(/what the change did/i, CARD_ONLY)).toBeNull()

    await tick(POLL_INTERVAL_RUN_MS)

    expect(screen.getByText(/what the change did/i)).toBeTruthy()
    expect(screen.getByText('Completed')).toBeTruthy()
    // The after-state, as data rather than as a word.
    expect(screen.getByText('suspended_at')).toBeTruthy()
    expect(screen.getByText('2026-08-08T09:31:00+00:00')).toBeTruthy()
    // And the before-state still on the card beside it.
    expect(screen.getByText(/before state/i)).toBeTruthy()
    expect(screen.getByText('domain')).toBeTruthy()
    expect(screen.getByText('acme.example')).toBeTruthy()
  })

  it('keeps the before-state on a change that failed, and names the cause', async () => {
    // The one case where an operator most needs the half a "done"-only card would drop.
    stubPolls(() =>
      Response.json(
        approvedBody(
          { status: 'FAILED', result_summary: 'NOA cannot run this change.' },
          receiptBody({
            ok: false,
            after: { ok: false, error_code: 'ssh_sudo_required', message: 'refused' },
            error_code: 'ssh_sudo_required',
          }),
        ),
      ),
    )
    renderCardView(load(approvedBody()))

    await tick(POLL_INTERVAL_RUN_MS)

    expect(screen.getByText('Did not complete')).toBeTruthy()
    expect(screen.getAllByText('ssh_sudo_required').length).toBeGreaterThan(0)
    expect(screen.getByText(/before state/i)).toBeTruthy()
    expect(screen.getByText('acme.example')).toBeTruthy()
  })

  it('shows the before-state once, not once per source', async () => {
    // `evidence` and `receipt.before` are the same payload — the executor's writer copies it — so rendering
    // both would put one fact on the card twice under two headings.
    renderCardView(load(approvedBody({ status: 'COMPLETED' }, receiptBody())))

    expect(screen.getAllByText('domain', CARD_ONLY)).toHaveLength(1)
    expect(screen.getAllByText('acme.example', CARD_ONLY)).toHaveLength(1)
  })

  it('stops watching a run that never moves, and says so', async () => {
    // A run whose executor died moves only when the reaper next runs, which is not a timescale
    // anyone watches a frame for. Without the cap this loop would run for as long as the frame is
    // open. Giving up is not reported as a failure — NOA has no evidence of one, only of not having
    // been told.
    const polls = stubPolls(() => Response.json(approvedBody({ status: 'STARTED' })))
    renderCardView(load(approvedBody()))

    await ticks(RUN_POLL_LIMIT, POLL_INTERVAL_RUN_MS)

    expect(polls.calls).toBe(RUN_POLL_LIMIT)
    expect(screen.getByText(/still running this change/i)).toBeTruthy()

    await ticks(5, POLL_INTERVAL_RUN_MS)
    expect(polls.calls).toBe(RUN_POLL_LIMIT)
  })

  it('renders no form, polling or not', async () => {
    // The sandbox LibreChat gives this frame omits `allow-forms`, so a native submit
    // dies silently in there. A re-render driven by a poll must not reintroduce one.
    stubPolls(() => Response.json(cardBody()))
    const { container } = renderCardView(load(cardBody()))

    expect(container.querySelector('form')).toBeNull()
    await tick(POLL_INTERVAL_PENDING_MS)
    expect(container.querySelector('form')).toBeNull()
  })

  it('asks the host for a frame the card fits in', async () => {
    // The wiring, and only the wiring: that this surface mounts a sizer and that a height leaves
    // the document. What the number must be, when it may change, and when it must stop are the
    // sizer's own rules and are asserted in `src/components/frame-sizer.test.tsx`; what nothing
    // else would catch is this component quietly losing its sizer in an edit.
    const post = vi.spyOn(window.parent, 'postMessage')
    stubPolls(() => Response.json(cardBody()))
    renderCardView(load(cardBody()), SIGN_IN, 'https://chat.noa.internal')

    expect(post).toHaveBeenCalledTimes(1)
    const [message, target] = post.mock.calls[0]!
    expect((message as { type: string }).type).toBe('ui-size-change')
    expect(target).toBe('https://chat.noa.internal')
  })

  it.each([
    ['unauthenticated', /cannot authenticate here/i],
    ['not-found', /request not available/i],
    ['unavailable', /could not load this request/i],
  ] as const)('renders the %s state the server read, and polls nothing', async (kind, text) => {
    const polls = stubPolls(() => Response.json(cardBody()))
    const initial = (kind === 'unavailable' ? { kind, status: 503 } : { kind }) as ApprovalCardLoad
    renderCardView(initial)

    expect(screen.getByText(text)).toBeTruthy()

    // No *loop* from any of these: none of them says what to wait for. The 401 retry is a click,
    // not a timer — a state with no terminator polled on an interval would ask forever.
    await ticks(5, POLL_INTERVAL_PENDING_MS)
    expect(polls.calls).toBe(0)
  })
})
