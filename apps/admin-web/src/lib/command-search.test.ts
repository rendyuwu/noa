import { describe, expect, it, vi } from 'vitest'

import { buildCommandGroups, permittedPageCommands } from '@/lib/command-search'

describe('command-search source', () => {
  it('lists role-gated pages only when permitted', () => {
    // Every page in this app is administration (§I.admin-web), so a non-admin
    // sees an empty palette rather than a shorter one. The old repo's ungated
    // chat entry is gone with the surface it pointed at.
    expect(permittedPageCommands(['user'])).toEqual([])

    const asAdmin = permittedPageCommands(['admin']).map((c) => c.label)
    expect(asAdmin).toContain('Administration')
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
