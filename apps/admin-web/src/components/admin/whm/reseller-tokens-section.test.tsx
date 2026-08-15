import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import type { WhmServerToken } from '@/lib/admin/whm/types'
import type { ResellerTokensController } from '@/lib/admin/whm/use-reseller-tokens'

import { ResellerTokensSection } from './reseller-tokens-section'

const state = vi.hoisted(() => ({ controller: null as unknown as ResellerTokensController }))

vi.mock('@/lib/admin/whm/use-reseller-tokens', () => ({
  useResellerTokens: () => state.controller,
}))

const token: WhmServerToken = {
  id: 'token-1',
  server_id: 'server-1',
  owner_username: 'reseller1',
  api_username: 'reseller1',
  updated_at: '2026-01-02T03:04:05.000Z',
}

function makeController(over: Partial<ResellerTokensController> = {}): ResellerTokensController {
  return {
    tokens: [token],
    loading: false,
    loadError: null,
    hasLoaded: true,
    load: vi.fn(),
    reload: vi.fn(),
    validateResultById: {},
    validateBusyId: null,
    deleteBusyId: null,
    createToken: vi.fn().mockResolvedValue({ ok: true }),
    rotateToken: vi.fn().mockResolvedValue({ ok: true }),
    deleteToken: vi.fn().mockResolvedValue({ ok: true }),
    validateToken: vi.fn().mockResolvedValue({ ok: true }),
    ...over,
  }
}

beforeEach(() => {
  state.controller = makeController()
})

function expand() {
  fireEvent.click(screen.getByRole('button', { name: 'Reseller tokens' }))
}

describe('ResellerTokensSection', () => {
  it('lazy-loads on expand and never renders a token value or a read-view token field', () => {
    render(<ResellerTokensSection serverId="server-1" />)
    expect(state.controller.load).not.toHaveBeenCalled()
    expand()
    expect(state.controller.load).toHaveBeenCalled()
    expect(screen.getByText('reseller1')).toBeInTheDocument()
    expect(screen.getByText('API user: reseller1')).toBeInTheDocument()
    // The read view exposes no token entry field and echoes no secret.
    expect(screen.queryByLabelText('API token')).not.toBeInTheDocument()
    expect(screen.queryByText(/RESELLER_TOKEN|SECRET/)).not.toBeInTheDocument()
  })

  it('shows the empty state when there are no tokens', () => {
    state.controller = makeController({ tokens: [] })
    render(<ResellerTokensSection serverId="server-1" />)
    expand()
    expect(screen.getByText(/No reseller tokens/)).toBeInTheDocument()
  })

  it('surfaces a token validation failure inline', () => {
    state.controller = makeController({
      validateResultById: { 'token-1': { ok: false, message: 'Token rejected' } },
    })
    render(<ResellerTokensSection serverId="server-1" />)
    expand()
    expect(screen.getByText('Token rejected')).toBeInTheDocument()
  })

  it('routes token deletion through a confirm dialog', async () => {
    render(<ResellerTokensSection serverId="server-1" />)
    expand()
    fireEvent.click(screen.getByRole('button', { name: 'Delete token reseller1' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete reseller token?' })
    expect(state.controller.deleteToken).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete token' }))
    await waitFor(() => expect(state.controller.deleteToken).toHaveBeenCalledWith('token-1'))
  })

  it('validates a token from its row action', async () => {
    render(<ResellerTokensSection serverId="server-1" />)
    expand()
    fireEvent.click(screen.getByRole('button', { name: 'Validate token reseller1' }))
    await waitFor(() => expect(state.controller.validateToken).toHaveBeenCalledWith('token-1'))
  })

  it('opens the rotate dialog with a read-only owner and clears the token on cancel', async () => {
    render(<ResellerTokensSection serverId="server-1" />)
    expand()
    fireEvent.click(screen.getByRole('button', { name: 'Rotate token reseller1' }))

    // Owner is the routing key — read-only in rotate mode.
    expect(screen.getByLabelText('Owner username')).toHaveAttribute('readonly')

    // Type a secret, cancel, reopen: the token field must not survive (write-only).
    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'SECRET_TOKEN' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Rotate token reseller1' }))
    expect(screen.getByLabelText('API token')).toHaveValue('')
  })

  it('sends the token write-only in the create body and never re-renders it', async () => {
    state.controller = makeController({ tokens: [] })
    render(<ResellerTokensSection serverId="server-1" />)
    expand()
    fireEvent.click(screen.getByRole('button', { name: /add token/i }))

    fireEvent.change(screen.getByLabelText('Owner username'), { target: { value: 'reseller1' } })
    fireEvent.change(screen.getByLabelText('API username'), { target: { value: 'reseller1' } })
    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'RESELLER_TOKEN' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add token' }))

    await waitFor(() =>
      expect(state.controller.createToken).toHaveBeenCalledWith({
        owner_username: 'reseller1',
        api_username: 'reseller1',
        api_token: 'RESELLER_TOKEN',
      }),
    )
    // The entered secret must never appear anywhere in the DOM afterward.
    expect(screen.queryByText('RESELLER_TOKEN')).not.toBeInTheDocument()
  })

  it('rotates without re-keying the owner and sends the token only when entered', async () => {
    render(<ResellerTokensSection serverId="server-1" />)
    expand()
    fireEvent.click(screen.getByRole('button', { name: 'Rotate token reseller1' }))
    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'NEW_TOKEN' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save token' }))

    await waitFor(() => expect(state.controller.rotateToken).toHaveBeenCalled())
    const [, body] = (state.controller.rotateToken as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, Record<string, unknown>]
    // Owner is never sent on rotate; api_token only when a new value was entered.
    expect(body).not.toHaveProperty('owner_username')
    expect(body).toEqual({ api_username: 'reseller1', api_token: 'NEW_TOKEN' })
  })
})
