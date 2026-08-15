import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import { CreateRoleDialog, type CreateRoleDialogProps } from './create-role-dialog'

// Mock typed to the actual prop signature so it is assignable to onCreateAction
// (a bare vi.fn() infers vitest 4's over-generic Mock<Procedure | Constructable>).
type CreateRoleMock = Mock<CreateRoleDialogProps['onCreateAction']>

beforeEach(() => {
  vi.clearAllMocks()
})

function renderDialog(onCreate: CreateRoleMock) {
  const onOpenChange = vi.fn()
  render(
    <CreateRoleDialog open onOpenChangeAction={onOpenChange} onCreateAction={onCreate} />,
  )
  return { onOpenChange }
}

describe('CreateRoleDialog', () => {
  it('uses a labeled FormField and creates a role on submit', async () => {
    const onCreate = vi.fn().mockResolvedValue({ ok: true, current: true })
    const { onOpenChange } = renderDialog(onCreate)

    fireEvent.change(screen.getByLabelText('Role name'), { target: { value: 'support' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create role' }))

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('support'))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('validates a required name without calling onCreate', async () => {
    const onCreate = vi.fn().mockResolvedValue({ ok: true, current: true })
    renderDialog(onCreate)

    fireEvent.click(screen.getByRole('button', { name: 'Create role' }))

    expect(await screen.findByText('Role name is required')).toBeInTheDocument()
    expect(onCreate).not.toHaveBeenCalled()
  })

  it('preserves the input and links the stable backend detail on a duplicate name', async () => {
    const onCreate = vi
      .fn()
      .mockResolvedValue({ ok: false, message: 'Role already exists', current: true })
    const { onOpenChange } = renderDialog(onCreate)

    const input = screen.getByLabelText('Role name') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create role' }))

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('admin'))
    // Error surfaced against the field; input preserved; dialog stays open.
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Role already exists')).toBeInTheDocument()
    expect((screen.getByLabelText('Role name') as HTMLInputElement).value).toBe('admin')
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
  })
})
