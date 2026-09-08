import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import type { AdminUser } from '@/lib/admin/users/types'
import type { VerifiedUser } from '@/lib/auth/use-verified-auth'

import { UserDetailDrawer, type UserDetailDrawerProps } from './user-detail-drawer'

// The drawer links out to the tokens route (§T76) rather than embedding a
// second table, so it now reads the router.
const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('next/navigation', () => ({ useRouter: () => nav }))

const member: AdminUser = {
  id: 'member-1',
  email: 'grace@example.com',
  display_name: 'Grace Hopper',
  roles: ['member'],
  is_active: true,
  last_login_at: '2026-06-01T00:00:00Z',
  tools: ['tool.read', 'tool.write'],
}

const admin: AdminUser = {
  id: 'admin-1',
  email: 'ada@example.com',
  roles: ['admin'],
  is_active: true,
}

const me: VerifiedUser = {
  id: 'me-1',
  email: 'root@example.com',
  display_name: 'Root',
  is_active: true,
  roles: ['admin'],
}

function renderDrawer(over: {
  user?: AdminUser
  me?: VerifiedUser
  allUsers?: AdminUser[]
  onSaveRoles?: ReturnType<typeof vi.fn>
  onSetActive?: ReturnType<typeof vi.fn>
  onDelete?: ReturnType<typeof vi.fn>
} = {}) {
  const props = {
    user: over.user ?? member,
    me: over.me ?? me,
    allUsers: over.allUsers ?? [member, admin, me as unknown as AdminUser],
    availableRoles: ['admin', 'member'],
    onCloseAction: vi.fn(),
    onSaveRolesAction: over.onSaveRoles ?? vi.fn().mockResolvedValue({ ok: true, current: true }),
    onSetActiveAction: over.onSetActive ?? vi.fn().mockResolvedValue({ ok: true, current: true }),
    onDeleteAction: over.onDelete ?? vi.fn().mockResolvedValue({ ok: true, current: true }),
  }
  render(<UserDetailDrawer {...(props as unknown as UserDetailDrawerProps)} />)
  return props
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('UserDetailDrawer', () => {
  it('titles the panel with the user and shows current roles and effective tools', () => {
    renderDrawer()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Grace Hopper')).toBeInTheDocument()
    expect(within(dialog).getByText('grace@example.com')).toBeInTheDocument()
    // Current role seeded into the form control as a chip.
    expect(within(dialog).getByText('member')).toBeInTheDocument()
    // Effective tools listed in monospace.
    expect(within(dialog).getByText('tool.read')).toBeInTheDocument()
    // Full id shown.
    expect(within(dialog).getByText('member-1')).toBeInTheDocument()
  })

  it('disables Save roles until the assignment changes', () => {
    renderDrawer()
    expect(screen.getByRole('button', { name: 'Save roles' })).toBeDisabled()
  })

  it('blocks deactivating and deleting your own account', () => {
    renderDrawer({ user: { ...admin, id: me.id }, me })
    expect(screen.getByRole('button', { name: 'Deactivate' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete user' })).toBeDisabled()
    expect(screen.getByText(/cannot deactivate or delete your own account/i)).toBeInTheDocument()
  })

  it('blocks deactivating, deleting, and removing the role from the last active admin', () => {
    renderDrawer({ user: admin, me, allUsers: [admin] })
    expect(screen.getByRole('button', { name: 'Deactivate' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete user' })).toBeDisabled()
    expect(screen.getByText(/last active admin/i)).toBeInTheDocument()

    const removeAdmin = screen.getByRole('button', { name: /remove admin/i })
    fireEvent.click(removeAdmin)
    // Controlled guard rejects chip removal; assignment remains unchanged.
    expect(screen.getByRole('button', { name: /remove admin/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save roles' })).toBeDisabled()
  })

  it('activates a deactivated user through the status toggle', async () => {
    const onSetActive = vi.fn().mockResolvedValue({ ok: true, current: true })
    renderDrawer({ user: { ...member, is_active: false }, onSetActive })
    fireEvent.click(screen.getByRole('button', { name: 'Activate' }))
    await waitFor(() => expect(onSetActive).toHaveBeenCalledWith(member.id, true))
  })

  it('offers activation, role assignment, and guarded deletion for a pending user', () => {
    renderDrawer({ user: { ...member, is_active: false, last_login_at: null } })
    expect(screen.getByText('Pending')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Activate' })).toBeEnabled()
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete user' })).toBeEnabled()
  })

  it('confirms deactivation before calling onSetActive', async () => {
    const onSetActive = vi.fn().mockResolvedValue({ ok: true, current: true })
    renderDrawer({ onSetActive })

    fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }))
    expect(onSetActive).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Deactivate user' }))
    await waitFor(() => expect(onSetActive).toHaveBeenCalledWith(member.id, false))
  })

  it('routes deletion through a confirm dialog before calling onDelete', async () => {
    const onDelete = vi.fn().mockResolvedValue({ ok: true, current: true })
    renderDrawer({ onDelete })

    // The footer trigger opens the confirmation; nothing is deleted yet.
    fireEvent.click(screen.getByRole('button', { name: 'Delete user' }))
    expect(onDelete).not.toHaveBeenCalled()

    // Opening the ConfirmDialog adds a second "Delete user" button (the confirm);
    // it is the one that mutates.
    const buttons = screen.getAllByRole('button', { name: 'Delete user' })
    fireEvent.click(buttons[buttons.length - 1]!)
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(member.id))
  })

  it('keeps the assignment when a role save fails, and surfaces the stable detail', async () => {
    const onSaveRoles = vi
      .fn()
      .mockResolvedValue({ ok: false, message: 'Unknown roles: ghost', current: true })
    renderDrawer({ onSaveRoles })
    const dialog = screen.getByRole('dialog')

    // Add a second role so the form is dirty and Save enables. The placeholder is
    // gone once a chip is present, so reach the field by its combobox role.
    fireEvent.click(within(dialog).getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'admin' }))

    const save = screen.getByRole('button', { name: 'Save roles' })
    await waitFor(() => expect(save).toBeEnabled())
    fireEvent.click(save)

    await waitFor(() => expect(onSaveRoles).toHaveBeenCalledTimes(1))
    const rolesArg = onSaveRoles.mock.calls[0]![1] as string[]
    expect([...rolesArg].sort()).toEqual(['admin', 'member'])
    // Input is preserved on failure: both chips remain selected (each chip carries
    // a labelled remove button).
    const dialogAfter = screen.getByRole('dialog')
    expect(within(dialogAfter).getByRole('button', { name: /remove admin/i })).toBeInTheDocument()
    expect(within(dialogAfter).getByRole('button', { name: /remove member/i })).toBeInTheDocument()
  })

  it('links out to the user’s MCP tokens route instead of embedding a second table', () => {
    renderDrawer()

    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'MCP tokens' }))
    expect(nav.push).toHaveBeenCalledWith(`/admin/users/${member.id}/tokens`)

    // One primary action in the drawer, still: the link is an outline button.
    const primaries = Array.from(dialog.querySelectorAll('button')).filter((button) =>
      button.classList.contains('bg-action-primary'),
    )
    expect(primaries.map((button) => button.textContent)).toEqual(['Save roles'])
  })
})
