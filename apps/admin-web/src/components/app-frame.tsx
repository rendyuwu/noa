'use client'

import { useCallback, useMemo, useState, type ReactNode } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { AppShell, CommandSearch, useCommandSearchShortcut } from '@gio/bigsu-app-shell'
import type { SidebarLinkProps } from '@gio/bigsu-app-shell'

import { clearAuth } from '@/lib/auth/session'
import type { VerifiedUser } from '@/lib/auth/use-verified-auth'
import { buildCommandGroups } from '@/lib/command-search'
import { NAV_ITEMS } from '@/lib/nav'

// AppShell chrome for the protected tree (issue #101). Mounted once by the
// protected layout: the Sidebar filters role-gated nav by the verified
// `/auth/me` roles (hiding a page never grants or denies access — FastAPI RBAC
// stays authoritative), the UserMenu identity comes from that same verified
// user, and Ctrl/Cmd+K opens a CommandSearch limited to permitted pages and
// actions. AppShell owns and persists its own collapse state, so navigation
// between pages never resets the chrome. Sign-out routes through the shared
// clearAuth flow so a logout on either frontend logs the user out of both.

// Next.js Link adapter so Sidebar navigation is client-side (no full reload).
function SidebarLink({ href, children, ...rest }: SidebarLinkProps) {
  return (
    <Link href={href} {...rest}>
      {children}
    </Link>
  )
}

export function AppFrame({
  user,
  children,
}: {
  user: VerifiedUser
  children: ReactNode
}) {
  const router = useRouter()
  const pathname = usePathname()
  const [searchOpen, setSearchOpen] = useState(false)
  const roles = user.roles

  useCommandSearchShortcut(() => setSearchOpen((open) => !open))

  const signOut = useCallback(() => clearAuth('logged_out'), [])

  const commandGroups = useMemo(
    () =>
      buildCommandGroups(roles, {
        navigate: (href) => {
          setSearchOpen(false)
          router.push(href)
        },
        onSignOut: signOut,
      }),
    [roles, router, signOut],
  )

  return (
    <>
      <AppShell
        sidebar={{
          items: NAV_ITEMS,
          activeHref: pathname,
          userRoles: roles,
          linkComponent: SidebarLink,
          homeHref: '/admin/users',
        }}
        topBar={{
          appName: 'NOA',
          onSearchOpen: () => setSearchOpen(true),
          user: {
            name: user.display_name ?? user.email,
            email: user.email,
            roles,
          },
          onSignOut: signOut,
        }}
      >
        {children}
      </AppShell>
      <CommandSearch
        groups={commandGroups}
        open={searchOpen}
        onOpenChange={setSearchOpen}
        placeholder="Search pages and actions…"
      />
    </>
  )
}
