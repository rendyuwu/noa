import type { CommandSearchGroup, CommandSearchItem } from '@gio/bigsu-app-shell'
import type { BigsuIconName } from '@gio/bigsu-icons'

import { NAV_ITEMS } from '@/lib/nav'

// CommandSearch source (issue #101). The palette lists ONLY permitted pages and
// actions: nav entries are filtered by the verified `/auth/me` roles the same
// way the Sidebar filters them, so a hidden page never appears here. It
// deliberately does NOT search conversation threads or business records (issue
// non-goal) — those get their own scoped search in later work. Role filtering is
// presentation only; FastAPI RBAC stays authoritative.

export type CommandHandlers = {
  /** Client navigate to a permitted page (also closes the palette). */
  navigate: (href: string) => void
  /** Sign the user out through the shared session flow. */
  onSignOut: () => void
}

// Mirror the Sidebar's role gate: no `roles` means everyone; otherwise the
// verified roles must intersect. Empty/missing required list shows to everyone.
const rolesAllow = (userRoles: string[], required?: string[]): boolean => {
  if (required === undefined || required.length === 0) return true
  return required.some((role) => userRoles.includes(role))
}

type PageCommand = {
  id: string
  label: string
  icon?: BigsuIconName
  keywords?: string[]
  href: string
}

// Flatten the role-visible nav (two levels max) into page commands.
export const permittedPageCommands = (userRoles: string[]): PageCommand[] => {
  const commands: PageCommand[] = []
  for (const item of NAV_ITEMS) {
    if (!rolesAllow(userRoles, item.roles)) continue
    commands.push({
      id: `page:${item.href}`,
      label: item.label,
      icon: item.icon,
      keywords: item.section ? [item.section] : undefined,
      href: item.href,
    })
    for (const child of item.children ?? []) {
      if (!rolesAllow(userRoles, child.roles)) continue
      commands.push({
        id: `page:${child.href}`,
        label: `${item.label} · ${child.label}`,
        icon: item.icon,
        href: child.href,
      })
    }
  }
  return commands
}

export const buildCommandGroups = (
  userRoles: string[],
  handlers: CommandHandlers,
): CommandSearchGroup[] => {
  const groups: CommandSearchGroup[] = []

  const pages = permittedPageCommands(userRoles)
  if (pages.length > 0) {
    const items: CommandSearchItem[] = pages.map((page) => ({
      id: page.id,
      label: page.label,
      icon: page.icon,
      keywords: page.keywords,
      onSelect: () => handlers.navigate(page.href),
    }))
    groups.push({ heading: 'Pages', items })
  }

  groups.push({
    heading: 'Actions',
    items: [
      {
        id: 'action:sign-out',
        label: 'Sign out',
        keywords: ['log out', 'logout'],
        onSelect: handlers.onSignOut,
      },
    ],
  })

  return groups
}
