import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import type { PmgServer } from '@/lib/admin/pmg/types'
import type { PmgServersController } from '@/lib/admin/pmg/use-pmg-servers'

import { PmgServersPage } from './pmg-servers-page'

const state = vi.hoisted(() => ({ controller: null as unknown as PmgServersController }))

vi.mock('@/lib/admin/pmg/use-pmg-servers', () => ({
  usePmgServers: () => state.controller,
}))

const serverA: PmgServer = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'alpha',
  ssh_host: 'alpha.example.com',
  ssh_username: 'root',
  ssh_port: null,
  ssh_host_key_fingerprint: 'SHA256:alpha',
  has_ssh_password: false,
  has_ssh_private_key: true,
}
const serverB: PmgServer = {
  ...serverA,
  id: '22222222-2222-4222-8222-222222222222',
  name: 'bravo',
  ssh_host: 'bravo.example.com',
  ssh_host_key_fingerprint: null,
}

function makeController(over: Partial<PmgServersController> = {}): PmgServersController {
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

describe('PmgServersPage', () => {
  it('follows the standard header/filter/table structure', () => {
    render(<PmgServersPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'PMG servers' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search by name, host, or ID')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /add server/i })).toBeInTheDocument()
  })

  it('renders each server with a full monospace identifier', () => {
    render(<PmgServersPage />)
    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText(serverA.id)).toBeInTheDocument()
    expect(screen.getByText(serverB.id)).toBeInTheDocument()
  })

  it('surfaces the host-key pinning state in the table', () => {
    render(<PmgServersPage />)
    expect(screen.getByText('Host key pinned')).toBeInTheDocument()
    expect(screen.getByText('Fingerprint missing')).toBeInTheDocument()
  })

  it('shows an error state with a working retry', () => {
    state.controller = makeController({ loadError: 'Unable to load PMG servers' })
    render(<PmgServersPage />)
    expect(screen.getByText('Unable to load PMG servers')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })

  it('shows the empty state when there are no servers', () => {
    state.controller = makeController({ servers: [] })
    render(<PmgServersPage />)
    expect(screen.getByText('No PMG servers yet')).toBeInTheDocument()
  })

  it('filters the list by the search term', () => {
    render(<PmgServersPage />)
    fireEvent.change(screen.getByPlaceholderText('Search by name, host, or ID'), {
      target: { value: 'bravo' },
    })
    expect(screen.queryByText('alpha')).not.toBeInTheDocument()
    expect(screen.getByText('bravo')).toBeInTheDocument()
  })

  it('opens the detail drawer for a clicked row', () => {
    render(<PmgServersPage />)
    fireEvent.click(screen.getByText('alpha'))
    expect(state.controller.selectServer).toHaveBeenCalledWith(serverA.id)
  })

  it('renders the detail drawer when a server is selected without exposing a secret', () => {
    state.controller = makeController({ selectedServer: serverA })
    render(<PmgServersPage />)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('alpha')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete server' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /validate/i })).toBeInTheDocument()
    // The pinned fingerprint is shown, but never a secret value input.
    expect(within(dialog).getByText('SHA256:alpha')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/SSH password/i)).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/SSH private key/i)).not.toBeInTheDocument()
  })
})
