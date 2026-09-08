import type { NavItem } from '@gio/bigsu-app-shell'

// Application navigation. Role gating is presentation only: entries carrying
// `roles` render when the verified `/auth/me` roles intersect (via the Sidebar's
// `userRoles`). FastAPI RBAC stays the authorization source of truth — hiding a
// nav item never grants or denies access.
//
// The old repo's first entry was the chat assistant; that surface left NOA, so
// administration was once the whole of this app (§I.admin-web) and a non-admin
// saw an empty sidebar. That is no longer true: §T76 added `/me/tokens`, where
// any verified operator mints the MCP token LibreChat asks them to paste into
// `customUserVars`. It carries no `roles` — the API reads the operator's id off
// their session, so there is nothing here to gate — and it is this deployable's
// only non-admin surface.
export const NAV_ITEMS: NavItem[] = [
  {
    label: 'Administration',
    href: '/admin/users',
    icon: 'settings',
    roles: ['admin'],
    section: 'Admin',
    children: [
      { label: 'Users', href: '/admin/users', roles: ['admin'] },
      { label: 'Roles', href: '/admin/roles', roles: ['admin'] },
      { label: 'WHM servers', href: '/admin/whm', roles: ['admin'] },
      { label: 'Proxmox servers', href: '/admin/proxmox', roles: ['admin'] },
      { label: 'PMG servers', href: '/admin/pmg', roles: ['admin'] },
      { label: 'Audit', href: '/admin/audit', roles: ['admin'] },
    ],
  },
  // Last so an admin's sidebar still opens on their work. For everyone else this
  // is the entire sidebar.
  {
    label: 'My MCP tokens',
    href: '/me/tokens',
    icon: 'security',
    section: 'Account',
  },
]
