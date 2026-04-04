# Split Shell & Chat UI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate NOA into two independent shells — a clean, chat-first assistant surface (like Claude/ChatGPT) and a dedicated admin dashboard — then redesign the assistant UI for production-grade UX.

**Architecture:** The current single `AppShell` + `ProtectedScreen` combo renders the same sidebar (with admin links) for every authenticated route. We replace it with two route-group-specific layouts: a `ChatShell` (sidebar = thread list only) wrapping `(chat)/**` routes and an `AdminShell` (sidebar = admin nav only) wrapping `(admin)/**` routes. The assistant workspace then gets a full layout rethink — removing the in-main-content thread list, centering the composer, and flattening the conversation view.

**Tech Stack:** Next.js 15 (App Router), React, Tailwind CSS, assistant-ui, Lucide icons, shadcn/ui primitives.

---

## Phase 1: Shell Separation (Admin Migration)

### Task 1: Create chat-only nav config

**Files:**
- Create: `apps/noa/components/layout/chat-nav-items.ts`

- [ ] **Step 1: Create the chat nav items file**

```ts
// apps/noa/components/layout/chat-nav-items.ts
import type { LucideIcon } from "lucide-react";
import { Settings } from "lucide-react";

export type ChatNavAction = {
  label: string;
  icon: LucideIcon;
  href: string;
};

/**
 * Admin link shown in user-menu for admin users only.
 */
export const adminNavAction: ChatNavAction = {
  label: "Admin panel",
  icon: Settings,
  href: "/admin",
};
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/layout/chat-nav-items.ts
git commit -m "feat(layout): add chat-only nav config with admin link for user menu"
```

---

### Task 2: Create admin-only nav config

**Files:**
- Create: `apps/noa/components/layout/admin-nav-items.ts`

- [ ] **Step 1: Create the admin nav items file**

```ts
// apps/noa/components/layout/admin-nav-items.ts
import type { LucideIcon } from "lucide-react";
import {
  Bot,
  ClipboardList,
  Database,
  HardDrive,
  Shield,
  Users,
} from "lucide-react";

export type AdminNavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
};

export const adminNavItems: AdminNavItem[] = [
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/roles", label: "Roles", icon: Shield },
  { href: "/admin/audit", label: "Audit", icon: ClipboardList },
  { href: "/admin/whm/servers", label: "WHM", icon: HardDrive },
  { href: "/admin/proxmox/servers", label: "Proxmox", icon: Database },
];

export const backToChatAction = {
  label: "Back to chat",
  icon: Bot,
  href: "/assistant",
};
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/layout/admin-nav-items.ts
git commit -m "feat(layout): add admin-only nav config with back-to-chat link"
```

---

### Task 3: Create ChatShell component

**Files:**
- Create: `apps/noa/components/layout/chat-shell.tsx`

This is the Claude/ChatGPT-style shell: a narrow sidebar containing only the thread list, new-thread button, user avatar, and (for admins) an admin link. No page header. The main area is a clean full-height canvas.

- [ ] **Step 1: Create ChatShell**

