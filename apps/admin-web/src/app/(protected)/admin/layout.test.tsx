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

import AdminLayout from './layout'

const admin = {
  id: 'me-1',
  email: 'root@example.com',
  display_name: 'Root',
  is_active: true,
  roles: ['admin'],
}

const child = <div data-testid="child">page</div>

beforeEach(() => {
  state.value = { status: 'ready', user: admin, isAdmin: true }
})

// One gate for every route under /admin. These five cases were asserted once
// per route page while each page carried its own copy; the pages no longer have
// a gate to assert, so they are asserted here, over a stand-in child.
describe('/admin section gate', () => {
  it('re-verifies with requireAdmin so the server decides access', () => {
    render(<AdminLayout>{child}</AdminLayout>)
    expect(state.lastOptions).toEqual({ requireAdmin: true })
  })

  it('renders the page for a verified admin', () => {
    render(<AdminLayout>{child}</AdminLayout>)
    expect(screen.getByTestId('child')).toBeInTheDocument()
  })

  it('shows a loading placeholder while verifying', () => {
    state.value = { status: 'loading', user: null, isAdmin: false }
    render(<AdminLayout>{child}</AdminLayout>)
    expect(screen.getByLabelText('Loading page')).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })

  it('renders nothing on an unexpected auth error', () => {
    state.value = { status: 'error', user: null, isAdmin: false }
    const { container } = render(<AdminLayout>{child}</AdminLayout>)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the 403 state to a verified non-admin, never a blank page', () => {
    // Role-denied resolves to a state instead of a redirect: every route in
    // this section is admin-only, so a redirect would only land on another one.
    // Rendering nothing would leave the operator inside the shell with no
    // explanation for the empty page.
    state.value = { status: 'forbidden', user: { ...admin, roles: [] }, isAdmin: false }
    render(<AdminLayout>{child}</AdminLayout>)
    expect(screen.getByRole('heading', { name: /have access/i })).toBeInTheDocument()
    expect(screen.queryByTestId('child')).not.toBeInTheDocument()
  })
})
