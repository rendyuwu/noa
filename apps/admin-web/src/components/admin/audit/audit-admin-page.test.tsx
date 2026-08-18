import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import type { AuditToolRunListItem } from '@/lib/admin/audit/types'

// The page pulls its data through the list hook, the detail hook and the
// transport. Mock all three so the test drives the page's own composition —
// filter wiring, pagination, row → drawer — deterministically.
//
// Ported with two tabs and a URL/pushState dance between them; the
// action-requests tab named routes NOA does not serve (T55), so what is asserted
// here now is that there is *one* view, no tab strip to get out of step with the
// address bar, and no control bound to a column `tool_runs` does not have.
const mocks = vi.hoisted(() => ({
  toolRuns: null as unknown,
  detailLoads: [] as string[],
}))

vi.mock('@/lib/admin/audit/use-audit-tool-runs', () => ({
  useAuditToolRuns: (enabled: boolean) => ({ ...(mocks.toolRuns as object), enabled }),
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

function toolController(over: Record<string, unknown> = {}) {
  return {
    draft: {
      fromDate: '',
      toDate: '',
      toolName: '',
      status: '',
      risk: '',
      conversationRef: '',
      requestedByEmail: '',
    },
    setDraft: vi.fn(),
    activeFilters: {},
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
    filterCount: 0,
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
    mocks.toolRuns = toolController({ items: [], filterCount: 2 })
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
    mocks.toolRuns = toolController({ items: [], filterCount: 0 })
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