```tsx
// apps/noa/components/layout/chat-shell.tsx
"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen, Settings, SquarePen, X } from "lucide-react";
import { ThreadListPrimitive, ThreadListItemPrimitive, useAssistantState } from "@assistant-ui/react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { clearAuth } from "@/components/lib/auth/auth-storage";
import type { AuthUser } from "@/components/lib/auth/types";
import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";

const COLLAPSED_KEY = "noa.chat-shell.collapsed";

type ChatShellProps = {
  children: ReactNode;
  user: AuthUser | null;
};

function ChatThreadListItem() {
  const activeRemoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId ?? null);
  const title = useAssistantState(({ threadListItem }) => threadListItem.title ?? null);
  const isActive = remoteId !== null && activeRemoteId === remoteId;

  return (
    <ThreadListItemPrimitive.Root className="mb-0.5">
      <ThreadListItemPrimitive.Trigger
        className={[
          "block w-full truncate rounded-lg px-3 py-2 text-left font-ui text-sm transition",
          isActive
            ? "bg-surface-2 font-medium text-text"
            : "text-muted hover:bg-surface-2/60 hover:text-text",
        ].join(" ")}
      >
        {title && title.trim() ? title : "Untitled thread"}
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  );
}

export function ChatShell({ children, user }: ChatShellProps) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const isAdmin = user?.roles?.includes("admin") ?? false;

  useEffect(() => {
    const saved = window.localStorage.getItem(COLLAPSED_KEY);
    setCollapsed(saved === "true");
  }, []);

  useEffect(() => {
    window.localStorage.setItem(COLLAPSED_KEY, String(collapsed));
  }, [collapsed]);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const Sidebar = (
    <aside className="flex h-full flex-col bg-surface text-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3">
        {!collapsed && (
          <span className="text-base font-semibold tracking-tight text-text">NOA</span>
        )}
        <div className="flex items-center gap-1">
          <ThreadListPrimitive.New asChild>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 rounded-lg text-muted hover:bg-surface-2 hover:text-text"
                  aria-label="New chat"
                >
                  <SquarePen className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side={collapsed ? "right" : "bottom"}>New chat</TooltipContent>
            </Tooltip>
          </ThreadListPrimitive.New>
          <Button
            variant="ghost"
            size="icon"
            className="hidden size-8 rounded-lg text-muted hover:bg-surface-2 hover:text-text md:inline-flex"
            onClick={() => setCollapsed((v) => !v)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 rounded-lg text-muted hover:bg-surface-2 hover:text-text md:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Close sidebar"
          >
            <X className="size-4" />
          </Button>
        </div>
      </div>

      {/* Thread list */}
      {!collapsed && (
        <ScrollArea className="flex-1 px-2">
          <ThreadListPrimitive.Root>
            <ThreadListPrimitive.Items components={{ ThreadListItem: ChatThreadListItem }} />
          </ThreadListPrimitive.Root>
        </ScrollArea>
      )}

      {/* Footer: user + actions */}
      <div className="border-t border-border/60 px-2 py-2">
        {isAdmin && !collapsed && (
          <Link
            href="/admin"
            className="flex items-center gap-2.5 rounded-lg px-3 py-2 font-ui text-sm text-muted transition hover:bg-surface-2 hover:text-text"
          >
            <Settings className="size-4" />
            Admin
          </Link>
        )}
        <button
          type="button"
          onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
          className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 font-ui text-sm text-muted transition hover:bg-surface-2 hover:text-text"
        >
          <LogOut className="size-4" />
          {!collapsed && "Sign out"}
        </button>
        {!collapsed && user && (
          <p className="mt-1 truncate px-3 font-ui text-xs text-muted/70">
            {user.display_name || user.email}
          </p>
        )}
      </div>
    </aside>
  );

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex min-h-dvh bg-bg text-text">
        {/* Desktop sidebar */}
        <div className={collapsed ? "hidden md:block md:w-[68px]" : "hidden md:block md:w-[260px]"}>
          {Sidebar}
        </div>

        {/* Mobile sidebar overlay */}
        {mobileOpen && (
          <div className="fixed inset-0 z-40 flex md:hidden">
            <div className="w-[260px] max-w-[85vw]">{Sidebar}</div>
            <button
              type="button"
              className="flex-1 bg-overlay/40"
              aria-label="Dismiss sidebar"
              onClick={() => setMobileOpen(false)}
            />
          </div>
        )}

        {/* Main canvas */}
        <div className="flex min-h-dvh flex-1 flex-col">
          {/* Mobile header */}
          <div className="flex items-center gap-2 px-3 py-2 md:hidden">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-lg text-muted hover:bg-surface-2"
              onClick={() => setMobileOpen(true)}
              aria-label="Open sidebar"
            >
              <Menu className="size-4" />
            </Button>
            <span className="text-sm font-semibold text-text">NOA</span>
            <ThemeToggle className="ml-auto" />
          </div>

          {/* Content */}
          <main className="flex flex-1 flex-col">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/layout/chat-shell.tsx
git commit -m "feat(layout): create ChatShell with thread-list sidebar, no admin nav"
```

