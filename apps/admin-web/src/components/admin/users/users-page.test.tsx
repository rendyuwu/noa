import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import type { AdminUser } from '@/lib/admin/users/types'
import type { UsersController } from '@/lib/admin/users/use-users'
import type { VerifiedUser } from '@/lib/auth/use-verified-auth'

import { UsersPage } from './users-page'

const state = vi.hoisted(() => ({ controller: null as unknown as UsersController }))

vi.mock('@/lib/admin/users/use-users', () => ({
  useUsers: () => state.controller,
}))

const userA: AdminUser = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'ada@example.com',
  display_name: 'Ada Lovelace',
  roles: ['admin'],
  is_active: true,
  last_login_at: null,
}
const userB: AdminUser = {
  id: '22222222-2222-4222-8222-222222222222',
  email: 'grace@example.com',
  roles: [],
  is_active: false,
  last_login_at: null,
}

const me: VerifiedUser = {
  id: '99999999-9999-4999-8999-999999999999',
  email: 'root@example.com',
  display_name: 'Root',
  is_active: true,
  roles: ['admin'],
}

function makeController(over: Partial<UsersController> = {}): UsersController {
  return {
    users: [userA, userB],
    availableRoles: ['admin', 'member'],
    loading: false,
    loadError: null,
    reload: vi.fn(),
    selectedUser: null,
    selectUser: vi.fn(),
    saveRoles: vi.fn(),
    setActive: vi.fn(),
    removeUser: vi.fn(),
    ...over,
  }
}

beforeEach(() => {
  state.controller = makeController()
})

describe('UsersPage', () => {
  it('follows the standard header/filter/table structure', () => {
    render(<UsersPage me={me} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Users' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search by email, name, or ID')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
  })

  it('renders each user with a full monospace identifier', () => {
    render(<UsersPage me={me} />)
    expect(screen.getByText('ada@example.com')).toBeInTheDocument()
    // Full user id shown, never truncated (createIdColumn / monospace convention).
    expect(screen.getByText(userA.id)).toBeInTheDocument()
    expect(screen.getByText(userB.id)).toBeInTheDocument()
  })

  it('shows an error state with a working retry', () => {
    state.controller = makeController({ loadError: 'Unable to load users' })
    render(<UsersPage me={me} />)
    expect(screen.getByText('Unable to load users')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })

  it('shows the empty state when there are no users', () => {
    state.controller = makeController({ users: [] })
    render(<UsersPage me={me} />)
    expect(screen.getByText('No users yet')).toBeInTheDocument()
  })

  it('filters the list by the search term', () => {
    render(<UsersPage me={me} />)
    fireEvent.change(screen.getByPlaceholderText('Search by email, name, or ID'), {
      target: { value: 'grace' },
    })
    expect(screen.queryByText('ada@example.com')).not.toBeInTheDocument()
    expect(screen.getByText('grace@example.com')).toBeInTheDocument()
  })

  it('sorts rows through a DataTable column header', () => {
    render(<UsersPage me={me} />)
    const userHeader = screen.getByRole('button', { name: /user id/i })

    fireEvent.click(userHeader)
    const ascending = screen.getAllByText(/11111111|22222222/)
    expect(ascending[0]).toHaveTextContent(userA.id)

    fireEvent.click(userHeader)
    const descending = screen.getAllByText(/11111111|22222222/)
    expect(descending[0]).toHaveTextContent(userB.id)
  })

  it('offers a clear-filters path out of a filtered dead end', () => {
    render(<UsersPage me={me} />)
    const search = screen.getByPlaceholderText('Search by email, name, or ID')
    fireEvent.change(search, { target: { value: 'nobody-matches-this' } })
    expect(screen.getByText('No matching users')).toBeInTheDocument()

    // Both the FilterBar and the empty state offer a clear-filters escape.
    const clears = screen.getAllByRole('button', { name: /clear filters/i })
    fireEvent.click(clears[clears.length - 1]!)
    expect(screen.getByText('ada@example.com')).toBeInTheDocument()
  })

  it('opens the detail panel for a clicked row', () => {
    render(<UsersPage me={me} />)
    fireEvent.click(screen.getByText('ada@example.com'))
    expect(state.controller.selectUser).toHaveBeenCalledWith(userA.id)
  })

  it('refreshes on demand', () => {
    render(<UsersPage me={me} />)
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })

  it('renders the detail drawer when a user is selected', () => {
    state.controller = makeController({ selectedUser: userA })
    render(<UsersPage me={me} />)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Ada Lovelace')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete user' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save roles' })).toBeInTheDocument()
  })
})
