import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DecisionControls } from './decision-controls'

/**
 * The reason box and the two buttons.
 *
 * **What jsdom can prove here is the structure, and it is the half the JS-fetch-only design is
 * about**: there is no
 * `<form>` in this tree and neither button submits one, so the sandbox LibreChat renders the frame
 * under — `allow-scripts allow-same-origin`, `allow-forms` absent — cannot silently
 * swallow a click. That the `fetch` really does reach NOA from inside such a frame is
 * `e2e/approvals.browser.e2e.ts`'s claim; a jsdom assertion about a sandbox would be a check that
 * cannot fail.
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

/** Far enough ahead of any clock this suite runs under that the window is open in every case. */
const EXPIRES = '2099-01-01T00:00:00+00:00'

/** The component under test, with the card's re-read stubbed out — see the last case for why. */
function mount(onRecorded: () => Promise<unknown> = () => Promise.resolve(), expiresAt = EXPIRES) {
  return render(
    <DecisionControls
      actionRequestId={ID}
      csrf={CSRF}
      expiresAt={expiresAt}
      onRecorded={onRecorded}
    />,
  )
}

function type(reason: string): void {
  fireEvent.change(screen.getByLabelText(/why is this change/i), { target: { value: reason } })
}

function click(name: RegExp): void {
  fireEvent.click(screen.getByRole('button', { name }))
}

async function settle(): Promise<void> {
  // One microtask turn past the `fetch`, which is all `submitDecision` awaits. `waitFor` would
  // work too and would hide a hang behind a timeout — the readiness-gate-not-the-subject family, one lane over.
  await vi.waitFor(() => expect(screen.getByRole('status')).toBeTruthy())
}