---

### Task 4: Create AdminShell component

**Files:**
- Create: `apps/noa/components/layout/admin-shell.tsx`

This is a clean admin layout with vertical nav, page header, and a "Back to chat" link. No thread list.

- [ ] **Step 1: Create AdminShell**

```tsx
// apps/noa/components/layout/admin-shell.tsx
"use client";

import { type ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { clearAuth } from "@/components/lib/auth/auth-storage";
import type { AuthUser } from "@/components/lib/auth/types";

import { adminNavItems, backToChatAction } from "./admin-nav-items";

const COLLAPSED_KEY = "noa.admin-shell.collapsed";

type AdminShellProps = {
  children: ReactNode;
  title: string;
  description: string;
  user: AuthUser | null;
};

export function AdminShell({ children, title, description, user }: AdminShellProps) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const saved = window.localStorage.getItem(COLLAPSED_KEY);
    setCollapsed(saved === "true");
  }, []);

  useEffect(() => {
    window.localStorage.setItem(COLLAPSED_KEY, String(collapsed));
  }, [collapsed]);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const NavLink = ({ href, label, icon: Icon, active }: { href: string; label: string; icon: React.ComponentType<{ className?: string }>; active: boolean }) => {
    const className = [
      "flex items-center gap-3 rounded-xl px-3 py-2.5 font-ui text-sm transition",
      active ? "bg-accent text-accent-foreground" : "text-muted hover:bg-surface-2 hover:text-text",
    ].join(" ");

    const content = (
      <>
        <Icon className="size-4 shrink-0" />
        {!collapsed && <span className="truncate">{label}</span>}
      </>
    );

    return collapsed ? (
      <Tooltip>
        <TooltipTrigger asChild>
          <Link href={href} className={className}>{content}</Link>
        </TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    ) : (
      <Link href={href} className={className}>{content}</Link>
    );
  };

  const Sidebar = (
    <aside className="flex h-full flex-col gap-5 border-r border-border/80 bg-surface px-3 py-4 text-sm shadow-soft">
      <div className="flex items-center justify-between gap-3 px-2">
        {!collapsed && (
          <div className="min-w-0">
            <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">NOA</p>
            <p className="truncate text-lg font-semibold text-text">Admin</p>
          </div>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="hidden rounded-lg border border-border bg-bg/70 text-muted hover:bg-surface-2 md:inline-flex"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="rounded-lg border border-border bg-bg/70 text-muted hover:bg-surface-2 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label="Close navigation"
        >
          <X className="size-4" />
        </Button>
      </div>

      <nav className="flex flex-1 flex-col gap-1">
        <NavLink
          href={backToChatAction.href}
          label={backToChatAction.label}
          icon={backToChatAction.icon}
          active={false}
        />
        <div className="my-2 border-t border-border/60" />
        {adminNavItems.map((item) => (
          <NavLink
            key={item.href}
            href={item.href}
            label={item.label}
            icon={item.icon}
            active={pathname === item.href || pathname.startsWith(`${item.href}/`)}
          />
        ))}
      </nav>

      <div className="space-y-1">
        <button
          type="button"
          onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 font-ui text-sm text-muted transition hover:bg-surface-2 hover:text-text"
        >
          <LogOut className="size-4 shrink-0" />
          {!collapsed && <span>Sign out</span>}
        </button>
        {!collapsed && user && (
          <div className="rounded-xl border border-border/70 bg-bg/70 px-3 py-3 font-ui text-xs text-muted">
            <p className="font-medium text-text">Signed in as</p>
            <p className="mt-1 truncate">{user.display_name || user.email || "Unknown user"}</p>
          </div>
        )}
      </div>
    </aside>
  );

  return (
    <TooltipProvider delayDuration={200}>
      <div className="min-h-dvh bg-bg text-text">
        <div className="flex min-h-dvh">
          <div className={collapsed ? "hidden md:block md:w-[88px]" : "hidden md:block md:w-[288px]"}>
            {Sidebar}
          </div>

          {mobileOpen && (
            <div className="fixed inset-0 z-40 flex md:hidden">
              <div className="w-[290px] max-w-[85vw]">{Sidebar}</div>
              <button
                type="button"
                className="flex-1 bg-overlay/40"
                aria-label="Dismiss navigation overlay"
                onClick={() => setMobileOpen(false)}
              />
            </div>
          )}

          <div className="flex min-h-dvh flex-1 flex-col">
            <header className="sticky top-0 z-20 border-b border-border/70 bg-bg/90 px-4 py-3 backdrop-blur sm:px-6">
              <div className="flex items-start gap-3">
                <Button
                  variant="ghost"
                  size="icon"
                  className="rounded-lg border border-border bg-surface text-muted hover:bg-surface-2 md:hidden"
                  onClick={() => setMobileOpen(true)}
                  aria-label="Open navigation"
                >
                  <Menu className="size-4" />
                </Button>
                <div className="min-w-0 flex-1">
                  <h1 className="text-xl font-semibold tracking-[-0.02em] sm:text-2xl">{title}</h1>
                  <p className="mt-1 max-w-3xl font-ui text-sm text-muted">{description}</p>
                </div>
                <ThemeToggle className="shrink-0" />
              </div>
            </header>
            <main className="flex-1 px-4 py-4 sm:px-6 sm:py-6">{children}</main>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/layout/admin-shell.tsx
git commit -m "feat(layout): create AdminShell with admin-only nav and back-to-chat link"
```

