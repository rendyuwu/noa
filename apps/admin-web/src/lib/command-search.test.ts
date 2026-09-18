import { describe, expect, it, vi } from 'vitest'

import { buildCommandGroups } from '@/lib/command-search'

// The role gate is asserted through the palette it builds, which is the only
// exported surface. Labels come off the Pages group; hrefs come off what selecting
// each entry actually navigates to, rather than off the command id that encodes it.
const pageLabels = (roles: string[]): string[] =>
  (
    buildCommandGroups(roles, { navigate: vi.fn(), onSignOut: vi.fn() }).find(
      (g) => g.heading === 'Pages',
    )?.items ?? []
  ).map((i) => i.label)

const pageHrefs = (roles: string[]): string[] => {
  const navigate = vi.fn()
  const groups = buildCommandGroups(roles, { navigate, onSignOut: vi.fn() })
  const items = groups.find((g) => g.heading === 'Pages')?.items ?? []
  items.forEach((item) => item.onSelect())
  return navigate.mock.calls.map(([href]) => href as string)
}

describe('command-search source', () => {
  it('lists role-gated pages only when permitted', () => {
    // Administration was once the whole of this app; the MCP token UI added `/me/tokens`,
    // which any verified operator may use. So a non-admin's palette is now the
    // ungated entry and nothing else — shorter, not empty.
    expect(pageHrefs(['user'])).toEqual(['/me/tokens'])
    expect(pageLabels(['admin'])).toContain('Administration')
  })

  // The entry carries no `roles`, so it survives the filter for a user with
  // no roles at all, while every `/admin/…` entry is still dropped. Both halves
  // matter: an entry visible to everyone is only correct if the gate still works.
  it('shows the MCP tokens page to a user with no roles, and no admin page with it', () => {
    const hrefs = pageHrefs([])

    expect(hrefs).toEqual(['/me/tokens'])
    expect(pageLabels([])[0]).toBe('My MCP tokens')
    expect(hrefs.some((href) => href.startsWith('/admin/'))).toBe(false)
  })

  it('flattens the gated children too, so a deep page is reachable', () => {
    // Separates from the assertion above: a filter that dropped children would
    // still leave 'Administration' in the list and read as a pass.
    const asAdmin = pageLabels(['admin'])

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
