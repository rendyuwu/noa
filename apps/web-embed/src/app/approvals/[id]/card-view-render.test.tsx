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
 * watched failing by deleting its path from the production hide-set in `card-view.tsx` — never by
 * touching an assertion in here, because deleting an assertion reddens nothing and so measures
 * nothing — and the spec went red naming the row it then found.
 *
 * **What is removed here is removed from a render and from nowhere else**, and it falls into two
 * groups that reach an operator by different routes. The four identifiers — the LibreChat account,
 * the conversation reference, the run id and the request id — ride in the copied summary
 * (`lib/approvals/summary.ts`), because those are the strings an operator quotes into a ticket and
 * the operator who requests a change may have no way into the admin panel. The before-state paths
 * are not in that summary and are not meant to be: `server_id` is a machine's row id,
 * `api_username` the credential a preflight was read with, and the six `account.*` paths are the
 * identity fields of the WHM row — an administrator's question, answered on `/admin` (held by
 * `apps/api/tests/test_admin_action_request_routes.py`). Every one of them stays in the database
 * and in the API's body regardless; only which surface prints them changes.
 *
 * **The before-state block is flattened, so these specs read dotted paths**, and the account record
 * is no longer dropped whole. The fields a suspension actually moves live inside it, and a PENDING
 * card has no execution or outcome block to show them in instead.
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

const SERVER_ID = '3d1b0c4e-0000-4000-8000-00000000000f'

/**
 * A WHM account preflight with the keys the gate persists, from
 * `apps/api/src/noa_api/mcp_tools/whm_account_change.py`, its record filled the way
 * `core/integrations/whm/accounts.py` normalises a `listaccts` row. The shared fixture's evidence
 * is two flat keys and carries no nested record at all, so it cannot show either half of this.
 *
 * `owner` is deliberately the same string at both levels: the gate copies `account["owner"]` to
 * the top level, so the two rows hold one value by construction and no value assertion anywhere
 * could tell a path filter from a leaf-name one.
 */
const WHM_EVIDENCE = {
  server_id: SERVER_ID,
  server: 'alpha',
  api_username: 'root',
  host: 'alpha.example',
  owner: 'reseller1',
  account: {
    user: 'acmeco',
    domain: 'acme.example',
    email: 'billing@acme.example',
    contactemail: 'ops@acme.example',
    owner: 'reseller1',
    suspendreason: 'Smoke Test Rendy',
    suspended: false,
    suspendtime: 1789109810,
    is_locked: false,
  },
}

/** What that gate persists beside it — `username`, not `account`: that key names the WHM row. */
const WHM_ARGUMENTS = { server_ref: 'alpha', username: 'acmeco' }

/**
 * One preflight per CHANGE tool, and the field of it the tool's own change moves.
 *
 * The shapes are copied from each gate's `evidence={...}` and from the record builders it calls:
 * `whm_firewall_change_common.py::firewall_state`, `proxmox_nic.py::VMNICState.as_evidence`,
 * `proxmox_password.py::VMCloudInitState.as_evidence` and
 * `pmg_whitelist.py::build_whitelist_evidence`.
 *
 * **The list of tools is hand-kept and nothing binds it to the registry.** This package is
 * TypeScript and the tool map is Python, so an eighth CHANGE tool will not redden anything in
 * here. Said rather than implied — the Python side holds that count
 * (`apps/api/tests/test_change_receipt_halves.py` drives every registered tool).
 */
const FIREWALL_STATE = {
  available_backends: { csf: true, imunify: false },
  sudo_required: false,
  combined_verdict: 'blocked',
  unanswered_backends: ['imunify'],
  matches: [{ backend: 'csf', line: 'deny 203.0.113.9' }],
  total_matches: 1,
  truncated: false,
  csf: { ok: true, verdict: 'blocked' },
}

const CHANGE_TOOLS = [
  { tool: 'whm_suspend_account', evidence: WHM_EVIDENCE, moves: ['account.suspended', 'account.is_locked'] },
  { tool: 'whm_unsuspend_account', evidence: WHM_EVIDENCE, moves: ['account.is_locked', 'account.suspended'] },
  {
    tool: 'whm_firewall_release_and_allow',
    evidence: {
      server_id: SERVER_ID,
      server: 'alpha',
      target: '203.0.113.9',
      duration_minutes: 30,
      firewall: FIREWALL_STATE,
    },
    moves: ['firewall.combined_verdict', 'firewall.total_matches'],
  },
  {
    tool: 'whm_firewall_allowlist_remove',
    evidence: { server_id: SERVER_ID, server: 'alpha', target: '203.0.113.9', firewall: FIREWALL_STATE },
    moves: ['firewall.combined_verdict'],
  },
  {
    tool: 'proxmox_vm_nic',
    evidence: {
      server_id: SERVER_ID,
      server: 'alpha',
      node: 'pve1',
      vmid: 108,
      net: 'net0',
      action: 'disable',
      nic: {
        net: 'net0',
        model: 'virtio',
        mac_address: 'AA:BB:CC:DD:EE:01',
        bridge: 'vmbr0',
        link_state: 'up',
        auto_selected: true,
      },
      vm: {
        name: 'web-1',
        run_status: 'running',
        nics: [{ net: 'net0', link_state: 'up' }],
        unavailable_reads: [],
      },
    },
    moves: ['nic.link_state', 'vm.run_status'],
  },
  {
    tool: 'proxmox_reset_vm_password',
    evidence: {
      server_id: SERVER_ID,
      server: 'alpha',
      node: 'pve1',
      vmid: 108,
      username: 'ubuntu',
      vm: {
        name: 'web-1',
        ciuser: 'ubuntu',
        has_cloudinit_password: true,
        run_status: 'running',
        unavailable_reads: [],
      },
    },
    moves: ['vm.has_cloudinit_password', 'vm.ciuser'],
  },
  {
    tool: 'pmg_whitelist',
    evidence: {
      server_id: SERVER_ID,
      server: 'mail1',
      action: 'add',
      target: '198.51.100.0/24',
      normalized_target: '198.51.100.0/24',
      matches: [],
      total_entries: 312,
      mynetworks_endpoint: '/config/mynetworks',
    },
    moves: ['total_entries'],
  },
] as const

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

