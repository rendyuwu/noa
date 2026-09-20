import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import type {
  AuditActionReceipt,
  AuditActionRequestDetail,
  AuditActionRequestListItem,
} from '@/lib/admin/audit/types'

// The page pulls its data through the list hook and two lazy detail hooks. Mock
// all of them so the test drives the page's own composition — filter wiring,
// pagination, row → drawer, and which of the two detail loads is dispatched —
// deterministically.
//
// The detail hook is generic and this page holds two instances of it, so the
// mock dispatches on the fetcher it was handed rather than on call order: a
// test that assumed "the first `useAuditDetail` is the request one" would break
// silently the day the two are declared in the other order.
const mocks = vi.hoisted(() => ({
  requests: null as unknown,
  detail: undefined as unknown,
  detailLoadingId: null as string | null,
  receipt: undefined as unknown,
  detailLoads: [] as string[],
  receiptLoads: [] as string[],
}))

vi.mock('@/lib/admin/audit/use-audit-list', () => ({
  useAuditList: () => mocks.requests,
}))
vi.mock('@/lib/admin/audit/audit-api', () => ({
  fetchActionRequestDetail: vi.fn(),
  fetchActionRequests: vi.fn(),
  fetchActionReceipt: vi.fn(),
}))
vi.mock('@/lib/admin/audit/use-audit-detail', async () => {
  const api = await import('@/lib/admin/audit/audit-api')
  return {
    useAuditDetail: (fetchDetail: unknown) => {
      const isReceipt = fetchDetail === api.fetchActionReceipt
      const stored = isReceipt ? mocks.receipt : mocks.detail
      const log = isReceipt ? mocks.receiptLoads : mocks.detailLoads
      return {
        detailsById: stored ? { [(stored as { actionRequestId: string }).actionRequestId]: stored } : {},
        loadingId: isReceipt ? null : mocks.detailLoadingId,
        errorId: null,
        error: null,
        load: (id: string) => log.push(id),
      }
    },
  }
})

import { ApprovalsAdminPage } from './approvals-admin-page'
import { ACTION_REQUEST_STATUS_OPTIONS } from './audit-filters'

const REQUEST_ID = '11111111-1111-4111-8111-111111111111'
const TOOL_RUN_ID = '22222222-2222-4222-8222-222222222222'

// The words an operator typed on the approval card. Several sentences, because
// that is what the column is for and what a truncating renderer would ruin.
const REASON =
  'Customer confirmed the abuse report on ticket OPS-4412. Suspending until the compromised ' +
  'mailbox password is rotated and the outbound queue is drained.'

const YOPASS_URL = 'https://yopass.internal/#/s/9f2c1b/one-time-key'

const requestRow: AuditActionRequestListItem = {
  actionRequestId: REQUEST_ID,
  toolName: 'whm_suspend_account',
  status: 'APPROVED',
  requestedByEmail: 'operator@example.com',
  conversationRef: 'conv-1',
  createdAt: '2026-09-09T10:00:00.000Z',
  expiresAt: '2026-09-09T10:15:00.000Z',
  decidedAt: '2026-09-09T10:00:42.000Z',
  toolRunId: TOOL_RUN_ID,
  hasReceipt: true,
}

const requestDetail: AuditActionRequestDetail = {
  ...requestRow,
  reason: REASON,
  approvalContext: {
    requester: { email: 'operator@example.com', librechat_user_id: 'lc-user-77' },
    arguments: { user: 'acmecorp', server_ref: 'web-01' },
    evidence: {
      server_id: '3f9d0a2e-0000-4000-8000-000000000001',
      api_username: 'noa-automation',
      account: { user: 'acmecorp', domain: 'acme.example', suspended: false },
    },
  },
}

const receipt: AuditActionReceipt = {
  actionRequestId: REQUEST_ID,
  toolRunId: TOOL_RUN_ID,
  createdAt: '2026-09-09T10:00:45.000Z',
  ok: true,
  before: { suspended: false },
  after: { suspended: true, yopass_url: YOPASS_URL },
  errorCode: null,
  delta: { verification: 'verified', changed_fields: [{ field: 'suspended', old: false, new: true }] },
}

const NO_REQUEST_FILTERS = { fromDate: '', toDate: '', toolName: '', status: '', requestedByEmail: '' }