---

### Task 5: Create AdminProtectedScreen wrapper

**Files:**
- Create: `apps/noa/components/layout/admin-protected-screen.tsx`

- [ ] **Step 1: Create AdminProtectedScreen**

This is a slimmed-down version of `ProtectedScreen` that uses `AdminShell` instead of `AppShell` and always requires admin.

```tsx
// apps/noa/components/layout/admin-protected-screen.tsx
"use client";

import type { ReactNode } from "react";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { clearAuth } from "@/components/lib/auth/auth-storage";
import { useRequireAuth } from "@/components/lib/auth/use-require-auth";
import { Button } from "@/components/ui/button";

import { AdminShell } from "./admin-shell";

type AdminProtectedScreenProps = {
  children: ReactNode;
  title: string;
  description: string;
};

export function AdminProtectedScreen({ children, title, description }: AdminProtectedScreenProps) {
  const router = useRouter();
  const { error, ready, refresh, user, validating } = useRequireAuth();
  const isAdmin = user?.roles?.includes("admin") ?? false;

  useEffect(() => {
    if (ready && !isAdmin) {
      router.replace("/assistant");
    }
  }, [isAdmin, ready, router]);

  if (error) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="w-full max-w-md rounded-2xl border border-border bg-surface px-5 py-5 shadow-soft">
          <h1 className="text-lg font-semibold text-text">Session validation failed</h1>
          <p className="mt-2 font-ui text-sm text-muted">{error}</p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <Button className="rounded-xl font-ui text-sm font-semibold" onClick={() => void refresh()}>
              Retry validation
            </Button>
            <Button
              variant="outline"
              className="rounded-xl font-ui text-sm font-medium"
              onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
            >
              Sign out
            </Button>
          </div>
        </div>
      </main>
    );
  }

  if (!ready || validating || !isAdmin) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="rounded-2xl border border-border bg-surface px-5 py-4 text-sm text-muted shadow-soft">
          Loading admin panel…
        </div>
      </main>
    );
  }

  return (
    <AdminShell title={title} description={description} user={user}>
      {children}
    </AdminShell>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/layout/admin-protected-screen.tsx
git commit -m "feat(layout): create AdminProtectedScreen using AdminShell"
```

