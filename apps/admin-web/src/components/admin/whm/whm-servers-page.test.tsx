import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import type { WhmServer } from '@/lib/admin/whm/types'
import type { WhmServersController } from '@/lib/admin/whm/use-whm-servers'

import { WhmServersPage } from './whm-servers-page'

const state = vi.hoisted(() => ({ controller: null as unknown as WhmServersController }))

vi.mock('@/lib/admin/whm/use-whm-servers', () => ({
  useWhmServers: () => state.controller,
}))

const serverA: WhmServer = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'alpha',
  base_url: 'https://alpha.example.com:2087',
  api_username: 'root',
  ssh_username: null,
  ssh_port: null,
  ssh_host_key_fingerprint: null,
  has_ssh_password: false,
  has_ssh_private_key: false,
  verify_ssl: true,
}
const serverB: WhmServer = {
  ...serverA,
  id: '22222222-2222-4222-8222-222222222222',
  name: 'bravo',
  base_url: 'https://bravo.example.com:2087',
  verify_ssl: false,
}

function makeController(over: Partial<WhmServersController> = {}): WhmServersController {
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

describe('WhmServersPage', () => {
  it('follows the standard header/filter/table structure', () => {
    render(<WhmServersPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'WHM servers' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search by name, URL, or ID')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /add server/i })).toBeInTheDocument()
  })

  it('renders each server with a full monospace identifier', () => {
    render(<WhmServersPage />)
    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText(serverA.id)).toBeInTheDocument()
    expect(screen.getByText(serverB.id)).toBeInTheDocument()
  })

  it('shows an error state with a working retry', () => {
    state.controller = makeController({ loadError: 'Unable to load WHM servers' })
    render(<WhmServersPage />)
    expect(screen.getByText('Unable to load WHM servers')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(state.controller.reload).toHaveBeenCalled()
  })

  it('shows the empty state when there are no servers', () => {
    state.controller = makeController({ servers: [] })
    render(<WhmServersPage />)
    expect(screen.getByText('No WHM servers yet')).toBeInTheDocument()
  })

  it('filters the list by the search term', () => {
    render(<WhmServersPage />)
    fireEvent.change(screen.getByPlaceholderText('Search by name, URL, or ID'), {
      target: { value: 'bravo' },
    })
    expect(screen.queryByText('alpha')).not.toBeInTheDocument()
    expect(screen.getByText('bravo')).toBeInTheDocument()
  })

  it('opens the detail drawer for a clicked row', () => {
    render(<WhmServersPage />)
    fireEvent.click(screen.getByText('alpha'))
    expect(state.controller.selectServer).toHaveBeenCalledWith(serverA.id)
  })

  it('renders the detail drawer when a server is selected', () => {
    state.controller = makeController({ selectedServer: serverA })
    render(<WhmServersPage />)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('alpha')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete server' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /validate/i })).toBeInTheDocument()
  })
})
