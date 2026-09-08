import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import type { McpToken } from '@/lib/admin/tokens/types'
import { TOKEN_ABSENT_MESSAGE, type TokensController } from '@/lib/admin/tokens/use-tokens'

import { TokensPanel } from './tokens-panel'

const state = vi.hoisted(() => ({ controller: null as unknown as TokensController }))
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  danger: vi.fn(),
  info: vi.fn(),
}))

// Partial mock: only the hook is replaced. `TOKEN_ABSENT_MESSAGE` is asserted
// below and must stay the real constant, or this test pins a string nobody ships.
vi.mock('@/lib/admin/tokens/use-tokens', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/admin/tokens/use-tokens')>()
  return { ...actual, useTokens: () => state.controller }
})
vi.mock('@gio/bigsu-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@gio/bigsu-ui')>()
  return { ...actual, bigsuToast: toast }
})

function makeToken(over: Partial<McpToken> = {}): McpToken {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    user_id: '99999999-9999-4999-8999-999999999999',
    token_prefix: 'noa_aaaabbbb',
    label: 'Laptop',
    librechat_user_id: null,
    last_used_at: null,
    last_ldap_check_at: null,
    expires_at: null,
    created_at: '2026-09-01T09:00:00Z',
    ...over,
  }
}

const live = makeToken()
const expired = makeToken({
  id: '22222222-2222-4222-8222-222222222222',
  token_prefix: 'noa_ccccdddd',
  label: null,
  expires_at: '2020-01-01T00:00:00Z',
})

function makeController(over: Partial<TokensController> = {}): TokensController {
  return {
    tokens: [live, expired],
    loading: false,
    loadError: null,
    reload: vi.fn(),
    mint: vi.fn(),
    revoke: vi.fn(),
    ...over,
  }
}

// The row-action menu is a Radix DropdownMenu behind an IconButton; Enter on the
// trigger opens it without needing jsdom to synthesise pointer capture.
function openRowActions(index = 0) {
  const triggers = screen.getAllByRole('button', { name: 'Row actions' })
  fireEvent.keyDown(triggers[index]!, { key: 'Enter', code: 'Enter' })
  return screen.findByRole('menu')
}

const primaryButtons = () =>
  Array.from(document.querySelectorAll('button')).filter((button) =>
    button.classList.contains('bg-action-primary'),
  )

beforeEach(() => {
  vi.clearAllMocks()
  state.controller = makeController()
})

describe('TokensPanel — structure and states', () => {
  it('puts mint and refresh in the toolbar row, with exactly one primary (A18)', () => {
    render(<TokensPanel scope={{ kind: 'self' }} />)

    expect(screen.getByRole('button', { name: /mint token/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    expect(primaryButtons().map((button) => button.textContent)).toEqual(['Mint token'])
  })

  it('renders the prefix through createIdColumn and the status through StatusChip (A14)', () => {
    render(<TokensPanel scope={{ kind: 'self' }} />)

    const prefix = screen.getByText(live.token_prefix)
    expect(prefix.className).toContain('font-mono')

    // The 11-status vocabulary has no `Expired`, so an expired token reads
    // Inactive — and both render as chips, never as coloured text.
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('Inactive')).toBeInTheDocument()
  })

  it('renders an unlabelled token as an em dash and a never-used one as Never', () => {
    render(<TokensPanel scope={{ kind: 'self' }} />)

    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getAllByText('Never').length).toBeGreaterThan(0)
  })

  it('ships the empty state with a way out (A13)', () => {
    state.controller = makeController({ tokens: [] })
    render(<TokensPanel scope={{ kind: 'self' }} />)

    expect(screen.getByText('No MCP tokens yet')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /mint token/i }).length).toBe(2)
    // The empty-state call to action must not become a second primary.
    expect(primaryButtons()).toHaveLength(1)
  })

  it('ships the error state with a working retry (A13)', () => {
    state.controller = makeController({ loadError: 'Unable to load MCP tokens' })
    render(<TokensPanel scope={{ kind: 'self' }} />)

    expect(screen.getByText('Unable to load MCP tokens')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })

  it('ships the loading state and blocks a duplicate refresh (A13)', () => {
    state.controller = makeController({ loading: true, tokens: [] })
    render(<TokensPanel scope={{ kind: 'self' }} />)

    expect(screen.getByRole('button', { name: /refresh/i })).toBeDisabled()
    // Skeleton rows, not the empty state: an empty table during a load reads as
    // "you have no tokens", which is a claim the UI cannot make yet.
    expect(screen.queryByText('No MCP tokens yet')).not.toBeInTheDocument()
  })

  it('ships search and pagination (A13)', () => {
    const many = Array.from({ length: 12 }, (_, index) =>
      makeToken({
        id: `id-${index}`,
        token_prefix: `noa_prefix${String(index).padStart(2, '0')}`,
        label: `token-${index}`,
      }),
    )
    state.controller = makeController({ tokens: many })
    render(<TokensPanel scope={{ kind: 'self' }} />)

    // Pagination caps the first page at 10 of the 12 rows.
    expect(screen.getAllByRole('button', { name: 'Row actions' })).toHaveLength(10)
    expect(screen.getByText('noa_prefix00')).toBeInTheDocument()
    expect(screen.queryByText('noa_prefix11')).not.toBeInTheDocument()

    const search = screen.getByLabelText('Search')
    fireEvent.change(search, { target: { value: 'token-11' } })
    expect(screen.getByText('noa_prefix11')).toBeInTheDocument()
    expect(screen.queryByText('noa_prefix00')).not.toBeInTheDocument()
  })

  it('refreshes on demand', () => {
    render(<TokensPanel scope={{ kind: 'self' }} />)
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })
})

