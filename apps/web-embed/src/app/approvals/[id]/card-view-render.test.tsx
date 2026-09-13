import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApprovalCardLoad } from '@/lib/approvals/card'

import { COPY_BLOCK_IGNORE } from '@/components/copy-summary'

import { CardView } from './card-view'
import {
  CARD_ID,
  FIREWALL_EVIDENCE,
  RECEIPT_AFTER,
  TOOL_RUN_ID,
  approvalCard,
  approvedBody,
  cardBody,
  receiptBody,
} from '../../../../tests/support/approval-card'

/**
 * What the card says, as opposed to what keeps it true.
 *
 * A second file beside `card-view.test.tsx` because the two have different subjects and because one
 * file could not hold both against this package's 450-line ceiling for a `.tsx`: that one is the
 * poll — that it starts, uses the right door and interval, and stops — and this one is the
 * rendering.
 *
 * **The rule every case here serves: no text on this surface is authored at runtime.** The headline
 * and the sentence are the runner's own bytes, the imperative and the evidence heading are the
 * gate's, and the lines under that heading are the target system's. What this app contributes is
 * the corner, the `Asked: ` label, and the order. So the assertions compare *whole strings against
 * the fixture's own*, never substrings: a substring match cannot tell a byte that arrived from one
 * a renderer happened to compose.
 *
 * **Every removal is asserted by name, never by counting rows.** A count goes green for the right
 * label spelled wrongly. What is removed here is removed from a render and from nowhere else: the
 * arguments, the before-state, the run's status and duration, the per-backend rows, the evidence
 * bound as a labelled row, the error code and the dump of everything the runner reported are all in
 * the database, in the API's body, and rendered whole in the admin drawer
 * (`apps/admin-web/src/components/admin/audit/action-request-detail-drawer.tsx` prints the approval
 * context and all three receipt halves as JSON).
 *
 * The clock is pinned per spec rather than read. Relative times against a live clock are a suite
 * that fails at a month boundary on a slow machine.
 */

/**
 * What "the visible card says this" means, now that the copy control renders the record twice.
 *
 * The off-screen block is in the DOM deliberately — a selection cannot cover a `display: none`
 * element — and Testing Library does not filter on visibility, so an assertion that a value is
 * *not* printed matches the copy of it in there and reads as a pass. The selector is the copy
 * control's own export, because both halves of it are load-bearing and the reason is written where
 * the block is.
 *
 * `script, style` is Testing Library's own default, restated because passing `ignore` replaces it.
 */
const CARD_ONLY = { ignore: COPY_BLOCK_IGNORE } as const

/** 17 minutes after the fixture was opened, and 43 before it expires. */
const NOW = new Date('2026-08-08T09:17:00+00:00')

function load(body: Record<string, unknown>): ApprovalCardLoad {
  return { kind: 'card', card: approvalCard(body) }
}

function renderCardView(initial: ApprovalCardLoad) {
  return render(
    <CardView
      initial={initial}
      actionRequestId={CARD_ID}
      signInUrl="https://admin.noa.internal/login"
      // No frame origin, which switches the sizer off: these specs are about what the card says,
      // and the sizer's rules have their own lane (`src/components/frame-sizer.test.tsx`).
      frameOrigin={null}
    />,
  )
}

/** Every `<dt>` the visible card renders, so an absent row is asserted by its label. */
function labels(): string[] {
  const card = screen.getByRole('main')
  return Array.from(card.querySelectorAll('dt')).map((node) => node.textContent ?? '')
}

/**
 * One node carrying exactly this text on the visible card.
 *
 * Scoped and un-normalised, and both halves matter. The copy block repeats the body, so an
 * unscoped presence query resolves to two nodes and throws on the ambiguity rather than asserting
 * anything. And Testing Library's default normaliser collapses runs of spaces — which is exactly
 * what a firewall line is full of, so a verbatim claim asserted through it is not a verbatim claim.
 */
function onCard(text: string): HTMLElement {
  return screen.getByText(text, { ...CARD_ONLY, normalizer: (value) => value })
}

