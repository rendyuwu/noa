import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApprovalCardLoad } from '@/lib/approvals/card'

import { COPY_BLOCK_IGNORE } from '@/components/copy-summary'

import { CardView } from './card-view'
import {
  CARD_ID,
  TOOL_RUN_ID,
  approvalCard,
  approvedBody,
  cardBody,
} from '../../../../tests/support/approval-card'

/**
 * What the card renders, as opposed to what keeps it true.
 *
 * A second file beside `card-view.test.tsx` because the two have different subjects and because
 * one file could not hold both: that one is the poll — that it starts, uses the right door and
 * interval, and stops — and this one is the rendering. Together they came to 512 lines against
 * this package's 450-line ceiling for a `.tsx`, which is a real limit here rather than a
 * formality, since the largest file in the repository sits at 449.
 *
 * **Every removal is asserted by name, never by counting rows.** A count goes green for the right
 * field spelled wrongly, and green-against-a-break is worse than no check at all. Each one was
 * watched failing: the three identifiers and the three before-state keys were put back into
 * `card-view.tsx`, both specs went red naming the `<dt>` they found, and the file was restored.
 *
 * **What is removed here is removed from a render and from nowhere else**, and the six values fall
 * into two groups that reach an operator by different routes. The four identifiers — the LibreChat
 * account, the conversation reference, the run id and the request id — ride in the copied summary
 * (`lib/approvals/summary.ts`), because those are the strings an operator quotes into a ticket and
 * the operator who requests a change may have no way into the admin panel. The three before-state
 * keys are not in that summary and are not meant to be: `server_id` is a machine's row id,
 * `api_username` the credential a preflight was read with, and the raw account record is the WHM
 * row whole — an administrator's question, answered on `/admin` (held by
 * `apps/api/tests/test_admin_action_request_routes.py`). All six stay in the database and in the
 * API's body regardless; only which surface prints them changes.
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

/** 17 minutes after the fixture was opened, and 43 before it expires. */
const NOW = new Date('2026-08-08T09:17:00+00:00')

/**
 * A WHM account preflight with the keys the gate persists, from
 * `apps/api/src/noa_api/mcp_tools/whm_account_change.py`. The shared fixture's evidence is two
 * flat keys and carries none of the three this card stops printing, so it cannot show the removal.
 */
const WHM_EVIDENCE = {
  server_id: '3d1b0c4e-0000-4000-8000-00000000000f',
  server: 'alpha',
  api_username: 'root',
  host: 'alpha.example',
  owner: 'reseller1',
  account: { user: 'acmeco', suspended: false, domain: 'acme.example' },
}

/** What that gate persists beside it — `username`, not `account`: that key names the WHM row. */
const WHM_ARGUMENTS = { server_ref: 'alpha', username: 'acmeco' }

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

describe('CardView rendering', () => {
  beforeEach(() => {
    // Fake timers so the clock can be pinned, and so the pending card's own poll timer is armed
    // but never fires — nothing here advances it, and nothing here stubs `fetch`.
    vi.useFakeTimers()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('renders the name and the times the way an operator reads them', async () => {
    // The reshaped values, and the rule they all follow: nothing this card renders as a rendering
    // of something else is only available reshaped. The raw tool name is what an operator quotes
    // to an administrator and the ISO stamp is what they paste into a ticket, so both stay on the
    // row they were reshaped on.
    vi.setSystemTime(NOW)
    renderCardView(load(cardBody()))

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Suspend Account')
    expect(screen.getByTitle('whm_suspend_account')).toBeTruthy()
    expect(screen.getByText('17 minutes ago')).toBeTruthy()
    expect(screen.getByText('in 43 minutes')).toBeTruthy()
    expect(screen.getByTitle('2026-08-08T09:00:00+00:00')).toBeTruthy()
    expect(screen.queryByText('2026-08-08T09:00:00+00:00', CARD_ONLY)).toBeNull()
  })

  it('collapses the run’s two stamps into one duration', async () => {
    vi.setSystemTime(NOW)
    renderCardView(
      load(approvedBody({ status: 'COMPLETED', completed_at: '2026-08-08T09:31:34+00:00' })),
    )

    expect(screen.getByText('1m 34s')).toBeTruthy()
    // Named, not counted: both of the labels the duration replaced.
    expect(screen.queryByText('Started', CARD_ONLY)).toBeNull()
    expect(screen.queryByText('Finished', CARD_ONLY)).toBeNull()
  })

  it('says when a run in flight started, having no duration to state yet', async () => {
    // The separating case. A run with no end has not taken any amount of time, and a `0s` there
    // would be a measurement nobody made. The run's start is moved off the decision's timestamp,
    // which the fixture shares with it: two rows reading one phrase cannot show which is which.
    vi.setSystemTime(new Date('2026-08-08T09:33:00+00:00'))
    renderCardView(load(approvedBody({ created_at: '2026-08-08T09:31:00+00:00' })))

    expect(screen.getByText('2 minutes ago')).toBeTruthy()
    expect(screen.getByText('Started')).toBeTruthy()
    expect(screen.queryByText('Duration', CARD_ONLY)).toBeNull()
  })

  it('leaves the request’s own identifiers off the card, each asserted by name', async () => {
    renderCardView(
      load(approvedBody({ status: 'COMPLETED', completed_at: '2026-08-08T09:31:34+00:00' })),
    )

    expect(screen.queryByText('LibreChat account', CARD_ONLY)).toBeNull()
    expect(screen.queryByText('librechat-user-1', CARD_ONLY)).toBeNull()
    expect(screen.queryByText('Conversation', CARD_ONLY)).toBeNull()
    expect(screen.queryByText('1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12', CARD_ONLY)).toBeNull()
    expect(screen.queryByText('Run', CARD_ONLY)).toBeNull()
    expect(screen.queryByText(TOOL_RUN_ID, CARD_ONLY)).toBeNull()

    // The identity that stays: who asked, which is the one an operator recognises.
    expect(screen.getByText('operator@noa.internal', CARD_ONLY)).toBeTruthy()
  })

  it('leaves the preflight’s plumbing out of the before-state, each asserted by name', async () => {
    renderCardView(load(cardBody({ evidence: WHM_EVIDENCE, arguments: WHM_ARGUMENTS })))

    expect(screen.queryByText('server_id', CARD_ONLY)).toBeNull()
    expect(screen.queryByText(WHM_EVIDENCE.server_id, CARD_ONLY)).toBeNull()
    expect(screen.queryByText('api_username', CARD_ONLY)).toBeNull()
    expect(screen.queryByText('account', CARD_ONLY)).toBeNull()
    // The record's contents went with it, rather than its label alone disappearing.
    expect(screen.queryByText(/"domain"/, CARD_ONLY)).toBeNull()

    // And the rest of the preflight still renders: the filter takes three keys, not the block.
    expect(screen.getByText('host', CARD_ONLY)).toBeTruthy()
    expect(screen.getByText('alpha.example', CARD_ONLY)).toBeTruthy()
    expect(screen.getByText('owner', CARD_ONLY)).toBeTruthy()
  })

  it('renders a nested argument as rows rather than a line of JSON', async () => {
    renderCardView(
      load(cardBody({ arguments: { server_ref: 'alpha', window: { minutes: 30, unit: 'm' } } })),
    )

    expect(screen.getByText('window.minutes')).toBeTruthy()
    expect(screen.getByText('30')).toBeTruthy()
    expect(screen.queryByText('{"minutes":30,"unit":"m"}', CARD_ONLY)).toBeNull()
  })
})