// `activeFilters` is not decoration here: the page runs the real
// activeActionRequestFilterCount over it to choose the empty state's copy, so a
// case that wants "filters are active" says so by naming filters.
function listController(over: Record<string, unknown> = {}) {
  return {
    draft: { ...NO_REQUEST_FILTERS },
    setDraft: vi.fn(),
    activeFilters: { ...NO_REQUEST_FILTERS },
    items: [requestRow],
    loading: false,
    loadError: null,
    pageIndex: 1,
    canGoPrev: false,
    canGoNext: true,
    applyFilters: vi.fn(),
    clearFilters: vi.fn(),
    goPrev: vi.fn(),
    goNext: vi.fn(),
    reload: vi.fn(),
    ...over,
  }
}

const controller = () => mocks.requests as ReturnType<typeof listController>

function openDrawer() {
  render(<ApprovalsAdminPage />)
  fireEvent.click(screen.getByText('Suspend Account'))
  return screen.getByRole('dialog')
}

// The value cell of one Identifiers row, found by its own label. A
// whole-drawer text match is not a substitute here: `lc-user-77` is also a key
// of the approval context and so renders a second time inside the JSON block,
// which means a drawer-wide assertion stays green with the named row deleted.
// The query has to be aimed at the row it claims to be testing.
function identifierValue(drawer: HTMLElement, label: string): string {
  const term = Array.from(drawer.querySelectorAll('dt')).find(
    (node) => node.textContent?.trim() === label,
  )
  return term?.nextElementSibling?.textContent?.trim() ?? ''
}

beforeEach(() => {
  mocks.requests = listController()
  mocks.detail = requestDetail
  mocks.detailLoadingId = null
  mocks.receipt = receipt
  mocks.detailLoads = []
  mocks.receiptLoads = []
  window.history.pushState(null, '', '/admin/approvals')
})

