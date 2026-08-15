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

vi.mock('@/components/admin/proxmox/proxmox-servers-page', () => ({
  ProxmoxServersPage: () => <div data-testid="proxmox-page">proxmox</div>,
}))

import AdminProxmoxRoute from './page'

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

describe('/admin/proxmox route gate', () => {
  it('re-verifies with requireAdmin so the server decides access', () => {
    render(<AdminProxmoxRoute />)
    expect(state.lastOptions).toEqual({ requireAdmin: true })
  })

  it('renders the Proxmox page for a verified admin', () => {
    render(<AdminProxmoxRoute />)
    expect(screen.getByTestId('proxmox-page')).toBeInTheDocument()
  })

  it('shows a loading placeholder while verifying', () => {
    state.value = { status: 'loading', user: null, isAdmin: false }
    render(<AdminProxmoxRoute />)
    expect(screen.getByLabelText('Loading page')).toBeInTheDocument()
    expect(screen.queryByTestId('proxmox-page')).not.toBeInTheDocument()
  })

  it('renders nothing on an unexpected auth error', () => {
    state.value = { status: 'error', user: null, isAdmin: false }
    const { container } = render(<AdminProxmoxRoute />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the 403 state to a verified non-admin, never a blank page', () => {
    // Role-denied resolves to a state instead of a redirect: every route in
    // this app is admin-only, so a redirect would only land on another one.
    // Rendering nothing would leave the operator inside the shell with no
    // explanation for the empty page.
    state.value = { status: 'forbidden', user: { ...admin, roles: [] }, isAdmin: false }
    render(<AdminProxmoxRoute />)
    expect(screen.getByRole('heading', { name: /have access/i })).toBeInTheDocument()
    expect(screen.queryByTestId('proxmox-page')).not.toBeInTheDocument()
  })
})