/**
 * The Before state block as the rows it rendered: dotted path to printed value.
 *
 * Scoped to that one block rather than to the card, because the filter's subject is that block and
 * a path missing from it may legitimately be printed elsewhere — `server_ref` is an argument as
 * well as a preflight key. It reads the rendered `<dt>`/`<dd>` pairs rather than the payload, so a
 * path that survives the filter and then fails to render still counts as absent.
 *
 * It throws rather than answering `{}` when the block is not there. An absence assertion over an
 * empty map is an assertion that cannot fail, and every spec below leans on absence.
 */
function beforeState(): Record<string, string> {
  const section = screen.getByRole('heading', { level: 2, name: 'Before state' }).parentElement
  if (section === null) throw new Error('the before-state heading has no block around it')

  const values = Array.from(section.querySelectorAll('dd'))
  return Object.fromEntries(
    Array.from(section.querySelectorAll('dt')).map((key, index) => [
      key.textContent ?? '',
      values[index]?.textContent ?? '',
    ]),
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

  it('prints what a suspension moves and none of the account’s identity', async () => {
    renderCardView(load(cardBody({ evidence: WHM_EVIDENCE, arguments: WHM_ARGUMENTS })))

    const before = beforeState()

    // Why this block is on a PENDING card at all: the fields the operation itself moves, at the
    // moment the operator decides and before any execution or outcome block exists. `is_locked` is
    // the one that answers "will this work" rather than "what is this" — a suspension lock refuses
    // `unsuspendacct` outright.
    expect(before['account.suspended']).toBe('false')
    expect(before['account.suspendtime']).toBe('1789109810')
    expect(before['account.is_locked']).toBe('false')

    // The identity beside them, each asserted by path. NOA needs the first two and the operator
    // does not; the rest name the account rather than saying anything about the change.
    expect(before['server_id']).toBeUndefined()
    expect(before['api_username']).toBeUndefined()
    expect(before['account.user']).toBeUndefined()
    expect(before['account.domain']).toBeUndefined()
    expect(before['account.email']).toBeUndefined()
    expect(before['account.contactemail']).toBeUndefined()

    // Its own assertion, because it is the one hidden path that does move with the change. WHM
    // stores the operator's typed NOA reason in that field and echoes it back on every later read,
    // so it carries nothing a decision rests on, and it is reason-bearing on the API side
    // (`core/approvals/delta.py`).
    expect(before['account.suspendreason']).toBeUndefined()

    // The rest of the preflight still renders: the filter takes paths, not the block.
    expect(before['host']).toBe('alpha.example')
  })

  it('hides the owner on the record while the top-level owner stays', async () => {
    // The reason the filter matches full paths, and the reason the flatten happens before it rather
    // than at the call site. `owner` is a top-level evidence key — the reseller the machine answers
    // to — and `account.owner` is the same identity repeated on the record. A leaf-name filter
    // deletes both, and because the gate copies one into the other the two rows hold an identical
    // string, so nothing but the label can separate them.
    renderCardView(load(cardBody({ evidence: WHM_EVIDENCE, arguments: WHM_ARGUMENTS })))

    const before = beforeState()

    expect(before['owner']).toBe('reseller1')
    expect(before['account.owner']).toBeUndefined()
  })

  it.each(CHANGE_TOOLS)(
    'gives $tool a field its own change can move, and no machine row id',
    ({ tool, evidence, moves }) => {
      renderCardView(load(cardBody({ tool_name: tool, evidence })))

      const before = beforeState()

      // Each one a labelled row, not a line of JSON inside a record: five of the seven carry a
      // nested object, and unflattened they arrived as one unreadable line.
      for (const path of moves) expect(before[path]).toBeDefined()

      // The one plumbing key all seven preflights share, so the filter is exercised against every
      // shape rather than only against the one it was derived from. **This is not a leakage check
      // for the rest of those shapes**: the other tools' nested records were enumerated once by
      // hand and carry no id and no credential, and nothing here would notice if that changed.
      expect(before['server_id']).toBeUndefined()
    },
  )

  it('renders a nested argument as rows rather than a line of JSON', async () => {
    renderCardView(
      load(cardBody({ arguments: { server_ref: 'alpha', window: { minutes: 30, unit: 'm' } } })),
    )

    expect(screen.getByText('window.minutes')).toBeTruthy()
    expect(screen.getByText('30')).toBeTruthy()
    expect(screen.queryByText('{"minutes":30,"unit":"m"}', CARD_ONLY)).toBeNull()
  })
})
