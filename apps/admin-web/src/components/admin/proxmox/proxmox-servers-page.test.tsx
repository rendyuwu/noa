import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import type { ProxmoxServer } from '@/lib/admin/proxmox/types'
import type { ProxmoxServersController } from '@/lib/admin/proxmox/use-proxmox-servers'

import { ProxmoxServersPage } from './proxmox-servers-page'

const state = vi.hoisted(() => ({ controller: null as unknown as ProxmoxServersController }))

vi.mock('@/lib/admin/proxmox/use-proxmox-servers', () => ({
  useProxmoxServers: () => state.controller,
}))

const serverA: ProxmoxServer = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'alpha',
  base_url: 'https://alpha.example.com:8006',
  api_token_id: 'root@pam!noa',
  has_api_token_secret: true,
  verify_ssl: true,
}
const serverB: ProxmoxServer = {
  ...serverA,
  id: '22222222-2222-4222-8222-222222222222',
  name: 'bravo',
  base_url: 'https://bravo.example.com:8006',
  verify_ssl: false,
}

function makeController(over: Partial<ProxmoxServersController> = {}): ProxmoxServersController {
  return {
    servers: [serverA, serverB],
    loading: false,
    loadError: null,
    reload: vi.fn(),
    selectedServer: null,
    selectServer: vi.fn(),
    validateResultById: {},
    validateBusyId: null,
    deleteBusyId: null,
    createServer: vi.fn(),
    updateServer: vi.fn(),
    deleteServer: vi.fn(),
    validateServer: vi.fn(),
    ...over,
  }
}

beforeEach(() => {
  state.controller = makeController()
})

describe('ProxmoxServersPage', () => {
  it('follows the standard header/filter/table structure', () => {
    render(<ProxmoxServersPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Proxmox servers' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search by name, URL, or ID')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /add server/i })).toBeInTheDocument()
  })

  it('renders each server with a full monospace identifier', () => {
    render(<ProxmoxServersPage />)
    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText(serverA.id)).toBeInTheDocument()
    expect(screen.getByText(serverB.id)).toBeInTheDocument()
  })

  it('surfaces the SSL verification state in the table', () => {
    render(<ProxmoxServersPage />)
    expect(screen.getByText('Verify enabled')).toBeInTheDocument()
    expect(screen.getByText('Verification off')).toBeInTheDocument()
  })

  it('shows an error state with a working retry', () => {
    state.controller = makeController({ loadError: 'Unable to load Proxmox servers' })
    render(<ProxmoxServersPage />)
    expect(screen.getByText('Unable to load Proxmox servers')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })

  it('shows the empty state when there are no servers', () => {
    state.controller = makeController({ servers: [] })
    render(<ProxmoxServersPage />)
    expect(screen.getByText('No Proxmox servers yet')).toBeInTheDocument()
  })

  it('filters the list by the search term', () => {
    render(<ProxmoxServersPage />)
    fireEvent.change(screen.getByPlaceholderText('Search by name, URL, or ID'), {
      target: { value: 'bravo' },
    })
    expect(screen.queryByText('alpha')).not.toBeInTheDocument()
    expect(screen.getByText('bravo')).toBeInTheDocument()
  })

  it('opens the detail drawer for a clicked row', () => {
    render(<ProxmoxServersPage />)
    fireEvent.click(screen.getByText('alpha'))
    expect(state.controller.selectServer).toHaveBeenCalledWith(serverA.id)
  })

  it('renders the detail drawer when a server is selected without exposing a secret', () => {
    state.controller = makeController({ selectedServer: serverA })
    render(<ProxmoxServersPage />)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('alpha')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete server' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /validate/i })).toBeInTheDocument()
    // The stored-secret presence is shown, but never a secret value input.
    expect(within(dialog).getByText('Stored')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/API token secret/i)).not.toBeInTheDocument()
  })
})