describe('DecisionControls', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders no form, and neither button submits one', () => {
    const { container } = mount()

    expect(container.querySelector('form')).toBeNull()
    for (const button of screen.getAllByRole('button')) {
      expect(button.getAttribute('type')).toBe('button')
    }
  })

  it('offers exactly one reason box and two decisions', () => {
    mount()

    // Exactly one, because the one-reason-field rule says exactly one reason field exists anywhere.
    expect(screen.getAllByLabelText(/why is this change/i)).toHaveLength(1)
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Approve',
      'Deny',
    ])
  })

  it('POSTs the typed reason and the token to the approve path', async () => {
    const seen = stubFetch(Response.json({ action_request_id: ID, tool_run_id: 'run-1' }))
    mount()

    type(REASON)
    click(/approve/i)
    await settle()

    expect(seen[0]?.url).toBe(`/api/action-requests/${ID}/approve`)
    expect(JSON.parse(String(seen[0]?.init?.body))).toEqual({ reason: REASON, csrf: CSRF })
    expect(screen.getByRole('status').textContent).toContain('Approved')
  })

  it('POSTs to the deny path when Deny is clicked', async () => {
    // The separating case: without it, "Approve posts to approve" passes just as well against a
    // component whose two buttons do the same thing.
    const seen = stubFetch(Response.json({ action_request_id: ID, status: 'DENIED' }))
    mount()

    type('Not a legitimate request.')
    click(/deny/i)
    await settle()

    expect(seen[0]?.url).toBe(`/api/action-requests/${ID}/deny`)
    expect(screen.getByRole('status').textContent).toContain('Nothing was changed')
  })

  it('shows a blank-reason refusal and leaves the buttons usable', async () => {
    // The gate is the endpoint, so a blank reason is submittable and answered — and the remedy is
    // to type one and click again, which means the buttons must not be spent.
    stubFetch(
      Response.json(
        {
          error_code: 'change_reason_required',
          message: 'A reason is required. Type why this change is being made or refused.',
        },
        { status: 409 },
      ),
    )
    mount()

    click(/approve/i)
    await settle()

    expect(screen.getByRole('status').textContent).toContain('A reason is required')
    expect(screen.getByRole('button', { name: /approve/i }).hasAttribute('disabled')).toBe(false)
    expect(screen.getByLabelText(/why is this change/i).hasAttribute('disabled')).toBe(false)
  })

  it('spends the buttons once a decision is recorded', async () => {
    // Exactly one `pending → decided` transition exists, so a second click could only ever earn a
    // 409. Disabling after a *recorded* answer and not after a refused one is the distinction.
    stubFetch(Response.json({ action_request_id: ID, tool_run_id: 'run-1' }))
    mount()

    type(REASON)
    click(/approve/i)
    await settle()

    expect(screen.getByRole('button', { name: /approve/i }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: /deny/i }).hasAttribute('disabled')).toBe(true)
  })

  it('does not POST twice for a second click on a recorded decision', async () => {
    const seen = stubFetch(Response.json({ action_request_id: ID, tool_run_id: 'run-1' }))
    mount()

    type(REASON)
    click(/approve/i)
    await settle()
    click(/approve/i)

    expect(seen).toHaveLength(1)
  })

  it('says nothing was recorded when the POST never arrived', async () => {
    // Distinct from a refusal on purpose: an operator who is told "refused" stops, and an operator
    // who is told "not recorded" retries. Only one of those is true here.
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      throw new TypeError('fetch failed')
    })
    mount()

    type(REASON)
    click(/approve/i)
    await settle()

    expect(screen.getByRole('status').textContent).toContain('nothing was recorded')
    expect(screen.getByRole('button', { name: /approve/i }).hasAttribute('disabled')).toBe(false)
  })

  it('re-reads the card once a decision is recorded, and not for a refusal', async () => {
    // Without this the card learns nothing until the next poll, which is 15s away while a request
    // sits PENDING — the fast run cadence is only chosen once a poll has already seen a run in
    // flight (`lib/approvals/poll.ts`). A refusal changed nothing, so there is nothing to re-read.
    //
    // **What this case cannot reach, said rather than implied.** A successful re-read makes the
    // request undecidable, and `card-view.tsx` then unmounts these controls — which is what fixes
    // the ordering of the re-read against `setOutcome` in place. This renders the component
    // standalone against a stub, so nothing unmounts here and no jsdom case in this app covers
    // that; `e2e/approvals.browser.e2e.ts` is the lane that could. For the same reason nothing
    // below asserts on `pending` clearing or on the button label: on the real path this component
    // is gone by then, so an assertion about either could not fail.
    const onRecorded = vi.fn(() => Promise.resolve())

    stubFetch(
      Response.json(
        { error_code: 'change_reason_required', message: 'A reason is required.' },
        { status: 409 },
      ),
    )
    mount(onRecorded)
    click(/approve/i)
    await settle()

    expect(onRecorded).not.toHaveBeenCalled()

    // Torn down before the second half so `settle` waits on a fresh status node rather than
    // finding the refusal's and resolving before the recorded answer has landed.
    cleanup()

    const seen = stubFetch(Response.json({ action_request_id: ID, tool_run_id: 'run-1' }))
    mount(onRecorded)
    type(REASON)
    click(/approve/i)
    await settle()

    expect(onRecorded).toHaveBeenCalledTimes(1)
    // The re-read is the card's GET, never a second decision: one POST for one click.
    expect(seen).toHaveLength(1)
  })

  it('states how long there is to answer, beside the buttons and nowhere else', () => {
    // **Which clock this is, is the whole point of the placement.** `Expires in 43 minutes` used to
    // sit in the provenance rows while a `duration_minutes` of 60 sat in the arguments, on one
    // screen, with nothing saying they were different clocks. This one is the approval window, and
    // it is stated where the decision is made.
    // The clock is pinned rather than read: a span against a live clock is a suite that fails at a
    // month boundary on a slow machine.
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-08T09:17:00+00:00'))
    const { container } = mount(undefined, '2026-08-08T10:00:00+00:00')

    expect(screen.getByText('You have 43 minutes to answer.')).toBeTruthy()
    // Inside the block the buttons are in, not floating above the card.
    expect(container.querySelector('[class*="actions"]')?.parentElement?.textContent).toContain(
      'You have 43 minutes to answer.',
    )
    vi.useRealTimers()
  })

  it('prints no window sentence for an expiry it cannot read', () => {
    // The separating case. The card parser's fallback for a missing string field is `''`, so this
    // is reachable from a body the API is free to send — and a window nobody can parse is not a
    // window of any particular length. Saying nothing beats inventing one.
    mount(undefined, '')

    expect(screen.queryByText(/to answer\./)).toBeNull()
    // And the buttons are still there, so the absence above is a sentence missing rather than the
    // whole block failing to render.
    expect(screen.getByRole('button', { name: /approve/i })).toBeTruthy()
  })
})
