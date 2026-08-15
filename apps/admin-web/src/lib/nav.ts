import type { NavItem } from '@gio/bigsu-app-shell'

// Application navigation. Role gating is presentation only: entries carrying
// `roles` render when the verified `/auth/me` roles intersect (via the Sidebar's
// `userRoles`). FastAPI RBAC stays the authorization source of truth — hiding a
// nav item never grants or denies access.
//
// The old repo's first entry was the chat assistant; that surface left NOA, so
// administration is the whole of this app (§I.admin-web). A non-admin therefore
// sees an empty sidebar here, which is correct: there is nothing in this
// deployable for them.
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
]