---

### Task 6: Create ChatProtectedScreen wrapper

**Files:**
- Create: `apps/noa/components/layout/chat-protected-screen.tsx`

- [ ] **Step 1: Create ChatProtectedScreen**

```tsx
// apps/noa/components/layout/chat-protected-screen.tsx
"use client";

import type { ReactNode } from "react";

import { clearAuth } from "@/components/lib/auth/auth-storage";
import { useRequireAuth } from "@/components/lib/auth/use-require-auth";
import { Button } from "@/components/ui/button";

import { ChatShell } from "./chat-shell";

type ChatProtectedScreenProps = {
  children: ReactNode;
};

export function ChatProtectedScreen({ children }: ChatProtectedScreenProps) {
  const { error, ready, refresh, user, validating } = useRequireAuth();

  if (error) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="w-full max-w-md rounded-2xl border border-border bg-surface px-5 py-5 shadow-soft">
          <h1 className="text-lg font-semibold text-text">Session validation failed</h1>
          <p className="mt-2 font-ui text-sm text-muted">{error}</p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <Button className="rounded-xl font-ui text-sm font-semibold" onClick={() => void refresh()}>
              Retry validation
            </Button>
            <Button
              variant="outline"
              className="rounded-xl font-ui text-sm font-medium"
              onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
            >
              Sign out
            </Button>
          </div>
        </div>
      </main>
    );
  }

  if (!ready || validating) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="rounded-2xl border border-border bg-surface px-5 py-4 text-sm text-muted shadow-soft">
          Loading…
        </div>
      </main>
    );
  }

  return <ChatShell user={user}>{children}</ChatShell>;
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/layout/chat-protected-screen.tsx
git commit -m "feat(layout): create ChatProtectedScreen using ChatShell"
```

---

### Task 7: Rewire assistant layout to use ChatProtectedScreen

**Files:**
- Modify: `apps/noa/app/(app)/assistant/layout.tsx`

- [ ] **Step 1: Update assistant layout**

Replace the current `ProtectedScreen` wrapper with `ChatProtectedScreen`. Remove `title` and `description` props — the chat shell doesn't show a page header.

