import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'

import type { AdminUser } from '@/lib/admin/users/types'
import type { TokenScope } from '@/lib/admin/tokens/types'
import type { TokensController } from '@/lib/admin/tokens/use-tokens'
import type { VerifiedAuthState } from '@/lib/auth/use-verified-auth'

import AdminUserTokensRoute from './page'

const state = vi.hoisted(() => ({
  auth: null as unknown as VerifiedAuthState,
  controller: null as unknown as TokensController,
  params: { userId: 'user-7' } as Record<string, string>,
  fetchUsers: vi.fn(),
  // A spy, not `() => state.controller`: the argument is the whole point. Sending
  // a self action to an admin path is covered at the transport
  // (tokens-api.test.ts:64-81), but nothing else proves this page hands that
  // transport the scope it read from the URL.
  useTokens: vi.fn<(scope: TokenScope) => TokensController>(),
}))

vi.mock('next/navigation', () => ({ useParams: () => state.params }))
vi.mock('@/lib/auth/use-verified-auth', () => ({ useVerifiedAuth: () => state.auth }))
vi.mock('@/lib/admin/tokens/use-tokens', () => ({ useTokens: state.useTokens }))
vi.mock('@/lib/admin/users/users-api', () => ({ fetchUsers: state.fetchUsers }))

const target: AdminUser = { id: 'user-7', email: 'grace@example.com', roles: ['member'] }

const admin = {
  id: 'me-1',
  email: 'root@example.com',
  is_active: true,
  roles: ['admin'],
}

beforeEach(() => {
  vi.clearAllMocks()
  state.params = { userId: 'user-7' }
  state.auth = { status: 'ready', user: admin, isAdmin: true }
  state.controller = {
    tokens: [],
    loading: false,
    loadError: null,
    reload: vi.fn(),
    mint: vi.fn(),
    revoke: vi.fn(),
  }
  state.useTokens.mockImplementation(() => state.controller)
  state.fetchUsers.mockResolvedValue([target])
})

describe('/admin/users/[userId]/tokens', () => {
  it('shows the loading skeleton while the admin gate is still resolving', () => {
    state.auth = { status: 'loading', user: null, isAdmin: false }
    render(<AdminUserTokensRoute />)

    expect(screen.getByLabelText('Loading page')).toBeInTheDocument()
    // The label lookup must not fire before the gate answers.
    expect(state.fetchUsers).not.toHaveBeenCalled()
  })

  it('renders ForbiddenView for a verified non-admin', () => {
    state.auth = { status: 'forbidden', user: { ...admin, roles: [] }, isAdmin: false }
    render(<AdminUserTokensRoute />)

    expect(screen.getByRole('heading', { level: 1, name: /don’t have access/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /mint token/i })).not.toBeInTheDocument()
  })

  it('ends the breadcrumb on the page title and names the user', async () => {
    render(<AdminUserTokensRoute />)

    await waitFor(() => expect(state.fetchUsers).toHaveBeenCalled())
    const crumbs = within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getAllByRole('listitem')
    expect(crumbs.map((crumb) => crumb.textContent)).toEqual([
      'Administration',
      'Users',
      target.email,
      'MCP tokens',
    ])
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('MCP tokens')
  })

  it('falls back to the monospace id when the user is not in the list', async () => {
    state.fetchUsers.mockResolvedValue([])
    render(<AdminUserTokensRoute />)

    await waitFor(() => expect(state.fetchUsers).toHaveBeenCalled())
    // The list is a label, not an authority: the tokens request's own 404 is.
    const crumbs = within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getAllByRole('listitem')
    expect(crumbs.map((crumb) => crumb.textContent)).toContain('user-7')
    expect(screen.getAllByRole('button', { name: /mint token/i }).length).toBeGreaterThan(0)
  })

  it('keeps the page usable when the label lookup fails outright', async () => {
    state.fetchUsers.mockRejectedValue(new Error('boom'))
    render(<AdminUserTokensRoute />)

    await waitFor(() => expect(state.fetchUsers).toHaveBeenCalled())
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('MCP tokens')
    expect(screen.getAllByRole('button', { name: /mint token/i }).length).toBeGreaterThan(0)
  })

  it('mounts the panel in the user scope it read from the route', async () => {
    render(<AdminUserTokensRoute />)
    await waitFor(() => expect(state.fetchUsers).toHaveBeenCalled())

    // The scope the page HANDS the controller. The union is the only thing that
    // builds a path, so this picks between the two mint surfaces — admin FOR
    // an operator vs operator for themself — and a wrong id here mints against
    // the wrong account. The empty-state wording below separates self from user
    // and nothing more: a page passing the wrong id renders identically.
    expect(state.useTokens).toHaveBeenCalledWith({ kind: 'user', userId: 'user-7' })
    expect(screen.getByText(/authenticate to NOA as this user/i)).toBeInTheDocument()
  })

  it('takes that id from the route segment, not from a constant', async () => {
    state.params = { userId: 'user-9' }
    render(<AdminUserTokensRoute />)
    await waitFor(() => expect(state.fetchUsers).toHaveBeenCalled())

    // The control for the assertion above: change the segment and the scope
    // follows it, so 'user-7' passing there is the page reading `useParams()`
    // rather than the fixture and the expectation agreeing by coincidence.
    expect(state.useTokens).toHaveBeenCalledWith({ kind: 'user', userId: 'user-9' })
    expect(state.useTokens).not.toHaveBeenCalledWith({ kind: 'user', userId: 'user-7' })
    expect(state.useTokens).not.toHaveBeenCalledWith({ kind: 'self' })
  })
})
