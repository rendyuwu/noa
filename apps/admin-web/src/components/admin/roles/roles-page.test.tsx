import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import type { RolesController } from '@/lib/admin/roles/use-roles'

import { RolesPage } from './roles-page'

const state = vi.hoisted(() => ({ controller: null as unknown as RolesController }))

vi.mock('@/lib/admin/roles/use-roles', () => ({
  useRoles: () => state.controller,
}))

// No `roles-api` mock. Nothing this page renders calls the transport directly any
// more — the controller owns every request, and it is mocked above (T65 removed the
// migration action, the one component that reached past it).

function makeController(over: Partial<RolesController> = {}): RolesController {
  return {
    roles: ['admin', 'member'],
    availableTools: ['tool.read', 'tool.write'],
    roleToolCounts: { admin: 2, member: 0 },
    loading: false,
    loadError: null,
    reload: vi.fn(),
    selectedRole: null,
    selectRole: vi.fn(),
    roleTools: [],
    roleToolsLoading: false,
    roleToolsError: null,
    createRole: vi.fn(),
    deleteRole: vi.fn(),
    saveRoleTools: vi.fn(),
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  state.controller = makeController()
})

describe('RolesPage', () => {
  it('follows the standard header/filter/table structure', () => {
    render(<RolesPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Roles' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search by role name')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add role' })).toBeInTheDocument()
  })

  it('renders each role as a full monospace identifier with its tool count', () => {
    render(<RolesPage />)
    expect(screen.getByText('admin')).toBeInTheDocument()
    expect(screen.getByText('2 tools assigned')).toBeInTheDocument()
    expect(screen.getByText('0 tools assigned')).toBeInTheDocument()
  })

  it('shows a dash for a role whose count has not resolved yet', () => {
    state.controller = makeController({ roleToolCounts: { admin: 2 } })
    render(<RolesPage />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('shows an error state with a working retry', () => {
    state.controller = makeController({ loadError: 'Unable to load roles' })
    render(<RolesPage />)
    expect(screen.getByText('Unable to load roles')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })

  it('shows the empty state when there are no roles', () => {
    state.controller = makeController({ roles: [], roleToolCounts: {} })
    render(<RolesPage />)
    expect(screen.getByText('No roles yet')).toBeInTheDocument()
  })

  it('filters the list by the search term', () => {
    render(<RolesPage />)
    fireEvent.change(screen.getByPlaceholderText('Search by role name'), {
      target: { value: 'member' },
    })
    expect(screen.queryByText('admin')).not.toBeInTheDocument()
    expect(screen.getByText('member')).toBeInTheDocument()
  })

  it('offers a clear-filters path out of a filtered dead end', () => {
    render(<RolesPage />)
    fireEvent.change(screen.getByPlaceholderText('Search by role name'), {
      target: { value: 'nothing-matches' },
    })
    expect(screen.getByText('No matching roles')).toBeInTheDocument()
    const clears = screen.getAllByRole('button', { name: /clear filters/i })
    fireEvent.click(clears[clears.length - 1]!)
    expect(screen.getByText('admin')).toBeInTheDocument()
  })

  it('opens the detail drawer for a clicked row', () => {
    render(<RolesPage />)
    fireEvent.click(screen.getByText('admin'))
    expect(state.controller.selectRole).toHaveBeenCalledWith('admin')
  })

  it('opens the create dialog from the Add role action', () => {
    render(<RolesPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Add role' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Add role')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Role name')).toBeInTheDocument()
  })

  // T65 / V75: the ported panel's direct-grant migration control is gone, because
  // NOA has no per-user grant table and no migration endpoint. Asserted as an
  // absence rather than simply deleting the old test: without this, re-adding the
  // button would go unnoticed until an operator clicked it and got a 404.
  it('offers no legacy direct-grant migration', () => {
    render(<RolesPage />)
    expect(screen.queryByRole('button', { name: /migrate direct grants/i })).toBeNull()
    expect(screen.queryByText(/direct.grant/i)).toBeNull()
  })

  it('renders the detail drawer when a role is selected', () => {
    state.controller = makeController({ selectedRole: 'admin', roleTools: ['tool.read'] })
    render(<RolesPage />)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('admin')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save allowlist' })).toBeInTheDocument()
  })
})
