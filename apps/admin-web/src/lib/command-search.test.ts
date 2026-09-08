import { describe, expect, it, vi } from 'vitest'

import { buildCommandGroups, permittedPageCommands } from '@/lib/command-search'

describe('command-search source', () => {
  it('lists role-gated pages only when permitted', () => {
    // Administration was once the whole of this app; §T76 added `/me/tokens`,
    // which any verified operator may use. So a non-admin's palette is now the
    // ungated entry and nothing else — shorter, not empty.
    const asUser = permittedPageCommands(['user']).map((c) => c.href)
    expect(asUser).toEqual(['/me/tokens'])

    const asAdmin = permittedPageCommands(['admin']).map((c) => c.label)
    expect(asAdmin).toContain('Administration')
  })

  // The entry carries no `roles`, so it survives the filter for a user with
  // no roles at all, while every `/admin/…` entry is still dropped. Both halves
  // matter: an entry visible to everyone is only correct if the gate still works.
  it('shows the MCP tokens page to a user with no roles, and no admin page with it', () => {
    const commands = permittedPageCommands([])

    expect(commands.map((c) => c.href)).toEqual(['/me/tokens'])
    expect(commands[0]?.label).toBe('My MCP tokens')
    expect(commands.some((c) => c.href.startsWith('/admin/'))).toBe(false)
  })

  it('flattens the gated children too, so a deep page is reachable', () => {
    // Separates from the assertion above: a filter that dropped children would
    // still leave 'Administration' in the list and read as a pass.
    const asAdmin = permittedPageCommands(['admin']).map((c) => c.label)

    expect(asAdmin).toContain('Administration · Users')
    expect(asAdmin).toContain('Administration · Audit')
  })

  it('navigates to the page href when a page command is selected', () => {
    const navigate = vi.fn()
    const groups = buildCommandGroups(['admin'], { navigate, onSignOut: vi.fn() })
    const pages = groups.find((g) => g.heading === 'Pages')
    pages?.items.find((i) => i.label === 'Administration')?.onSelect()
    // `/admin` has no page of its own — the parent entry points at the first
    // vertical, so selecting it cannot land on a 404.
    expect(navigate).toHaveBeenCalledWith('/admin/users')
  })

  it('always exposes a Sign out action wired to the sign-out handler', () => {
    const onSignOut = vi.fn()
    const groups = buildCommandGroups([], { navigate: vi.fn(), onSignOut })
    const actions = groups.find((g) => g.heading === 'Actions')
    const signOut = actions?.items.find((i) => i.id === 'action:sign-out')
    expect(signOut).toBeDefined()
    signOut?.onSelect()
    expect(onSignOut).toHaveBeenCalledOnce()
  })

  it('never emits thread or record search entries (issue non-goal)', () => {
    const groups = buildCommandGroups(['admin', 'user'], { navigate: vi.fn(), onSignOut: vi.fn() })
    const headings = groups.map((g) => g.heading)
    expect(headings).toEqual(['Pages', 'Actions'])
    const ids = groups.flatMap((g) => g.items.map((i) => i.id))
    // Only page: and action: entries — no thread/record ids.
    expect(ids.every((id) => id.startsWith('page:') || id.startsWith('action:'))).toBe(true)
  })
})