```tsx
// apps/noa/app/(app)/assistant/layout.tsx
import type { ReactNode } from "react";

import { ChatProtectedScreen } from "@/components/layout/chat-protected-screen";
import { requireServerUser } from "@/components/lib/auth/server-session";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime/runtime-provider";

export default async function AssistantLayout({ children }: { children: ReactNode }) {
  await requireServerUser("/assistant");

  return (
    <NoaAssistantRuntimeProvider>
      <ChatProtectedScreen>{children}</ChatProtectedScreen>
    </NoaAssistantRuntimeProvider>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `npm run build` from `apps/noa`
Expected: Build succeeds with no type errors.

- [ ] **Step 3: Commit**

```bash
git add apps/noa/app/\(app\)/assistant/layout.tsx
git commit -m "refactor(assistant): use ChatProtectedScreen, remove page header"
```

---

### Task 8: Rewire all admin pages to use AdminProtectedScreen

**Files:**
- Modify: `apps/noa/app/(admin)/admin/users/page.tsx`
- Modify: `apps/noa/app/(admin)/admin/roles/page.tsx`
- Modify: `apps/noa/app/(admin)/admin/audit/page.tsx`
- Modify: `apps/noa/app/(admin)/admin/audit/receipts/[actionRequestId]/page.tsx`
- Modify: `apps/noa/app/(admin)/admin/whm/servers/page.tsx`
- Modify: `apps/noa/app/(admin)/admin/proxmox/servers/page.tsx`

- [ ] **Step 1: Update all admin page files**

Replace `ProtectedScreen` import with `AdminProtectedScreen` in every admin page. Each page follows this pattern (shown for users; repeat for all six pages):

```tsx
// apps/noa/app/(admin)/admin/users/page.tsx
import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function UsersPage() {
  return (
    <AdminProtectedScreen
      title="Users"
      description="Unified admin shell target for user management parity."
    >
      <UsersAdminPage />
    </AdminProtectedScreen>
  );
}
```

Apply the same pattern to:
- `roles/page.tsx` — title: "Roles", description: "Shared shell route for role and allowlist management parity."
- `audit/page.tsx` — title: "Audit", description: "Review action request history, filter events, and open receipt details."
- `audit/receipts/[actionRequestId]/page.tsx` — title: "Audit receipt", description: "Inspect detailed evidence for an audited workflow action."
- `whm/servers/page.tsx` — title: "WHM servers", description: "Create, update, validate, and remove WHM server connection profiles."
- `proxmox/servers/page.tsx` — title: "Proxmox servers", description: "Create, update, validate, and remove Proxmox server connection profiles."

- [ ] **Step 2: Verify build**

Run: `npm run build` from `apps/noa`
Expected: Build succeeds with no type errors.

- [ ] **Step 3: Commit**

```bash
git add apps/noa/app/\(admin\)/
git commit -m "refactor(admin): use AdminProtectedScreen on all admin pages"
```

---

## Phase 2: Chat UI Redesign

### Task 9: Redesign AssistantWorkspace — remove in-content thread list, center composer

**Files:**
- Modify: `apps/noa/components/assistant/assistant-workspace.tsx`

The thread list now lives in `ChatShell` sidebar. The main workspace should only render the conversation panel (full-width) and approval/workflow docks.

- [ ] **Step 1: Rewrite AssistantWorkspace**

```tsx
// apps/noa/components/assistant/assistant-workspace.tsx
"use client";

import { useMemo } from "react";
import { useAssistantState } from "@assistant-ui/react";

import { extractLatestCanonicalActionRequests } from "./approval-state";
import { ApprovalDock } from "./approval-dock";
import { RequestApprovalToolUI } from "./assistant-tool-ui";
import { RouteThreadSync } from "./assistant-route-thread-sync";
import { ThreadPanel } from "./assistant-thread-panel";
import { WorkflowDock } from "./workflow-dock";
import {
  extractLatestCanonicalWorkflowTodos,
  extractLatestWorkflowTodos,
  WorkflowTodoToolUI,
} from "./workflow-todo-tool-ui";
import { WorkflowReceiptToolUI } from "./workflow-receipt-tool-ui";

function AssistantLiveDocks() {
  const threadMessages = useAssistantState((state) => state.thread?.messages);
  const isRunning = useAssistantState((state) => Boolean(state.thread?.isRunning));

  const actionRequests = useMemo(
    () => extractLatestCanonicalActionRequests(threadMessages) ?? [],
    [threadMessages],
  );
  const workflowTodos = useMemo(() => {
    const canonical = extractLatestCanonicalWorkflowTodos(threadMessages);
    if (canonical) {
      return canonical;
    }

    return extractLatestWorkflowTodos(threadMessages);
  }, [threadMessages]);

  if (actionRequests.length === 0 && workflowTodos.length === 0) {
    return null;
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-3 px-4">
      <ApprovalDock requests={actionRequests} />
      <WorkflowDock todos={workflowTodos} isRunning={isRunning} />
    </div>
  );
}

