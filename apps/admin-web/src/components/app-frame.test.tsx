import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'

import { AppFrame } from '@/components/app-frame'
import type { VerifiedUser } from '@/lib/auth/use-verified-auth'

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

const user: VerifiedUser = {
  id: '1',
  email: 'op@biznetgio.com',
  display_name: 'Operator',
  is_active: true,
  roles: ['user'],
}

const renderFrame = () => render(<AppFrame user={user}>page body</AppFrame>)
const palette = () => screen.queryByPlaceholderText(/search pages and actions/i)

describe('AppFrame command search (Ctrl/Cmd+K)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('is closed until invoked', () => {
    renderFrame()
    expect(palette()).not.toBeInTheDocument()
  })

  it('opens the palette on Ctrl+K and closes it on Escape', async () => {
    renderFrame()
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    await waitFor(() => expect(palette()).toBeInTheDocument())
    fireEvent.keyDown(palette()!, { key: 'Escape' })
    await waitFor(() => expect(palette()).not.toBeInTheDocument())
  })

  it('opens the palette on Cmd+K (macOS)', async () => {
    renderFrame()
    fireEvent.keyDown(document, { key: 'k', metaKey: true })
    await waitFor(() => expect(palette()).toBeInTheDocument())
  })

  it('lists permitted pages and actions only — no admin page for a non-admin', async () => {
    renderFrame()
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    await waitFor(() => expect(palette()).toBeInTheDocument())
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Sign out')
    expect(dialog).not.toHaveTextContent('Administration')
  })
})
