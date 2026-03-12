# Admin Users Sidebar Collapsed-by-Default Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the existing Claude sidebar to `/admin/users`, default it to collapsed on desktop, and keep it fully openable (desktop + mobile) while preserving current users management behavior.

**Architecture:** Introduce a reusable admin shell component that reuses `ClaudeThreadList` directly and wraps arbitrary page content with desktop-collapsible + mobile-drawer sidebar behavior. Wire `/admin/users` through `NoaAssistantRuntimeProvider` and this new shell, keeping `UsersAdminPage` logic unchanged. Validate with targeted unit tests and Playwright screenshots as a required completion gate.

**Tech Stack:** Next.js App Router, React, Radix Dialog, assistant-ui runtime + thread list primitives, Tailwind token classes, Vitest + Testing Library, Playwright (`@noa-playwright-smoke` + browser automation).

---

## Worktree

Create and use a dedicated worktree before implementation:

```bash
git worktree add ".worktrees/feat/admin-users-sidebar-collapsed" -b "feat/admin-users-sidebar-collapsed"
```

---

## Safety (secrets + artifacts)

- Never print credentials.
- Read `NOA_TEST_USER` and `NOA_TEST_PASSWORD` from env only.
- Do not commit `.env`, `.env.local`, `.env.*`, or Playwright artifact images.

---

### Task 1: Add failing tests for the new admin sidebar shell behavior

**Files:**

- Create: `apps/web/components/admin/admin-sidebar-shell.test.tsx`

**Step 1: Write the failing tests**

Create tests that define expected behavior before implementation:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@radix-ui/react-dialog", async () => {
  const React = await import("react");
  return {
    Root: ({ children }: any) => <div>{children}</div>,
    Portal: ({ children }: any) => <div>{children}</div>,
    Overlay: (props: any) => <div {...props} />,
    Content: (props: any) => <div {...props} />,
    Title: (props: any) => <h2 {...props} />,
    Description: (props: any) => <p {...props} />,
  };
});

vi.mock("@/components/claude/claude-thread-list", () => ({
  ClaudeThreadList: ({ onCloseSidebar, onSelectThread }: any) => (
    <div data-testid="claude-thread-list">
      <button onClick={onCloseSidebar} type="button">
        Close sidebar
      </button>
      <button onClick={onSelectThread} type="button">
        Select thread
      </button>
    </div>
  ),
}));

import { AdminSidebarShell } from "./admin-sidebar-shell";

describe("AdminSidebarShell", () => {
  beforeEach(() => {
    mocks.push.mockReset();
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }));
  });

  it("starts desktop collapsed and shows an open-sidebar button", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div data-testid="users-page" />
      </AdminSidebarShell>,
    );

    const grid = container.querySelector(".grid");
    expect(grid).toHaveClass("md:grid-cols-1");
    expect(screen.getByRole("button", { name: "Open sidebar" })).toBeInTheDocument();
  });

  it("expands the desktop sidebar when Open sidebar is clicked", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div data-testid="users-page" />
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));
    const grid = container.querySelector(".grid");
    expect(grid).toHaveClass("md:grid-cols-[18rem_minmax(0,1fr)]");
    expect(screen.getByTestId("claude-thread-list")).toBeInTheDocument();
  });

  it("routes to /assistant when a sidebar thread action is selected", () => {
    render(
      <AdminSidebarShell>
        <div data-testid="users-page" />
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Select thread" }));
    expect(mocks.push).toHaveBeenCalledWith("/assistant");
  });
});
```

**Step 2: Run the test to verify failure**

Run:

```bash
cd apps/web && npm test -- components/admin/admin-sidebar-shell.test.tsx
```

Expected: FAIL (`AdminSidebarShell` does not exist yet).

**Step 3: Commit failing test**

```bash
git add apps/web/components/admin/admin-sidebar-shell.test.tsx
git commit -m "test(web): define admin sidebar shell behavior"
```

---

### Task 2: Implement `AdminSidebarShell` using the existing sidebar component

**Files:**

- Create: `apps/web/components/admin/admin-sidebar-shell.tsx`
- Test: `apps/web/components/admin/admin-sidebar-shell.test.tsx`

**Step 1: Implement minimal shell to satisfy tests**

Create component with desktop-collapsed default, mobile drawer support, and sidebar reuse:

```tsx
"use client";

import type { PropsWithChildren } from "react";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import * as Dialog from "@radix-ui/react-dialog";
import { HamburgerMenuIcon } from "@radix-ui/react-icons";

import { ClaudeThreadList } from "@/components/claude/claude-thread-list";