describe('ApprovalsAdminPage structure', () => {
  it('renders the header and the request table with full monospace ids', () => {
    render(<ApprovalsAdminPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Approvals' })).toBeInTheDocument()
    expect(screen.getByText(REQUEST_ID)).toBeInTheDocument()
  })

  it('is its own view rather than a tab beside the audit list', () => {
    render(<ApprovalsAdminPage />)
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
    expect(window.location.pathname).toBe('/admin/approvals')
  })

  it('maps each decision status onto its own chip', () => {
    render(<ApprovalsAdminPage />)
    expect(screen.getByText('Approved')).toBeInTheDocument()
  })

  it('renders an expiry as a Badge rather than borrowing "Rejected"', () => {
    // The four statuses are not four chips: BIGSU has no standard status for an
    // expiry, and an expiry is a decision the clock made — nobody rejected
    // anything. The fallback Badge is the honest rendering, and asserting it
    // here is what stops a later edit from collapsing the two.
    mocks.requests = listController({ items: [{ ...requestRow, status: 'EXPIRED' }] })
    render(<ApprovalsAdminPage />)
    expect(screen.getByText('Expired')).toBeInTheDocument()
    expect(screen.queryByText('Rejected')).not.toBeInTheDocument()
  })

  it('offers no risk filter: every row here is a CHANGE', () => {
    render(<ApprovalsAdminPage />)
    expect(screen.getByLabelText('Status')).toBeInTheDocument()
    expect(screen.queryByLabelText('Risk')).not.toBeInTheDocument()
  })

  it('offers every decision status the enum defines, and no invented one', () => {
    // The dropdown mirrors `ActionRequestStatus` in `core/db/lifecycle.py`, and
    // nothing in this package can read that enum — so this pins the TypeScript
    // half and the comment above the constant says the other half is unbound.
    // Asserted as the whole ordered list, not a length: a count goes green for
    // four wrong names, and a status the API would reject is as bad as a status
    // it accepts and this list omits.
    expect(ACTION_REQUEST_STATUS_OPTIONS.map((option) => option.value)).toEqual([
      '',
      'PENDING',
      'APPROVED',
      'DENIED',
      'EXPIRED',
    ])
  })
})

describe('ApprovalsAdminPage read-only surface', () => {
  it('shows no decision affordance anywhere on the page', () => {
    // The one writer of a terminal status is the approval card's POST, and it is
    // not this app. A button here would be a second answer to "may this run?".
    const drawer = openDrawer()
    for (const label of [/approve/i, /deny/i, /delete/i, /edit/i, /retry/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
    expect(drawer.querySelector('form')).toBeNull()
    expect(drawer.querySelector('textarea')).toBeNull()
  })
})

describe('ApprovalsAdminPage detail drawer', () => {
  it('renders the operator reason whole', () => {
    // The field this whole vertical exists for. Asserted on the entire string,
    // not on a prefix: a renderer that truncated it would still contain the
    // opening clause, and the clause that matters is often the last one.
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent(REASON)
  })

  it('says why there is no reason rather than rendering an empty box', () => {
    // "Nobody has decided yet" is a fact. A blank panel under a Reason heading
    // reads as a rendering failure, which is the one thing it must not look like.
    mocks.requests = listController({
      items: [{ ...requestRow, status: 'PENDING', decidedAt: null, hasReceipt: false }],
    })
    mocks.detail = { ...requestDetail, status: 'PENDING', reason: null }
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent('Not decided yet, so no reason was given.')
  })

  it('names an expiry as nobody having answered', () => {
    mocks.requests = listController({
      items: [{ ...requestRow, status: 'EXPIRED', decidedAt: null, hasReceipt: false }],
    })
    mocks.detail = { ...requestDetail, status: 'EXPIRED', reason: null }
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent('This request expired unanswered, so nobody gave a reason.')
  })

  it('lazy-loads the detail and the receipt when a row is opened', () => {
    openDrawer()
    expect(mocks.detailLoads).toContain(REQUEST_ID)
    expect(mocks.receiptLoads).toContain(REQUEST_ID)
  })

  it('does not fetch a receipt for a request that has none', () => {
    // The control for the pair above, and it is load-bearing rather than tidy:
    // that route answers 404 for every deny and every expiry, so a page that
    // always fetched would show an error banner on the majority of rows.
    mocks.requests = listController({
      items: [{ ...requestRow, status: 'DENIED', toolRunId: null, hasReceipt: false }],
    })
    mocks.receipt = undefined
    const drawer = openDrawer()
    expect(mocks.detailLoads).toContain(REQUEST_ID)
    expect(mocks.receiptLoads).toEqual([])
    expect(drawer).toHaveTextContent('No run was started for this request, so there is no receipt.')
  })
})

describe('ApprovalsAdminPage relocation checklist', () => {
  // Every field the operator's approval card stops rendering has to be readable
  // here. One assertion per field name — never a count — for the reason a count
  // assertion goes red for the right thing spelled wrongly and green for the
  // wrong thing spelled right.
  it('renders the LibreChat account behind the call', () => {
    // Scoped to the Identifiers row rather than to the drawer: the same id is a
    // value inside the approval-context JSON two sections down, so a
    // drawer-wide match would pass with this row and the extraction behind it
    // both deleted.
    expect(identifierValue(openDrawer(), 'LibreChat account')).toBe('lc-user-77')
  })

  it('says the LibreChat account is still loading rather than reporting none', () => {
    // That id rides in with the detail fetch, not with the list row, so until
    // the fetch lands there is no answer. An em dash here would say *this call
    // had no LibreChat account* — a different fact, and the one an auditor
    // would carry away from a panel they closed while it was still filling in.
    mocks.detail = undefined
    mocks.detailLoadingId = REQUEST_ID
    const drawer = openDrawer()
    expect(identifierValue(drawer, 'LibreChat account')).toBe('Loading…')
    expect(identifierValue(drawer, 'LibreChat account')).not.toBe('—')
  })

  it('renders the conversation ref', () => {
    expect(openDrawer()).toHaveTextContent('conv-1')
  })

  it('renders the tool run the approval started', () => {
    expect(openDrawer()).toHaveTextContent(TOOL_RUN_ID)
  })

  it('renders the server and the credential identity from the gate evidence', () => {
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent('3f9d0a2e-0000-4000-8000-000000000001')
    expect(drawer).toHaveTextContent('noa-automation')
  })

  it('renders the nested account blob the card flattens away', () => {
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent('acme.example')
  })

  it('renders both raw receipt halves beside the delta', () => {
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent('Before')
    expect(drawer).toHaveTextContent('After')
    expect(drawer).toHaveTextContent('"suspended": true')
  })
})

describe('ApprovalsAdminPage receipt rendering', () => {
  it('renders a yopass URL as text and never as a link', () => {
    // The secret behind that URL is consumed once. A hover preview, a
    // prefetch or a mis-click spends the operator's own delivery and nobody can
    // read the password afterwards — so the criterion is the absence of an
    // anchor, and it is asserted as absence rather than inferred from the
    // renderer using a <pre>.
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent(YOPASS_URL)

    const anchors = Array.from(drawer.querySelectorAll('a'))
    expect(anchors.some((anchor) => anchor.getAttribute('href')?.includes('yopass'))).toBe(false)
    expect(anchors.some((anchor) => anchor.textContent?.includes(YOPASS_URL))).toBe(false)
  })

  it('renders a yopass URL in the delta as text too, not only one in the after-half', () => {
    // The fixture above carries its URL in `after`, and all three halves go
    // through one renderer today — so that one case covers them only while the
    // renderer stays one. A password reset is the change that actually delivers
    // a credential, and it states it in the delta and nowhere else: no
    // `changed_fields`, and `delivered_credential` is the URL itself rather
    // than a wrapper around it. Pinned as its own case so splitting the
    // renderer per half cannot leave this half unguarded.
    mocks.receipt = {
      ...receipt,
      before: { password_set: true },
      after: { password_set: true },
      delta: { verification: 'verified', delivered_credential: YOPASS_URL },
    }
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent(YOPASS_URL)

    const anchors = Array.from(drawer.querySelectorAll('a'))
    expect(anchors.some((anchor) => anchor.getAttribute('href')?.includes('yopass'))).toBe(false)
    expect(anchors.some((anchor) => anchor.textContent?.includes(YOPASS_URL))).toBe(false)
  })

  it('would notice an anchor if one were rendered', () => {
    // The negative control for the assertion above. Without it the anchor query
    // is green against a drawer that renders no anchors at all for any reason,
    // including one that failed to render the receipt — so the probe is pointed
    // at a document that does contain a matching anchor and it finds one.
    const probe = document.createElement('div')
    probe.innerHTML = `<a href="${YOPASS_URL}">${YOPASS_URL}</a>`
    const anchors = Array.from(probe.querySelectorAll('a'))
    expect(anchors.some((anchor) => anchor.getAttribute('href')?.includes('yopass'))).toBe(true)
  })

  it('names an absent delta rather than drawing an empty object', () => {
    // `delta: null` means the runner stated none, which is what separates an
    // executor refusal from a runner failure — both are `ok: false`. A renderer
    // that drew `{}` would merge the two, and the merge would be invisible.
    mocks.receipt = { ...receipt, ok: false, errorCode: 'ssh_sudo_required', delta: null }
    const drawer = openDrawer()
    expect(drawer).toHaveTextContent('The runner stated no change.')
    expect(drawer).toHaveTextContent('ssh_sudo_required')
    expect(drawer).toHaveTextContent('Failed')
  })
})

describe('ApprovalsAdminPage filters, pagination and errors', () => {
  it('applies filters through the controller', () => {
    render(<ApprovalsAdminPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
    expect(controller().applyFilters).toHaveBeenCalled()
  })

  it('drives the next cursor from the standalone pagination', () => {
    render(<ApprovalsAdminPage />)
    fireEvent.click(screen.getByRole('button', { name: /next page/i }))
    expect(controller().goNext).toHaveBeenCalled()
  })

  it('distinguishes an empty trail from an empty filtered result', () => {
    mocks.requests = listController({ items: [] })
    render(<ApprovalsAdminPage />)
    expect(screen.getByText('No approval requests')).toBeInTheDocument()

    mocks.requests = listController({
      items: [],
      activeFilters: { ...NO_REQUEST_FILTERS, toolName: 'whm_suspend_account', status: 'APPROVED' },
    })
    render(<ApprovalsAdminPage />)
    expect(screen.getByText('No matching approval requests')).toBeInTheDocument()
  })

  it('surfaces a load error with a retry', () => {
    mocks.requests = listController({ loadError: 'Unable to load approval requests' })
    render(<ApprovalsAdminPage />)
    expect(screen.getByText('Unable to load approval requests')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(controller().reload).toHaveBeenCalled()
  })
})
