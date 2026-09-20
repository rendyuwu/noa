import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import type { AuditToolRunListItem } from '@/lib/admin/audit/types'

// The page pulls its data through the list hook, the detail hook and the
// transport. Mock all three so the test drives the page's own composition —
// filter wiring, pagination, row → drawer — deterministically.
//
// Ported with two tabs and a URL/pushState dance between them; the
// action-requests tab named routes NOA does not serve, so what is asserted
// here now is that there is *one* view, no tab strip to get out of step with the
// address bar, and no control bound to a column `tool_runs` does not have.
const mocks = vi.hoisted(() => ({
  toolRuns: null as unknown,
  detailLoads: [] as string[],
}))

vi.mock('@/lib/admin/audit/use-audit-list', () => ({
  useAuditList: () => mocks.toolRuns,
}))
vi.mock('@/lib/admin/audit/use-audit-detail', () => ({
  useAuditDetail: () => ({
    detailsById: {},
    loadingId: null,
    errorId: null,
    error: null,
    load: (id: string) => mocks.detailLoads.push(id),
  }),
}))
vi.mock('@/lib/admin/audit/audit-api', () => ({
  fetchToolRunDetail: vi.fn(),
  fetchToolRuns: vi.fn(),
}))

import { AuditAdminPage } from './audit-admin-page'

const toolRow: AuditToolRunListItem = {
  toolRunId: '22222222-2222-4222-8222-222222222222',
  toolName: 'pmg_whitelist_search',
  risk: 'READ',
  status: 'COMPLETED',
  conversationRef: 'conv-1',
  requestedByEmail: 'reader@example.com',
  resultSummary: 'ok',
  createdAt: '2026-07-02T00:00:00.000Z',
  completedAt: '2026-07-02T00:00:01.000Z',
  durationMs: 1000,
}

const NO_TOOL_FILTERS = {
  fromDate: '',
  toDate: '',
  toolName: '',
  status: '',
  risk: '',
  conversationRef: '',
  requestedByEmail: '',
}