export function AdminSidebarShell({ children }: PropsWithChildren) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(false);

  const openSidebar = useCallback(() => {
    setDesktopSidebarOpen(true);
    if (window.matchMedia("(min-width: 768px)").matches) return;
    setOpen(true);
  }, []);

  const closeSidebar = useCallback(() => setOpen(false), []);
  const closeDesktopSidebar = useCallback(() => setDesktopSidebarOpen(false), []);

  const goToAssistant = useCallback(() => {
    setOpen(false);
    router.push("/assistant");
  }, [router]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 768px)");
    const closeOnDesktop = (event: MediaQueryList | MediaQueryListEvent) => {
      if (event.matches) setOpen(false);
    };
    closeOnDesktop(mediaQuery);

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener("change", closeOnDesktop);
      return () => mediaQuery.removeEventListener("change", closeOnDesktop);
    }

    mediaQuery.addListener(closeOnDesktop);
    return () => mediaQuery.removeListener(closeOnDesktop);
  }, []);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <section className="relative h-dvh w-full overflow-hidden bg-bg">
        {!desktopSidebarOpen ? (
          <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
            <button
              type="button"
              onClick={openSidebar}
              aria-label="Open sidebar"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-surface/70 text-muted shadow-sm backdrop-blur-sm transition hover:bg-surface hover:text-text active:scale-[0.98]"
            >
              <HamburgerMenuIcon width={18} height={18} />
            </button>
          </div>
        ) : null}

        <div
          className={[
            "grid h-full min-h-0 grid-cols-1",
            desktopSidebarOpen ? "md:grid-cols-[18rem_minmax(0,1fr)]" : "md:grid-cols-1",
          ].join(" ")}
        >
          {desktopSidebarOpen ? (
            <aside className="hidden h-full min-h-0 border-border border-r md:block">
              <ClaudeThreadList onSelectThread={goToAssistant} onCloseSidebar={closeDesktopSidebar} />
            </aside>
          ) : null}

          <div className="h-full min-h-0 min-w-0 overflow-auto">{children}</div>
        </div>

        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0 md:hidden" />
          <Dialog.Content
            className={[
              "fixed inset-y-0 left-0 z-50 w-[18rem] max-w-[86vw]",
              "bg-bg shadow-[0_1rem_3rem_rgba(0,0,0,0.22)]",
              "transition-transform duration-200 ease-out",
              "data-[state=open]:translate-x-0 data-[state=closed]:-translate-x-full",
              "outline-none md:hidden",
            ].join(" ")}
          >
            <Dialog.Title className="sr-only">Sidebar</Dialog.Title>
            <Dialog.Description className="sr-only">Admin navigation and recent threads.</Dialog.Description>
            <div className="h-full">
              <ClaudeThreadList onSelectThread={goToAssistant} onCloseSidebar={closeSidebar} />
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </section>
    </Dialog.Root>
  );
}
```

**Step 2: Run test to verify pass**

Run:

```bash
cd apps/web && npm test -- components/admin/admin-sidebar-shell.test.tsx
```

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/components/admin/admin-sidebar-shell.tsx
git commit -m "feat(web): add reusable admin sidebar shell"
```

---

### Task 3: Add failing route-level composition test for `/admin/users`

**Files:**

- Create: `apps/web/components/admin/admin-users-page-route.test.tsx`

**Step 1: Write a failing route composition test**

```tsx
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  ready: true,
}));

vi.mock("@/components/lib/auth-store", () => ({
  useRequireAuth: () => mocks.ready,
}));

vi.mock("@/components/lib/runtime-provider", async () => {
  const React = await import("react");
  return {
    NoaAssistantRuntimeProvider: ({ children }: { children?: React.ReactNode }) => (
      <div data-testid="runtime-provider">{children}</div>
    ),
  };
});

vi.mock("@/components/admin/admin-sidebar-shell", async () => {
  const React = await import("react");
  return {
    AdminSidebarShell: ({ children }: { children?: React.ReactNode }) => (
      <div data-testid="admin-sidebar-shell">{children}</div>
    ),
  };
});

vi.mock("@/components/admin/users-admin-page", () => ({
  UsersAdminPage: () => <div data-testid="users-admin-page" />,
}));

import AdminUsersPage from "@/app/(admin)/admin/users/page";

describe("/admin/users route composition", () => {
  beforeEach(() => {
    mocks.ready = true;
  });

  it("wraps UsersAdminPage with runtime provider and admin sidebar shell", () => {
    render(<AdminUsersPage />);
    expect(screen.getByTestId("runtime-provider")).toBeInTheDocument();
    expect(screen.getByTestId("admin-sidebar-shell")).toBeInTheDocument();
    expect(screen.getByTestId("users-admin-page")).toBeInTheDocument();
  });

  it("returns null when auth gate is not ready", () => {
    mocks.ready = false;
    const { container } = render(<AdminUsersPage />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

**Step 2: Run test to verify failure**

Run:

```bash
cd apps/web && npm test -- components/admin/admin-users-page-route.test.tsx
```

Expected: FAIL because page currently renders `UsersAdminPage` directly.

**Step 3: Commit failing test**

```bash
git add apps/web/components/admin/admin-users-page-route.test.tsx
git commit -m "test(web): define /admin/users shell composition"
```

---

### Task 4: Wire `/admin/users` to runtime provider + `AdminSidebarShell`

**Files:**

- Modify: `apps/web/app/(admin)/admin/users/page.tsx`
- Test: `apps/web/components/admin/admin-users-page-route.test.tsx`
- Test: `apps/web/components/admin/admin-sidebar-shell.test.tsx`

**Step 1: Implement route wrapper**

Update the route page:

```tsx
"use client";

