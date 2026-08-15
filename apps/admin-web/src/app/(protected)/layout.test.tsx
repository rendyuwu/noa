import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'

import type { VerifiedAuthState } from '@/lib/auth/use-verified-auth'

const hook = vi.hoisted(() => ({
  state: { status: 'loading', user: null, isAdmin: false } as VerifiedAuthState,
}))
vi.mock('@/lib/auth/use-verified-auth', () => ({
  useVerifiedAuth: () => hook.state,
}))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/assistant',
}))
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}))

import ProtectedLayout from './layout'

const ready = (roles: string[]): VerifiedAuthState => ({
  status: 'ready',
  isAdmin: roles.includes('admin'),
  user: { id: '1', email: 'op@biznetgio.com', display_name: 'Operator', is_active: true, roles },
})

const renderLayout = () => render(<ProtectedLayout>protected content</ProtectedLayout>)

describe('ProtectedLayout (single AppShell + auth gate)', () => {
  beforeEach(() => {
    hook.state = { status: 'loading', user: null, isAdmin: false }
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows a loading state while /auth/me is revalidating (no protected content)', () => {
    hook.state = { status: 'loading', user: null, isAdmin: false }
    renderLayout()
    expect(screen.getByLabelText('Verifying session')).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('renders a distinct pending-approval state for an unapproved account', () => {
    hook.state = { status: 'pending', user: null, isAdmin: false }
    renderLayout()
    expect(screen.getByRole('heading', { name: /pending approval/i })).toBeInTheDocument()
  })

  it('renders a distinct unexpected-error state when verification fails', () => {
    hook.state = { status: 'error', user: null, isAdmin: false }
    renderLayout()
    expect(screen.getByRole('heading', { name: /something went wrong/i })).toBeInTheDocument()
  })

  it('mounts the shell and renders protected content for a verified user', () => {
    hook.state = ready(['user'])
    renderLayout()
    expect(screen.getByText('protected content')).toBeInTheDocument()
  })

  it('shows UserMenu identity from the verified /auth/me user', () => {
    hook.state = ready(['user'])
    renderLayout()
    expect(screen.getByText('Operator')).toBeInTheDocument()
  })

  it('shows role-gated navigation for an admin', () => {
    hook.state = ready(['admin'])
    renderLayout()
    expect(screen.getByText('Administration')).toBeInTheDocument()
  })

  it('hides role-gated navigation for a non-admin (visibility from /auth/me roles)', () => {
    hook.state = ready(['user'])
    renderLayout()
    expect(screen.queryByText('Administration')).not.toBeInTheDocument()
  })
})