/** Every line inside the evidence block, exactly as the DOM carries them. */
function evidenceLines(): string[] {
  const block = screen.getByRole('main').querySelector('[data-noa-evidence]')
  return Array.from(block?.querySelectorAll('li') ?? []).map((row) => row.textContent ?? '')
}

describe('the card’s own words', () => {
  beforeEach(() => {
    // Fake timers so the clock can be pinned, and so the pending card's own poll timer is armed but
    // never fires — nothing here advances it, and nothing here stubs `fetch`.
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    vi.restoreAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('renders the gate’s headline and imperative byte for byte', () => {
    // The whole bytes, both of them, against the fixture's own strings. `Asked: ` is the card's own
    // label and the only string on this surface that is neither measured nor quoted; everything
    // after it is what the gate composed.
    renderCardView(load(cardBody()))

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Suspend an account — acmeco')
    expect(onCard('Asked: suspend the acmeco account on alpha')).toBeTruthy()
  })

  it('states what was asked as an imperative, never as a prediction', () => {
    // The grammar is the whole difference. "203.0.113.24 will be unblocked" is a claim with nothing
    // measured behind it; the gate composed the imperative on purpose, and this card renders it.
    renderCardView(load(cardBody({ tool_name: 'whm_firewall_release_and_allow', evidence: FIREWALL_EVIDENCE })))

    expect(
      onCard('Asked: remove 203.0.113.24 from the deny lists on alpha and allow it for 60 minutes'),
    ).toBeTruthy()
    expect(screen.queryByText(/will be/i, CARD_ONLY)).toBeNull()
  })

  it('prefers the runner’s headline and sentence once a run has written them', () => {
    // What happened replaces what was asked for the moment there is a run that can say so — and the
    // bytes are the runner's, unchanged.
    renderCardView(load(approvedBody({ status: 'COMPLETED' }, receiptBody())))

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(RECEIPT_AFTER['headline'])
    expect(onCard(String(RECEIPT_AFTER['message']))).toBeTruthy()
    // And the gate's two strings are off the card, because one fact belongs in one place on it.
    expect(screen.queryByText('Suspend an account — acmeco', CARD_ONLY)).toBeNull()
    expect(screen.queryByText(/^Asked:/, CARD_ONLY)).toBeNull()
  })

  it('falls back to the humanised tool name on a card that predates the headline keys', () => {
    // **Permanent, not transitional.** Every `action_requests` row opened before those keys shipped
    // carries neither, and those cards still have to render.
    renderCardView(load(cardBody({ evidence: { suspended: false } })))

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Suspend Account')
    // Nothing takes the imperative's place either: a gate that stated no request has none to show.
    expect(screen.queryByText(/^Asked:/, CARD_ONLY)).toBeNull()
  })

  it('puts a verification state this build has never heard of on the screen, quoted', () => {
    // The one failure mode the four-state split exists to prevent, asserted at the render rather
    // than only at the function: an unrecognised value folded into the benign word would be NOA
    // claiming a measurement it does not have, on the line an operator acts on.
    renderCardView(
      load(
        approvedBody(
          { status: 'COMPLETED' },
          receiptBody({ delta: { identity: { server: 'alpha' }, verification: 'refuted_by_neighbour' } }),
        ),
      ),
    )

    expect(onCard('Approved · NOA reported "refuted_by_neighbour"')).toBeTruthy()
  })

  it('keeps a confirmed change apart from one NOA could not read back', () => {
    // The two share a headline on every family, by design — WHM accepted the call either way — so
    // the corner is the only thing separating them and it has to actually do it.
    const { unmount } = renderCardView(load(approvedBody({ status: 'COMPLETED' }, receiptBody())))
    expect(onCard('Approved')).toBeTruthy()
    unmount()

    renderCardView(
      load(
        approvedBody(
          { status: 'COMPLETED' },
          receiptBody({ delta: { identity: { server: 'alpha' }, verification: 'unavailable' } }),
        ),
      ),
    )
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(RECEIPT_AFTER['headline'])
    expect(onCard('Approved · not confirmed')).toBeTruthy()
  })

  it('names the machine beside the corner', () => {
    // Two of the seven families name only the node in their `asked` sentence — "disable net0 on VM
    // 108 (pve1)" — so an operator could not tell which Proxmox it is on without this.
    renderCardView(load(cardBody()))

    expect(screen.getByText('alpha', CARD_ONLY)).toBeTruthy()
  })

  it('draws no machine where the gate named none', () => {
    // The separating case for the row above. Absent is absent, never a guess and never a dash.
    const { container } = renderCardView(load(cardBody({ evidence: { headline: 'Something' } })))

    // Scoped to the corner itself: the copy control lives in the same header and renders a
    // paragraph per section of the record it hands the clipboard.
    expect(container.querySelectorAll('[class*="corner"] p')).toHaveLength(1)
  })
})

describe('the evidence block', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('draws the gate’s heading over the server’s own lines', () => {
    renderCardView(load(cardBody({ tool_name: 'whm_firewall_release_and_allow', evidence: FIREWALL_EVIDENCE })))

    expect(screen.getByRole('heading', { level: 2, name: 'Why it was blocked' })).toBeTruthy()
    // Verbatim means the double space csf printed survives too, which is why this reads the node
    // rather than going through a matcher that would normalise it away.
    expect(evidenceLines()).toEqual(['DENY  203.0.113.24 # lfd: too many login failures'])
    expect(onCard('Read from the server before the change ran.')).toBeTruthy()
  })

  it('draws no block at all for a family the gate published no heading for', () => {
    // The account fixture: structured fields, no vendor text to head. An empty heading over nothing
    // reads as a renderer that broke.
    renderCardView(load(cardBody()))

    expect(screen.queryByText(/read from the server/i, CARD_ONLY)).toBeNull()
    expect(screen.getByRole('main').querySelector('[data-noa-evidence]')).toBeNull()
  })

  it('shows the same block before and after the change ran', () => {
    // A card whose evidence changed between deciding and reading back would be two readings of one
    // moment. Both states read `evidence`, never `receipt.before`.
    const pending = renderCardView(
      load(cardBody({ tool_name: 'whm_firewall_release_and_allow', evidence: FIREWALL_EVIDENCE })),
    )
    const before = screen.getByRole('main').querySelector('[data-noa-evidence]')?.textContent
    pending.unmount()

    renderCardView(
      load(
        approvedBody(
          { status: 'COMPLETED' },
          receiptBody({ before: { evidence_heading: 'Something else', matches: ['changed'] } }),
          { tool_name: 'whm_firewall_release_and_allow', evidence: FIREWALL_EVIDENCE },
        ),
      ),
    )

    expect(screen.getByRole('main').querySelector('[data-noa-evidence]')?.textContent).toBe(before)
  })
})

describe('the rows this card no longer carries', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('drops the arguments, the before-state and the execution block, each by name', () => {
    renderCardView(
      load(approvedBody({ status: 'COMPLETED', completed_at: '2026-08-08T09:31:34+00:00' }, receiptBody())),
    )

    for (const heading of ['Arguments', 'Before state', 'Execution']) {
      expect(screen.queryByRole('heading', { name: heading })).toBeNull()
    }
    // And their values with them: the argument keys, the preflight's own fields, the run's status.
    //
    // `operator words WHM would echo back` is the one this list cannot lose. It is WHM's echo of
    // the operator's own typed reason, which the gate evidence genuinely carries, and the rule it
    // holds is a hard one: no surface renders a reason back. The render path now refuses it by
    // construction — a closed allowlist of gate keys rather than the denylist of dotted paths the
    // deleted before-state renderer used, and a denylist fails open on the key nobody listed — so
    // this is the check that the allowlist stays closed as keys are added to it.
    for (const value of ['server_ref', 'acmeco', 'domain', 'COMPLETED', 'operator words WHM would echo back']) {
      expect(screen.queryByText(value, CARD_ONLY)).toBeNull()
    }
    // The separating case — the card did render, so the absences above are rows leaving rather than
    // a card that failed to draw.
    expect(screen.getByRole('heading', { level: 1 })).toBeTruthy()
  })

  it('drops the verification sentences, the backends, the bound row and the error code', () => {
    renderCardView(
      load(
        approvedBody(
          { status: 'FAILED' },
          receiptBody({
            ok: false,
            error_code: 'ssh_sudo_required',
            delta: {
              identity: { server: 'alpha' },
              verification: 'unavailable',
              backends: [{ name: 'csf', driven: true, answered: false, verdict: null, error_code: null }],
              unanswered: ['csf'],
              bound: { total: 20, truncated: true },
            },
          }),
        ),
      ),
    )

    // The four facts these rows carried are not gone: the silent source is named in the runner's own
    // sentence, the cap is on the evidence block's closing line, the `null`/`()` split is in the
    // before-clause the runner composes, and the four verification states are in the corner — which
    // is asserted here on the same render, so this is rows leaving rather than a card going blank.
    expect(onCard('Approved · not confirmed')).toBeTruthy()
    for (const gone of ['Backends', 'Reported by the runner', 'Reading covered', 'Error code', 'Because']) {
      expect(screen.queryByText(gone, CARD_ONLY)).toBeNull()
    }
    expect(labels()).not.toContain('Outcome')
    expect(screen.queryByText(/neither confirmed nor ruled out/i, CARD_ONLY)).toBeNull()
    expect(screen.queryByText(/no answer from/i, CARD_ONLY)).toBeNull()
  })
})

