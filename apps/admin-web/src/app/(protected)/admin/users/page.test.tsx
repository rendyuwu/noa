import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import type { VerifiedAuthState } from '@/lib/auth/use-verified-auth'

const state = vi.hoisted(() => ({
  value: null as unknown as VerifiedAuthState,
  lastOptions: undefined as unknown,
}))

vi.mock('@/lib/auth/use-verified-auth', () => ({
  useVerifiedAuth: (options: unknown) => {
    state.lastOptions = options
    return state.value
  },
}))

vi.mock('@/components/admin/users/users-page', () => ({
  UsersPage: ({ me }: { me: { email: string } }) => <div data-testid="users-page">{me.email}</div>,
}))

import AdminUsersRoute from './page'

const admin = {
  id: 'me-1',
  email: 'root@example.com',
  display_name: 'Root',
  is_active: true,
  roles: ['admin'],
}

beforeEach(() => {
  state.value = { status: 'ready', user: admin, isAdmin: true }
})

describe('/admin/users route gate', () => {
  it('re-verifies with requireAdmin so the server decides access', () => {
    render(<AdminUsersRoute />)
    expect(state.lastOptions).toEqual({ requireAdmin: true })
  })

  it('renders the Users page for a verified admin', () => {
    render(<AdminUsersRoute />)
    expect(screen.getByTestId('users-page')).toHaveTextContent('root@example.com')
  })

  it('shows a loading placeholder while verifying', () => {
    state.value = { status: 'loading', user: null, isAdmin: false }
    render(<AdminUsersRoute />)
    expect(screen.getByLabelText('Loading page')).toBeInTheDocument()
    expect(screen.queryByTestId('users-page')).not.toBeInTheDocument()
  })

  it('renders nothing on an unexpected auth error', () => {
    state.value = { status: 'error', user: null, isAdmin: false }
    const { container } = render(<AdminUsersRoute />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the 403 state to a verified non-admin, never a blank page', () => {
    // Role-denied resolves to a state instead of a redirect. Since §T76 there
    // IS somewhere else to send them — `/me/tokens`, via `/home` — but a
    // redirect would answer a refusal by silently moving the operator, leaving
    // them to guess why the page they asked for is not the page they got. The
    // 403 state names the refusal and offers that route as a choice instead
    // (its "Back to home" button). Rendering nothing would leave them inside
    // the shell with no explanation for the empty page.
    state.value = { status: 'forbidden', user: { ...admin, roles: [] }, isAdmin: false }
    render(<AdminUsersRoute />)
    expect(screen.getByRole('heading', { name: /have access/i })).toBeInTheDocument()
    expect(screen.queryByTestId('users-page')).not.toBeInTheDocument()
  })
})