describe('TokensPanel — revoke (A16)', () => {
  it('offers revoke last, marked destructive', async () => {
    render(<TokensPanel scope={{ kind: 'self' }} />)
    const menu = await openRowActions()

    const items = within(menu).getAllByRole('menuitem')
    const revoke = items[items.length - 1]!
    expect(revoke).toHaveTextContent('Revoke token')
    expect(revoke.className).toContain('text-status-danger')
    // `icon: 'delete'` — a BigsuIcon, never a raw glyph or an emoji.
    expect(revoke.querySelector('svg')).not.toBeNull()
  })

  it('confirms through a danger-toned ConfirmDialog before revoking', async () => {
    const revoke = vi.fn().mockResolvedValue({ ok: true })
    state.controller = makeController({ revoke })
    render(<TokensPanel scope={{ kind: 'self' }} />)

    const menu = await openRowActions()
    fireEvent.click(within(menu).getByRole('menuitem', { name: /revoke token/i }))

    // The menu closes itself on select, so the dialog has to be controlled and
    // rendered outside it — an uncontrolled trigger would go down with the menu.
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Revoke this token\?/i)).toBeInTheDocument()
    expect(within(dialog).getByText(new RegExp(live.token_prefix))).toBeInTheDocument()
    expect(revoke).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Revoke token' }))
    await waitFor(() => expect(revoke).toHaveBeenCalledWith(live.id))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  it('keeps the confirmation open when the revoke fails', async () => {
    // The A17 wording is the controller's; assert the constant so a reword
    // cannot leave this test passing against a string nobody ships.
    const revoke = vi.fn().mockResolvedValue({ ok: false, message: TOKEN_ABSENT_MESSAGE })
    state.controller = makeController({ revoke })
    render(<TokensPanel scope={{ kind: 'self' }} />)

    const menu = await openRowActions()
    fireEvent.click(within(menu).getByRole('menuitem', { name: /revoke token/i }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revoke token' }))

    await waitFor(() =>
      expect(toast.danger).toHaveBeenCalledWith('Could not revoke the token', {
        description: TOKEN_ABSENT_MESSAGE,
      }),
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('says nothing when a 401 is already navigating away', async () => {
    const revoke = vi.fn().mockResolvedValue({ redirecting: true })
    state.controller = makeController({ revoke })
    render(<TokensPanel scope={{ kind: 'self' }} />)

    const menu = await openRowActions()
    fireEvent.click(within(menu).getByRole('menuitem', { name: /revoke token/i }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revoke token' }))

    await waitFor(() => expect(revoke).toHaveBeenCalled())
    expect(toast.success).not.toHaveBeenCalled()
    expect(toast.danger).not.toHaveBeenCalled()
  })
})

describe('TokensPanel — scope', () => {
  it('names whose tokens these are in the empty state', () => {
    state.controller = makeController({ tokens: [] })
    const { unmount } = render(<TokensPanel scope={{ kind: 'user', userId: 'user-7' }} />)
    expect(screen.getByText(/authenticate to NOA as this user/i)).toBeInTheDocument()
    unmount()

    render(<TokensPanel scope={{ kind: 'self' }} />)
    expect(screen.getByText(/authenticate to NOA as you/i)).toBeInTheDocument()
  })
})
