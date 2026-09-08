import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthUserProvider } from '@/lib/auth/auth-context'
import type { VerifiedUser } from '@/lib/auth/use-verified-auth'

const nav = vi.hoisted(() => {
  const replace = vi.fn()
  const push = vi.fn()
  return { replace, push, router: { replace, push, refresh: () => {} } }
})

vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
}))

import HomeRoute from './page'

// The real context provider, not a mocked hook: the protected layout is what
// supplies this, and the page's claim is that it needs no auth call of its own.
const user = (roles: string[]): VerifiedUser => ({
  id: 'me-1',
  email: 'operator@example.com',
  display_name: 'Operator',
  is_active: true,
  roles,
})

const renderAs = (roles: string[]) =>
  render(
    <AuthUserProvider user={user(roles)}>
      <HomeRoute />
    </AuthUserProvider>,
  )

beforeEach(() => {
  nav.replace.mockReset()
  nav.push.mockReset()
})

// A23 — `/` stays a server 307 (pinned in `tests/framing-live.server.test.ts`)
// and lands here; this is where the role decides the destination.
describe('/home dispatch (A23)', () => {
  it('sends an admin to the first admin vertical', () => {
    renderAs(['admin'])
    expect(nav.replace).toHaveBeenCalledWith('/admin/users')
  })

  it('sends everyone else to their own MCP tokens', () => {
    renderAs(['user'])
    expect(nav.replace).toHaveBeenCalledWith('/me/tokens')
  })

  it('sends a user with no roles at all to the same non-admin landing', () => {
    // The previous default was `/admin/users`, which answered this operator's
    // first request with a 403.
    renderAs([])
    expect(nav.replace).toHaveBeenCalledWith('/me/tokens')
  })

  it('replaces rather than pushes, so Back does not return to the dispatcher', () => {
    renderAs(['admin'])
    expect(nav.push).not.toHaveBeenCalled()
    expect(nav.replace).toHaveBeenCalledTimes(1)
  })

  it('matches the admin gate exactly: a differently-cased role is not admin here either', () => {
    // `use-verified-auth.ts:69` tests `roles.includes('admin')`. A looser check
    // here would route this operator to a page whose own gate then refuses
    // them — a 403 this page caused.
    renderAs(['Admin'])
    expect(nav.replace).toHaveBeenCalledWith('/me/tokens')
  })
})

describe('/home while it replaces', () => {
  it('renders a loading state rather than nothing', () => {
    renderAs(['admin'])
    expect(screen.getByLabelText('Loading page')).toBeInTheDocument()
  })

  it('does not open a second main landmark inside the shell', () => {
    // This page renders inside AppShell, which already owns the page `main`.
    // The pre-shell LoadingView owns one of its own (states.tsx:56-58) and is
    // the wrong component here.
    const { container } = renderAs(['admin'])
    expect(container.querySelector('main')).toBeNull()
    expect(container).not.toBeEmptyDOMElement()
  })
})
