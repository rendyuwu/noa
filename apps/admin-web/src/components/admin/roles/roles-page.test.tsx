import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import type { RolesController } from '@/lib/admin/roles/use-roles'

import { RolesPage } from './roles-page'

const state = vi.hoisted(() => ({ controller: null as unknown as RolesController }))

vi.mock('@/lib/admin/roles/use-roles', () => ({
  useRoles: () => state.controller,
}))

// The migration action calls the API directly; stub the network boundary so the
// page test stays focused on composition.
vi.mock('@/lib/admin/roles/roles-api', () => ({
  migrateDirectGrants: vi.fn().mockResolvedValue({}),
}))

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

  it('confirms before migrating legacy direct grants', () => {
    render(<RolesPage />)
    fireEvent.click(screen.getByRole('button', { name: /migrate direct grants/i }))
    expect(screen.getByRole('dialog', { name: 'Migrate legacy direct grants?' })).toBeInTheDocument()
  })

  it('renders the detail drawer when a role is selected', () => {
    state.controller = makeController({ selectedRole: 'admin', roleTools: ['tool.read'] })
    render(<RolesPage />)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('admin')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save allowlist' })).toBeInTheDocument()
  })
})