describe('the identifiers and the clock', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('carries the run id once there is a run, and substitutes nothing before', () => {
    // The audit list is keyed on the run id, so it is the one string that gets an operator who
    // cannot open `/admin` an answer from somebody who can. A row reading `none` would state an
    // identifier that does not exist, and the action-request id is not put in its place.
    const pending = renderCardView(load(cardBody()))
    expect(labels()).not.toContain('Run id')
    expect(screen.queryByText(CARD_ID, CARD_ONLY)).toBeNull()
    pending.unmount()

    renderCardView(load(approvedBody({ status: 'COMPLETED' }, receiptBody())))
    expect(screen.getByText(TOOL_RUN_ID, CARD_ONLY)).toBeTruthy()
  })

  it('leaves the request’s other identifiers off the card, each by name', () => {
    renderCardView(load(approvedBody({ status: 'COMPLETED' }, receiptBody())))

    expect(screen.queryByText('librechat-user-1', CARD_ONLY)).toBeNull()
    expect(screen.queryByText('1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12', CARD_ONLY)).toBeNull()
    // The identity that stays: who asked, which is the one an operator recognises.
    expect(screen.getByText('operator@noa.internal', CARD_ONLY)).toBeTruthy()
  })

  it('states the approval window as a span beside the buttons, and drops the Expires row', () => {
    // Two clocks used to sit on one screen with nothing saying they were different clocks. This one
    // is how long there is to answer; the firewall entry's own window is inside the gate's `asked`
    // sentence, in the minutes the schema takes.
    renderCardView(load(cardBody()))

    expect(onCard('You have 43 minutes to answer.')).toBeTruthy()
    expect(labels()).not.toContain('Expires')
    expect(screen.queryByText('in 43 minutes', CARD_ONLY)).toBeNull()
    // The relative time that stays, which is what a decision turns on.
    expect(onCard('17 minutes ago')).toBeTruthy()
    expect(screen.getByTitle('2026-08-08T09:00:00+00:00')).toBeTruthy()
  })

  it('says the window has closed rather than counting backwards', () => {
    // The separating case. A negative countdown rendered as "43 minutes" would read as an
    // instruction to hurry on a request nothing will accept.
    vi.setSystemTime(new Date('2026-08-08T10:30:00+00:00'))
    renderCardView(load(cardBody()))

    expect(onCard('You have no time left to answer.')).toBeTruthy()
  })
})