import { AdminSidebarShell } from "@/components/admin/admin-sidebar-shell";
import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { useRequireAuth } from "@/components/lib/auth-store";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime-provider";

export default function AdminUsersPage() {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return (
    <main className="min-h-dvh bg-bg p-0">
      <NoaAssistantRuntimeProvider>
        <AdminSidebarShell>
          <UsersAdminPage />
        </AdminSidebarShell>
      </NoaAssistantRuntimeProvider>
    </main>
  );
}
```

**Step 2: Run focused tests**

Run:

```bash
cd apps/web && npm test -- components/admin/admin-sidebar-shell.test.tsx components/admin/admin-users-page-route.test.tsx
```

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/app/(admin)/admin/users/page.tsx
git commit -m "feat(web): reuse claude sidebar on /admin/users"
```

---

### Task 5: Run regression tests for existing users admin behavior

**Files:**

- Test: `apps/web/components/admin/users-admin-page.test.tsx`
- Test: `apps/web/components/claude/claude-thread-list.test.tsx`

**Step 1: Run regression tests**

Run:

```bash
cd apps/web && npm test -- components/admin/users-admin-page.test.tsx components/claude/claude-thread-list.test.tsx
```

Expected: PASS.

**Step 2: Fix only if tests fail (minimal patch)**

If a regression appears, apply the smallest possible fix in the touched files and re-run the same command until green.

**Step 3: Commit regression fix (only if needed)**

```bash
git add <touched-files>
git commit -m "fix(web): resolve admin sidebar integration regression"
```

---

### Task 6: Full web verification

**Files:** none

**Step 1: Run full web test suite**

Run:

```bash
cd apps/web && npm test
```

Expected: PASS.

**Step 2: Run production build/typecheck gate**

Run:

```bash
cd apps/web && npm run build
```

Expected: PASS.

---

### Task 7: Required Playwright verification and 5 screenshots before completion notice

**Files:** none (artifacts only; do not commit)

**Step 1: Start required services**

From repo root:

```bash
docker compose up -d postgres
```

Start API (separate terminal):

```bash
cd apps/api && uv run uvicorn noa_api.main:app --reload --port 8000
```

Start Web (separate terminal):

```bash
cd apps/web && npm run dev
```

**Step 2: Use `@noa-playwright-smoke` for auth + base route validation**

- Login with env credentials.
- Reach `/assistant` successfully.
- Confirm baseline app health before route-specific capture.

**Step 3: Capture required `/admin/users` screenshots with Playwright**

Use Playwright browser automation to capture exactly these files:

1. `01-admin-users-desktop-collapsed.png`
2. `02-admin-users-desktop-sidebar-open.png`
3. `03-admin-users-drawer-open.png`
4. `04-admin-users-mobile-sidebar-open.png`
5. `05-admin-users-to-assistant-nav.png`

**Step 4: Validate screenshot intent**

- Confirm image 1 shows desktop collapsed by default.
- Confirm image 2 shows opened desktop sidebar.
- Confirm image 3 shows user editor drawer.
- Confirm image 4 shows mobile sidebar drawer.
- Confirm image 5 shows navigation landing on `/assistant`.

**Step 5: Completion gate**

Do not report the feature as complete unless:

- unit tests + build are green, and
- all 5 screenshots are captured and valid.

---

## Final git check

Before opening PR:

```bash
git status
git log --oneline -n 10
```

Ensure only intended files are included.
