import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import { RoleDetailDrawer, type RoleDetailDrawerProps } from './role-detail-drawer'

function renderDrawer(over: Partial<RoleDetailDrawerProps> = {}) {
  const props: RoleDetailDrawerProps = {
    role: over.role ?? 'admin',
    availableTools: over.availableTools ?? ['tool.read', 'tool.write'],
    roleTools: over.roleTools ?? ['tool.read'],
    roleToolsLoading: over.roleToolsLoading ?? false,
    roleToolsError: over.roleToolsError ?? null,
    onCloseAction: over.onCloseAction ?? vi.fn(),
    onSaveToolsAction:
      over.onSaveToolsAction ?? vi.fn().mockResolvedValue({ ok: true, current: true }),
    onDeleteAction: over.onDeleteAction ?? vi.fn().mockResolvedValue({ ok: true, current: true }),
  }
  render(<RoleDetailDrawer {...props} />)
  return props
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('RoleDetailDrawer', () => {
  it('titles the panel with the role and seeds the allowlist from the role tools', () => {
    renderDrawer()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('admin')).toBeInTheDocument()
    // Full tool identifiers render as monospace checkbox labels.
    const readBox = within(dialog).getByRole('checkbox', { name: 'tool.read' })
    expect(readBox).toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: 'tool.write' })).not.toBeChecked()
  })

  it('shows a loading state while role tools load', () => {
    renderDrawer({ roleToolsLoading: true, roleTools: [] })
    expect(screen.getByText('Loading role tools…')).toBeInTheDocument()
  })

  it('disables Save allowlist until the selection changes', () => {
    renderDrawer()
    expect(screen.getByRole('button', { name: 'Save allowlist' })).toBeDisabled()
  })

  it('saves the edited allowlist through onSaveTools', async () => {
    const onSaveToolsAction = vi.fn().mockResolvedValue({ ok: true, current: true })
    renderDrawer({ onSaveToolsAction })

    fireEvent.click(screen.getByRole('checkbox', { name: 'tool.write' }))
    const save = screen.getByRole('button', { name: 'Save allowlist' })
    await waitFor(() => expect(save).toBeEnabled())
    fireEvent.click(save)

    await waitFor(() => expect(onSaveToolsAction).toHaveBeenCalledTimes(1))
    const [name, tools] = onSaveToolsAction.mock.calls[0]!
    expect(name).toBe('admin')
    expect([...(tools as string[])].sort()).toEqual(['tool.read', 'tool.write'])
  })

  it('filters the tool list by the filter input', () => {
    renderDrawer({ availableTools: ['alpha.read', 'beta.write'], roleTools: [] })
    fireEvent.change(screen.getByLabelText('Filter tools'), { target: { value: 'beta' } })
    expect(screen.queryByRole('checkbox', { name: 'alpha.read' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'beta.write' })).toBeInTheDocument()
  })

  it('preserves the edited selection and surfaces the stable detail when save fails', async () => {
    const onSaveToolsAction = vi
      .fn()
      .mockResolvedValue({ ok: false, message: 'Unknown tools: ghost', current: true })
    renderDrawer({ onSaveToolsAction })

    fireEvent.click(screen.getByRole('checkbox', { name: 'tool.write' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save allowlist' }))

    await waitFor(() => expect(onSaveToolsAction).toHaveBeenCalled())
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Unknown tools: ghost')).toBeInTheDocument()
    // Edited selection preserved on failure.
    expect(within(dialog).getByRole('checkbox', { name: 'tool.write' })).toBeChecked()
  })

  it('routes deletion through a confirm dialog before calling onDelete', async () => {
    const onDeleteAction = vi.fn().mockResolvedValue({ ok: true, current: true })
    renderDrawer({ onDeleteAction })

    fireEvent.click(screen.getByRole('button', { name: 'Delete role' }))
    expect(onDeleteAction).not.toHaveBeenCalled()

    // The ConfirmDialog adds a second "Delete role" button (the confirm).
    const buttons = screen.getAllByRole('button', { name: 'Delete role' })
    fireEvent.click(buttons[buttons.length - 1]!)
    await waitFor(() => expect(onDeleteAction).toHaveBeenCalledWith('admin'))
  })

  it('surfaces a load error against the allowlist field', () => {
    renderDrawer({ roleToolsError: 'Unable to load role tools', roleTools: [] })
    expect(screen.getByText('Unable to load role tools')).toBeInTheDocument()
  })
})