export function AssistantWorkspace({ threadId }: { threadId?: string | null }) {
  return (
    <div className="flex flex-1 flex-col">
      <RequestApprovalToolUI />
      <WorkflowTodoToolUI />
      <WorkflowReceiptToolUI />
      <RouteThreadSync routeThreadId={threadId} />
      <AssistantLiveDocks />
      <ThreadPanel />
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/assistant/assistant-workspace.tsx
git commit -m "refactor(assistant): remove in-content thread list, full-width thread panel"
```

---

### Task 10: Redesign ThreadPanel — clean, centered, Claude/ChatGPT-style

**Files:**
- Modify: `apps/noa/components/assistant/assistant-thread-panel.tsx`

Key changes:
- Remove the bordered card wrapper — conversation fills the canvas
- Remove the "Assistant workspace" header bar
- Center the empty-state greeting + composer
- Make the composer full-width at the bottom
- Hide Stop button unless a run is active
- Remove dashed borders everywhere

- [ ] **Step 1: Rewrite ThreadPanel**

```tsx
// apps/noa/components/assistant/assistant-thread-panel.tsx
"use client";

import { ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useAssistantState } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { AlertTriangle, ArrowUp, LoaderCircle, RefreshCw, Square } from "lucide-react";
import remarkGfm from "remark-gfm";

import { useThreadHydration } from "@/components/lib/runtime/thread-hydration";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { ToolFallback, ToolGroup } from "./assistant-tool-ui";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mb-4 flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-accent px-4 py-3 text-sm text-accent-foreground shadow-sm">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function MarkdownText() {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      className="aui-md-root text-text [&_a]:text-accent [&_code]:rounded [&_code]:bg-bg [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-sm [&_h1]:mb-2 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-1.5 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:my-1 [&_ol]:list-decimal [&_p]:my-1 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-border [&_pre]:bg-bg [&_pre]:p-3 [&_pre_code]:bg-bg [&_pre_code]:p-0 [&_ul]:my-1 [&_ul]:list-disc"
    />
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="mb-4">
      <div className="max-w-[92%] text-sm text-text">
        <MessagePrimitive.Parts
          components={{
            Text: MarkdownText,
            ToolGroup,
            tools: { Fallback: ToolFallback },
          }}
        />
      </div>
    </MessagePrimitive.Root>
  );
}

export function ThreadPanel() {
  const { errorMessage, isHydrating, retry } = useThreadHydration();
  const isRunning = useAssistantState(({ thread }) => Boolean(thread?.isRunning));

  return (
    <ThreadPrimitive.Root className="flex flex-1 flex-col">
      {/* Scrollable message area */}
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6">
          {isHydrating && (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
              <LoaderCircle className="size-4 animate-spin" />
              Restoring conversation…
            </div>
          )}

          {errorMessage && !isHydrating ? (
            <Alert tone="destructive" className="mb-4">
              <AlertTriangle />
              <div>
                <AlertTitle>Thread recovery failed</AlertTitle>
                <AlertDescription>{errorMessage}</AlertDescription>
                <Button variant="outline" size="sm" className="mt-3 gap-2 rounded-xl font-ui text-sm font-medium" onClick={retry}>
                  <RefreshCw className="size-4" />
                  Retry
                </Button>
              </div>
            </Alert>
          ) : null}

          <ThreadPrimitive.Empty>
            <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
              <h2 className="text-2xl font-semibold tracking-tight text-text">
                How can I help you?
              </h2>
              <p className="mt-2 max-w-md font-ui text-sm text-muted">
                Start a conversation with NOA. Your threads are saved automatically.
              </p>
            </div>
          </ThreadPrimitive.Empty>

          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />

          {isRunning && (
            <div className="flex items-center gap-2 py-2 text-sm text-muted">
              <LoaderCircle className="size-3.5 animate-spin" />
              Thinking…
            </div>
          )}
        </div>
      </ThreadPrimitive.Viewport>

      {/* Composer pinned to bottom */}
      <div className="border-t border-border/50 bg-bg/80 backdrop-blur">
        <ComposerPrimitive.Root className="mx-auto w-full max-w-3xl px-4 py-3">
          <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface p-2 shadow-sm">
            <ComposerPrimitive.Input
              className="min-h-[44px] flex-1 resize-none border-0 bg-transparent px-3 py-2 font-ui text-sm text-text outline-none placeholder:text-muted"
              placeholder="Ask NOA…"
            />
            {isRunning ? (
              <ComposerPrimitive.Cancel asChild type="button">
                <Button
                  size="icon"
                  variant="outline"
                  className="size-9 shrink-0 rounded-xl"
                  aria-label="Stop"
                >
                  <Square className="size-4" />
                </Button>
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send asChild type="submit">
                <Button
                  size="icon"
                  className="size-9 shrink-0 rounded-xl"
                  aria-label="Send"
                >
                  <ArrowUp className="size-4" />
                </Button>
              </ComposerPrimitive.Send>
            )}
          </div>
        </ComposerPrimitive.Root>
      </div>
    </ThreadPrimitive.Root>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/noa/components/assistant/assistant-thread-panel.tsx
git commit -m "refactor(assistant): redesign ThreadPanel — centered, borderless, Claude/ChatGPT style"
```

---

### Task 11: Delete the old ThreadSidebar (now unused)

**Files:**
- Delete: `apps/noa/components/assistant/assistant-thread-sidebar.tsx`

- [ ] **Step 1: Delete the old file**

```bash
rm apps/noa/components/assistant/assistant-thread-sidebar.tsx
```

- [ ] **Step 2: Verify build**

Run: `npm run build` from `apps/noa`
Expected: Build succeeds. No remaining imports of `ThreadSidebar` since Task 9 removed it from `assistant-workspace.tsx`.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: delete old ThreadSidebar, now in ChatShell"
```

---

### Task 12: Verify full build and visual smoke test

**Files:** None (verification only)

- [ ] **Step 1: Build**

Run: `npm run build` from `apps/noa`
Expected: Build succeeds with zero errors.

- [ ] **Step 2: Start dev server and visually verify**

Run: `npm run dev` from `apps/noa`

Check these routes:
- `/login` — login page unchanged
- `/assistant` — clean chat UI: sidebar with thread list only, centered greeting + composer, no page header, no thread card, no dashed borders
- `/assistant/:threadId` — conversation fills canvas, composer at bottom, running indicator, stop button only when active
- `/admin/users` — admin shell with admin nav, "Back to chat" link, page header
- `/admin/roles` — same admin shell
- `/admin/audit` — same admin shell
- `/admin/whm/servers` — same admin shell
- `/admin/proxmox/servers` — same admin shell
- Admin link visible in chat sidebar user area (for admin users)

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "fix: address visual smoke test findings"
```

---

### Task 13: Clean up dead code

**Files:**
- Possibly delete: `apps/noa/components/layout/app-shell.tsx` (if no remaining imports)
- Possibly delete: `apps/noa/components/layout/protected-screen.tsx` (if no remaining imports)
- Possibly delete: `apps/noa/components/layout/nav-items.ts` (if no remaining imports)

- [ ] **Step 1: Check for remaining imports of old shell**

Search for imports of `app-shell`, `protected-screen`, and `nav-items` across the codebase. If none remain after Tasks 7 and 8, delete the files.

```bash
rg "from.*app-shell" apps/noa --include="*.tsx" --include="*.ts"
rg "from.*protected-screen" apps/noa --include="*.tsx" --include="*.ts"
rg "from.*nav-items" apps/noa --include="*.tsx" --include="*.ts"
```

- [ ] **Step 2: Delete unused files**

```bash
# Only delete if grep returns zero results:
rm apps/noa/components/layout/app-shell.tsx
rm apps/noa/components/layout/protected-screen.tsx
rm apps/noa/components/layout/nav-items.ts
```

- [ ] **Step 3: Final build verification**

Run: `npm run build` from `apps/noa`
Expected: Build succeeds with zero errors.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove dead AppShell, ProtectedScreen, nav-items after shell split"
```