// `activeFilters` is not decoration here: the page runs the real
// activeToolFilterCount over it to choose the empty state's copy, so a case
// that wants "filters are active" says so by naming filters.
function toolController(over: Record<string, unknown> = {}) {
  return {
    draft: { ...NO_TOOL_FILTERS },
    setDraft: vi.fn(),
    activeFilters: { ...NO_TOOL_FILTERS },
    items: [toolRow],
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

const controller = () => mocks.toolRuns as ReturnType<typeof toolController>

beforeEach(() => {
  mocks.toolRuns = toolController()
  mocks.detailLoads = []
  window.history.pushState(null, '', '/admin/audit')
})

describe('AuditAdminPage structure', () => {
  it('renders the header and the tool-run table with full monospace IDs', () => {
    render(<AuditAdminPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Audit' })).toBeInTheDocument()
    // The full run id renders, untruncated, so an operator can copy and grep it.
    expect(screen.getByText(toolRow.toolRunId)).toBeInTheDocument()
  })

  it('is one view: no tab strip to keep in step with the URL', () => {
    render(<AuditAdminPage />)
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
    expect(window.location.pathname).toBe('/admin/audit')
  })

  it('renders a StatusChip for the run status and a Badge for risk', () => {
    render(<AuditAdminPage />)
    // COMPLETED → "Completed" through StatusChip's standard vocabulary.
    expect(screen.getByText('Completed')).toBeInTheDocument()
    // Risk is a non-workflow descriptor → Badge, not StatusChip.
    expect(screen.getByText('Read')).toBeInTheDocument()
  })

  it('offers a conversation-ref filter and no thread filter', () => {
    render(<AuditAdminPage />)
    expect(screen.getByLabelText('Conversation ref')).toBeInTheDocument()
    expect(screen.queryByLabelText(/thread/i)).not.toBeInTheDocument()
  })
})

describe('AuditAdminPage filters, pagination, and detail', () => {
  it('applies filters through the controller', () => {
    render(<AuditAdminPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
    expect(controller().applyFilters).toHaveBeenCalled()
  })

  it('drives the next cursor from the standalone pagination', () => {
    render(<AuditAdminPage />)
    fireEvent.click(screen.getByRole('button', { name: /next page/i }))
    expect(controller().goNext).toHaveBeenCalled()
  })

  it('opens the detail drawer and lazy-loads detail when a row is clicked', () => {
    render(<AuditAdminPage />)
    fireEvent.click(screen.getByText('Whitelist Search'))
    expect(mocks.detailLoads).toContain(toolRow.toolRunId)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('shows no decision affordance in the drawer: the trail is append-only', () => {
    render(<AuditAdminPage />)
    fireEvent.click(screen.getByText('Whitelist Search'))
    const drawer = screen.getByRole('dialog')
    for (const label of [/approve/i, /deny/i, /delete/i, /edit/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
    expect(drawer.querySelector('form')).toBeNull()
  })

  it('offers a Clear filters action in the empty state when filters are active', () => {
    mocks.toolRuns = toolController({
      items: [],
      activeFilters: { ...NO_TOOL_FILTERS, toolName: 'pmg_whitelist_search', risk: 'READ' },
    })
    render(<AuditAdminPage />)
    expect(screen.getByText('No matching tool runs')).toBeInTheDocument()
    // Two of them: the FilterBar's own, and the one inside the empty state — an
    // operator who filtered themselves into nothing should not have to find the
    // bar again. The last is the empty state's, and it drives the controller.
    const clears = screen.getAllByRole('button', { name: 'Clear filters' })
    expect(clears).toHaveLength(2)
    fireEvent.click(clears[1] as HTMLElement)
    expect(controller().clearFilters).toHaveBeenCalled()
  })

  it('distinguishes an empty trail from an empty filtered result', () => {
    mocks.toolRuns = toolController({ items: [] })
    render(<AuditAdminPage />)
    expect(screen.getByText('No tool runs')).toBeInTheDocument()
  })

  it('surfaces a load error with a retry', () => {
    mocks.toolRuns = toolController({ loadError: 'Unable to load tool runs' })
    render(<AuditAdminPage />)
    expect(screen.getByText('Unable to load tool runs')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(controller().reload).toHaveBeenCalled()
  })
})

// The four fields the API had been returning and the table had not been drawing
// (already part of the admin API's contract). No new endpoint and no new field: `AuditToolRunListItemResponse`
// has carried all four since the route shipped, so this is the cheapest half of
// making the trail legible — and the half that would be easiest to leave undone
// while calling the vertical finished.
//
// Asserted one column at a time, by the value each renders, so a column dropped
// in a later edit reddens the line that names it. A header-count assertion would
// go green for five headers and the wrong five.
describe('AuditAdminPage list columns', () => {
  it('renders the requester the API already returned', () => {
    render(<AuditAdminPage />)
    expect(screen.getByRole('columnheader', { name: 'Requester' })).toBeInTheDocument()
    expect(screen.getByText('reader@example.com')).toBeInTheDocument()
  })

  it('renders the conversation ref as a column, not only as a filter', () => {
    render(<AuditAdminPage />)
    expect(screen.getByRole('columnheader', { name: 'Conversation' })).toBeInTheDocument()
    expect(screen.getByText('conv-1')).toBeInTheDocument()
  })

  it('renders the completed timestamp beside the created one', () => {
    render(<AuditAdminPage />)
    expect(screen.getByRole('columnheader', { name: 'Finished' })).toBeInTheDocument()
    // Both stamps are drawn, so a run that started and never finished is visibly
    // different from one that did — the pair is what `durationMs` is derived from.
    expect(screen.getByRole('columnheader', { name: 'Created' })).toBeInTheDocument()
    // And the header does not collide with the Status column's own vocabulary:
    // "Completed" appears once on this page, as a status value.
    expect(screen.getAllByText('Completed')).toHaveLength(1)
  })

  it('renders the result summary, with the stored string reachable in full', () => {
    render(<AuditAdminPage />)
    expect(screen.getByRole('columnheader', { name: 'Result' })).toBeInTheDocument()
    const cell = screen.getAllByText('ok').find((node) => node.tagName === 'SPAN')
    expect(cell).toBeDefined()
    // The cell truncates visually; the whole stored summary stays on the title,
    // because it was already capped at the write and a second shortening here
    // must not be mistaken for that cap.
    expect(cell).toHaveAttribute('title', 'ok')
  })

  it('renders an em dash rather than nothing for a run that has not finished', () => {
    // The control for the two nullable columns above: without it, a cell that
    // rendered empty for a null would pass every assertion that looks for a value.
    mocks.toolRuns = toolController({
      items: [{ ...toolRow, completedAt: null, resultSummary: null, conversationRef: null }],
    })
    render(<AuditAdminPage />)
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3)
  })
})
