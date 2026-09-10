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
    const { container } = render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

    expect(container.querySelector('form')).toBeNull()
    for (const button of screen.getAllByRole('button')) {
      expect(button.getAttribute('type')).toBe('button')
    }
  })

  it('offers exactly one reason box and two decisions', () => {
    render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

    // Exactly one, because the one-reason-field rule says exactly one reason field exists anywhere.
    expect(screen.getAllByLabelText(/why is this change/i)).toHaveLength(1)
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Approve',
      'Deny',
    ])
  })

  it('POSTs the typed reason and the token to the approve path', async () => {
    const seen = stubFetch(Response.json({ action_request_id: ID, tool_run_id: 'run-1' }))
    render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

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
    render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

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
    render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

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
    render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

    type(REASON)
    click(/approve/i)
    await settle()

    expect(screen.getByRole('button', { name: /approve/i }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: /deny/i }).hasAttribute('disabled')).toBe(true)
  })

  it('does not POST twice for a second click on a recorded decision', async () => {
    const seen = stubFetch(Response.json({ action_request_id: ID, tool_run_id: 'run-1' }))
    render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

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
    render(<DecisionControls actionRequestId={ID} csrf={CSRF} />)

    type(REASON)
    click(/approve/i)
    await settle()

    expect(screen.getByRole('status').textContent).toContain('nothing was recorded')
    expect(screen.getByRole('button', { name: /approve/i }).hasAttribute('disabled')).toBe(false)
  })
})
