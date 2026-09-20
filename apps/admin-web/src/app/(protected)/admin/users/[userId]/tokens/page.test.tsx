import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'

import type { AdminUser } from '@/lib/admin/users/types'
import type { TokenScope } from '@/lib/admin/tokens/types'
import type { TokensController } from '@/lib/admin/tokens/use-tokens'

import AdminUserTokensRoute from './page'

const state = vi.hoisted(() => ({
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
vi.mock('@/lib/admin/tokens/use-tokens', () => ({ useTokens: state.useTokens }))
vi.mock('@/lib/admin/users/users-api', () => ({ fetchUsers: state.fetchUsers }))

const target: AdminUser = { id: 'user-7', email: 'grace@example.com', roles: ['member'] }

beforeEach(() => {
  vi.clearAllMocks()
  state.params = { userId: 'user-7' }
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
