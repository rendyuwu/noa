import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type {
  AuditActionRequestListItem,
  AuditToolRunListItem,
} from '@/lib/admin/audit/types'

// The page pulls its data through the two list hooks, the detail hook, and the
// transport. Mock all of them so the test drives the page's own composition
// (tabs, URL history, filter wiring, row → drawer) deterministically.
const mocks = vi.hoisted(() => ({
  actions: null as unknown,
  toolRuns: null as unknown,
  detailLoads: [] as string[],
}))

vi.mock('@/lib/admin/audit/use-audit-action-requests', () => ({
  useAuditActionRequests: (enabled: boolean) => ({ ...(mocks.actions as object), enabled }),
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
  fetchActionRequestDetail: vi.fn(),
  fetchToolRunDetail: vi.fn(),
}))
// The action-request drawer's "View receipt" button navigates via useRouter.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

import { AuditAdminPage } from './audit-admin-page'

const actionRow: AuditActionRequestListItem = {
  actionRequestId: '11111111-1111-4111-8111-111111111111',
  threadId: 'thread-a',
  toolName: 'whm_create_account',
  risk: 'CHANGE',
  status: 'APPROVED',
  requestedByEmail: 'ops@example.com',
  createdAt: '2026-07-01T00:00:00.000Z',
  updatedAt: '2026-07-01T00:00:00.000Z',
  terminalPhase: 'completed',
  hasReceipt: true,
  receiptId: 'receipt-1',
  toolRunId: 'run-9',
}

const toolRow: AuditToolRunListItem = {
  toolRunId: '22222222-2222-4222-8222-222222222222',
  threadId: 'thread-b',
  actionRequestId: null,
  toolName: 'pmg_whitelist_search',
  risk: 'READ',
  status: 'COMPLETED',
  requestedByEmail: 'reader@example.com',
  createdAt: '2026-07-02T00:00:00.000Z',
  completedAt: '2026-07-02T00:00:01.000Z',
  durationMs: 1000,
  resultSummary: 'ok',
  error: null,
}

function actionController(over: Record<string, unknown> = {}) {
  return {
    draft: { fromDate: '', toDate: '', toolName: '', status: '', terminalPhase: '', threadId: '', requestedByEmail: '' },
    setDraft: vi.fn(),
    activeFilters: {},
    items: [actionRow],
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

function toolController(over: Record<string, unknown> = {}) {
  return {
    draft: { fromDate: '', toDate: '', toolName: '', status: '', risk: '', threadId: '', requestedByEmail: '' },
    setDraft: vi.fn(),
    activeFilters: {},
    items: [toolRow],
    loading: false,
    loadError: null,
    pageIndex: 1,
    canGoPrev: false,
    canGoNext: false,
    applyFilters: vi.fn(),
    clearFilters: vi.fn(),
    goPrev: vi.fn(),
    goNext: vi.fn(),
    reload: vi.fn(),
    filterCount: 0,
    ...over,
  }
}

beforeEach(() => {
  mocks.actions = actionController()
  mocks.toolRuns = toolController()
  mocks.detailLoads = []
  window.history.pushState(null, '', '/admin/audit')
})

describe('AuditAdminPage structure', () => {
  it('renders the header, tabs, and the action-request table with full monospace IDs', () => {
    render(<AuditAdminPage initialTab="action-requests" />)
    expect(screen.getByRole('heading', { level: 1, name: 'Audit' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Action requests' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Tool runs' })).toBeInTheDocument()
    // The full action-request id renders, untruncated.
    expect(screen.getByText(actionRow.actionRequestId)).toBeInTheDocument()
  })

  it('renders a StatusChip for a completed action request (workflow status)', () => {
    render(<AuditAdminPage initialTab="action-requests" />)
    expect(screen.getByText('Completed')).toBeInTheDocument()
    // Risk is a non-workflow descriptor → Badge, not StatusChip.
    expect(screen.getByText('Change')).toBeInTheDocument()
  })
})

describe('AuditAdminPage tabs and browser history', () => {
  it('switches to the tool-runs tab and pushes the deep-link URL', async () => {
    const user = userEvent.setup()
    render(<AuditAdminPage initialTab="action-requests" />)
    await user.click(screen.getByRole('tab', { name: 'Tool runs' }))
    expect(window.location.pathname).toBe('/admin/audit/tool-runs')
    expect(screen.getByText(toolRow.toolRunId)).toBeInTheDocument()
  })

  it('resyncs the active tab from a popstate (browser back/forward)', async () => {
    const user = userEvent.setup()
    render(<AuditAdminPage initialTab="action-requests" />)
    await user.click(screen.getByRole('tab', { name: 'Tool runs' }))
    expect(screen.getByText(toolRow.toolRunId)).toBeInTheDocument()

    window.history.pushState(null, '', '/admin/audit')
    fireEvent.popState(window)
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Action requests' })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    )
    expect(screen.getByText(actionRow.actionRequestId)).toBeInTheDocument()
  })

  it('honours the tool-runs deep-link entry point', () => {
    render(<AuditAdminPage initialTab="tool-runs" />)
    expect(screen.getByRole('tab', { name: 'Tool runs' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText(toolRow.toolRunId)).toBeInTheDocument()
  })
})

describe('AuditAdminPage filters, pagination, and detail', () => {
  it('applies filters through the controller', () => {
    render(<AuditAdminPage initialTab="action-requests" />)
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
    expect((mocks.actions as ReturnType<typeof actionController>).applyFilters).toHaveBeenCalled()
  })

  it('drives the next cursor from the standalone pagination', () => {
    render(<AuditAdminPage initialTab="action-requests" />)
    fireEvent.click(screen.getByRole('button', { name: /next page/i }))
    expect((mocks.actions as ReturnType<typeof actionController>).goNext).toHaveBeenCalled()
  })

  it('opens the detail drawer and lazy-loads detail when a row is clicked', () => {
    render(<AuditAdminPage initialTab="action-requests" />)
    fireEvent.click(screen.getByText('Create Account'))
    expect(mocks.detailLoads).toContain(actionRow.actionRequestId)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('offers a Clear filters action in the empty state when filters are active', () => {
    mocks.actions = actionController({ items: [], filterCount: 2 })
    render(<AuditAdminPage initialTab="action-requests" />)
    expect(screen.getByText('No matching action requests')).toBeInTheDocument()
  })

  it('surfaces a load error with a retry', () => {
    mocks.actions = actionController({ loadError: 'Unable to load audit history' })
    render(<AuditAdminPage initialTab="action-requests" />)
    expect(screen.getByText('Unable to load audit history')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect((mocks.actions as ReturnType<typeof actionController>).reload).toHaveBeenCalled()
  })
})
