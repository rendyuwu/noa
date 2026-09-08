import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'

import { AuthUserProvider } from '@/lib/auth/auth-context'
import type { TokensController } from '@/lib/admin/tokens/use-tokens'
import type { VerifiedUser } from '@/lib/auth/use-verified-auth'

import MyTokensRoute from './page'

const state = vi.hoisted(() => ({ controller: null as unknown as TokensController }))

vi.mock('@/lib/admin/tokens/use-tokens', () => ({ useTokens: () => state.controller }))

const me: VerifiedUser = {
  id: '99999999-9999-4999-8999-999999999999',
  email: 'operator@example.com',
  display_name: 'Operator',
  is_active: true,
  roles: [],
}

function renderRoute() {
  return render(
    <AuthUserProvider user={me}>
      <MyTokensRoute />
    </AuthUserProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  state.controller = {
    tokens: [],
    loading: false,
    loadError: null,
    reload: vi.fn(),
    mint: vi.fn(),
    revoke: vi.fn(),
  }
})

// The no-second-auth-call test spies `globalThis.fetch`. It calls through, so
// leaving it in place is harmless today — but a spy that outlives its test is a
// trap for whoever adds the next one, and this file had nothing to take it down.
afterEach(() => {
  vi.restoreAllMocks()
})

describe('/me/tokens', () => {
  it('renders for a verified user with no admin role and no second auth call', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    renderRoute()

    expect(screen.getByRole('heading', { level: 1, name: 'MCP tokens' })).toBeInTheDocument()
    // The protected layout already resolved `ready` and shares the verdict; a
    // second `/auth/me` here would re-ask a question already answered.
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('ends the breadcrumb on the page title', () => {
    renderRoute()

    const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' })
    const crumbs = within(breadcrumb).getAllByRole('listitem')
    expect(crumbs.map((crumb) => crumb.textContent)).toEqual(['Account', 'MCP tokens'])
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('MCP tokens')
  })

  it('names the account the tokens will speak for', () => {
    renderRoute()
    expect(screen.getByText(new RegExp(me.email))).toBeInTheDocument()
  })

  it('mounts the panel in self scope', () => {
    renderRoute()
    expect(screen.getByText(/authenticate to NOA as you/i)).toBeInTheDocument()
  })
})
