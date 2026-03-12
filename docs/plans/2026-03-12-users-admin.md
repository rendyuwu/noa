# Users Admin (/admin/users) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the assistant sidebar "Customize" entry with an admin-only "Users" link, remove the sidebar "Admin" link, and implement a future-proof `/admin/users` user management UI (table + right slide-over) to enable/disable users and set per-user authorized tools with solid error handling.

**Architecture:** Next.js web route `/admin/users` fetches admin data via same-origin `/api/*` proxy calls to the existing FastAPI `/admin/*` endpoints. The page lists users in a table and uses a Radix Dialog-based right slide-over panel for editing a selected user (status + tool allowlist). Errors are surfaced as inline banners using existing tokenized color classes.

**Tech Stack:** Next.js App Router (apps/web), React, Tailwind token classes (`bg-bg`, `bg-surface`, `text-text`, `border-border`), Radix Dialog + Icons, Vitest + Testing Library; API is FastAPI with existing admin routes; E2E verification via repo skill `noa-playwright-smoke`.

---

## Worktree

This plan assumes you are working in a dedicated worktree:

- Worktree path: `.worktrees/feat/admin-users`
- Branch: `feat/admin-users`

Create it from repo root:

```bash
git worktree add ".worktrees/feat/admin-users" -b "feat/admin-users"
```

---

## Safety (secrets)

- Playwright credentials MUST come from env vars `NOA_TEST_USER` and `NOA_TEST_PASSWORD`.
- Do not print them, do not write them to disk, do not paste them into tool calls.
- Do not commit `.env`, `.env.local`, or `.artifacts/*`.

---

### Task 1: Update sidebar tests for "Users" + remove "Admin"

**Files:**

- Modify: `apps/web/components/claude/claude-thread-list.test.tsx`

**Step 1: Write the failing test changes**

Update expectations:

- Replace "Customize" in the disabled list with admin-only "Users" link assertions.
- Remove all expectations for the "Admin" footer link.
- Add a new test that "Users" is hidden for non-admin roles.

Example edits:

```ts
it("renders disabled Claude-style nav items under the new chat button", () => {
  render(<ClaudeThreadList />);

  for (const label of ["Search", "Projects", "Artifacts", "Code"]) {
    const button = screen.getByRole("button", { name: label });
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).not.toBeDisabled();
  }

  const users = screen.getByRole("link", { name: "Users" });
  expect(users).toHaveAttribute("href", "/admin/users");
});

it("hides the Users link for non-admin users", () => {
  mocks.user = {
    id: "1",
    email: "casey@example.com",
    display_name: "Casey Rivers",
    roles: ["member"],
  };

  render(<ClaudeThreadList />);
  expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
});
```

Also update the existing tests to remove:

```ts
screen.getByRole("link", { name: "Admin" })
```

**Step 2: Run the test to confirm failure**

Run:

```bash
cd apps/web && npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: FAIL (component still renders "Customize" and the footer "Admin" link).

**Step 3: Commit the failing test**

```bash
git add apps/web/components/claude/claude-thread-list.test.tsx
git commit -m "test(web): expect sidebar Users link and remove Admin"
```

---

### Task 2: Implement sidebar "Users" link (admin-only) + icon swap + remove footer Admin

**Files:**

- Modify: `apps/web/components/claude/claude-thread-list.tsx`
- Test: `apps/web/components/claude/claude-thread-list.test.tsx`

**Step 1: Update sidebar nav item and icon**

- Replace the disabled "Customize" item with an enabled Link labeled "Users".
- Only render it when the stored auth user includes `roles` containing `"admin"`.
- Swap the icon from `GearIcon` to a user-shaped icon from `@radix-ui/react-icons` (e.g. `PersonIcon`).
- Reuse existing sidebar token classes for colors.

Example:

```tsx
import {
  // ...
  PersonIcon,
  // remove GearIcon
} from "@radix-ui/react-icons";

function NavLinkItem({
  icon,
  label,
  href,
}: {
  icon: ReactNode;
  label: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="flex w-full items-center justify-start gap-3 rounded-lg px-4 py-2 font-ui text-sm text-muted transition-colors hover:bg-surface-2/60 hover:text-text"
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </Link>
  );
}

// inside ClaudeThreadList
const isAdmin = Boolean(user?.roles?.includes("admin"));

<DisabledNavItem icon={<MagnifyingGlassIcon width={16} height={16} />} label="Search" />
{isAdmin ? (
  <NavLinkItem icon={<PersonIcon width={16} height={16} />} label="Users" href="/admin/users" />
) : null}
<DisabledNavItem icon={<LayersIcon width={16} height={16} />} label="Projects" />
```

**Step 2: Remove footer "Admin" link**

Delete the footer `Link` to `/admin` and keep only the Logout button.

**Step 3: Run web tests**

Run:

```bash
cd apps/web && npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/web/components/claude/claude-thread-list.tsx
git commit -m "feat(web): add admin-only Users nav and remove Admin link"
```

---

### Task 3: Turn `/admin` into an index redirect to `/admin/users`

**Files:**

- Modify: `apps/web/app/(admin)/admin/page.tsx`

**Step 1: Replace the page with a server redirect**

Replace the current client page with:

```tsx
import { redirect } from "next/navigation";

export default function AdminIndexPage() {
  redirect("/admin/users");
}
```

**Step 2: Run a build/typecheck to ensure Next compiles routes**

Run:

```bash
cd apps/web && npm run typecheck
```

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/app/(admin)/admin/page.tsx
git commit -m "refactor(web): redirect /admin to /admin/users"
```

---

### Task 4: Add the `/admin/users` route shell (auth gate)

**Files:**

- Create: `apps/web/app/(admin)/admin/users/page.tsx`

**Step 1: Create the route file**

Create:

```tsx
"use client";

import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { useRequireAuth } from "@/components/lib/auth-store";

export default function AdminUsersPageRoute() {
  const ready = useRequireAuth();
  if (!ready) {
    return null;
  }
  return <UsersAdminPage />;
}
```

**Step 2: Run typecheck**

Run:

```bash
cd apps/web && npm run typecheck
```

Expected: FAIL (until `UsersAdminPage` exists).

**Step 3: Commit the route shell (optional; OK to defer until component exists)**

```bash
git add apps/web/app/(admin)/admin/users/page.tsx
git commit -m "feat(web): add /admin/users route"
```

---

### Task 5: UsersAdminPage - failing test for table rendering + API wiring

**Files:**

- Create: `apps/web/components/admin/users-admin-page.test.tsx`
- Create (later): `apps/web/components/admin/users-admin-page.tsx`

**Step 1: Write a failing test for initial load rendering**

Create `apps/web/components/admin/users-admin-page.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: any) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/lib/auth-store", () => ({
  clearAuth: vi.fn(),
}));

vi.mock("@/components/lib/fetch-helper", async () => {
  const actual = await vi.importActual<any>("@/components/lib/fetch-helper");
  return {
    ...actual,
    fetchWithAuth: mocks.fetchWithAuth,
    jsonOrThrow: mocks.jsonOrThrow,
  };
});

import { UsersAdminPage } from "./users-admin-page";

describe("UsersAdminPage", () => {
  it("renders a users table after loading", async () => {
    mocks.fetchWithAuth
      .mockResolvedValueOnce({} as any)
      .mockResolvedValueOnce({} as any);

    mocks.jsonOrThrow
      .mockResolvedValueOnce({
        users: [
          {
            id: "u1",
            email: "member@example.com",
            display_name: "Member",
            is_active: false,
            roles: ["member"],
            tools: ["get_current_time"],
          },
        ],
      })
      .mockResolvedValueOnce({ tools: ["get_current_time", "set_demo_flag"] });

    render(<UsersAdminPage />);

    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(await screen.findByText("member@example.com")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});
```

**Step 2: Run the test to confirm failure**

Run:

```bash
cd apps/web && npm test -- components/admin/users-admin-page.test.tsx
```

Expected: FAIL (module `./users-admin-page` does not exist yet).

**Step 3: Commit failing test**

```bash
git add apps/web/components/admin/users-admin-page.test.tsx
git commit -m "test(web): add Users admin page loading test"
```

---

### Task 6: Implement UsersAdminPage skeleton (table + errors + load)

**Files:**

- Create: `apps/web/components/admin/users-admin-page.tsx`
- Modify: `apps/web/app/(admin)/admin/users/page.tsx`
- Test: `apps/web/components/admin/users-admin-page.test.tsx`

**Step 1: Create `UsersAdminPage` as a client component**

Create `apps/web/components/admin/users-admin-page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import * as Dialog from "@radix-ui/react-dialog";

import { clearAuth } from "@/components/lib/auth-store";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

type AdminUser = {
  id: string;
  email: string;
  display_name?: string | null;
  is_active: boolean;
  roles: string[];
  tools: string[];
};

export function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [tools, setTools] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const selectedUser = useMemo(
    () => users.find((u) => u.id === selectedUserId) ?? null,
    [users, selectedUserId]
  );

  const [toolQuery, setToolQuery] = useState("");
  const [draftTools, setDraftTools] = useState<Set<string>>(new Set());
  const [panelError, setPanelError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setError(null);
    setLoading(true);
    try {
      const [usersResponse, toolsResponse] = await Promise.all([
        fetchWithAuth("/admin/users"),
        fetchWithAuth("/admin/tools"),
      ]);

      const usersBody = await jsonOrThrow<{ users: AdminUser[] }>(usersResponse);
      const toolsBody = await jsonOrThrow<{ tools: string[] }>(toolsResponse);
      setUsers(usersBody.users);
      setTools(toolsBody.tools);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    setPanelError(null);
    setToolQuery("");
    setDraftTools(new Set(selectedUser?.tools ?? []));
  }, [selectedUserId]);

  const filteredTools = useMemo(() => {
    const q = toolQuery.trim().toLowerCase();
    if (!q) return tools;
    return tools.filter((name) => name.toLowerCase().includes(q));
  }, [tools, toolQuery]);

  return (
    <main className="min-h-dvh p-4 text-text sm:p-6">
      <div className="mx-auto w-full max-w-6xl">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="font-body text-3xl leading-tight tracking-tight">Users</h1>
            <p className="mt-1 font-ui text-sm text-muted">Enable accounts and set tool access.</p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Link
              className="inline-flex items-center justify-center rounded-lg border border-transparent bg-accent px-3 py-2 font-ui text-sm font-medium text-white shadow-sm transition-colors hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
              href="/assistant"
            >
              Assistant
            </Link>
            <button
              className="inline-flex items-center justify-center rounded-lg border border-border bg-surface px-3 py-2 font-ui text-sm font-medium text-text shadow-sm transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
              onClick={clearAuth}
              type="button"
            >
              Logout
            </button>
          </div>
        </header>

        {error ? (
          <div
            className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
            role="alert"
          >
            {error}
          </div>
        ) : null}

        <section className="mt-6 overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
          <div className="flex items-center justify-between gap-3 border-border border-b px-4 py-3">
            <p className="font-ui text-sm font-semibold text-text">{users.length} users</p>
            {loading ? <p className="font-ui text-xs text-muted">Loading…</p> : null}
          </div>

          <div className="w-full overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse">
              <thead>
                <tr className="bg-surface-2/40">
                  <th className="px-4 py-3 text-left font-ui text-xs font-semibold uppercase tracking-[0.12em] text-muted">
                    User
                  </th>
                  <th className="px-4 py-3 text-left font-ui text-xs font-semibold uppercase tracking-[0.12em] text-muted">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left font-ui text-xs font-semibold uppercase tracking-[0.12em] text-muted">
                    Roles
                  </th>
                  <th className="px-4 py-3 text-left font-ui text-xs font-semibold uppercase tracking-[0.12em] text-muted">
                    Tools
                  </th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr
                    key={user.id}
                    className="cursor-pointer border-border border-t transition-colors hover:bg-surface-2/40"
                    onClick={() => setSelectedUserId(user.id)}
                  >
                    <td className="px-4 py-3">
                      <p className="font-ui text-sm font-semibold text-text">{user.display_name || user.email}</p>
                      <p className="mt-0.5 font-ui text-xs text-muted">{user.email}</p>
                    </td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center rounded-full border border-border bg-surface-2 px-2.5 py-1 font-ui text-xs text-text">
                        {user.is_active ? "Enabled" : "Disabled"}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-ui text-sm text-muted">{user.roles.join(", ") || "none"}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-ui text-sm text-muted">{user.tools.length}</p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <Dialog.Root
          open={Boolean(selectedUser)}
          onOpenChange={(open) => (open ? null : setSelectedUserId(null))}
        >
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />
            <Dialog.Content
              className={[
                "fixed inset-y-0 right-0 z-50 w-full max-w-[34rem]",
                "bg-bg shadow-[0_1rem_3rem_rgba(0,0,0,0.35)]",
                "transition-transform duration-200 ease-out",
                "data-[state=open]:translate-x-0 data-[state=closed]:translate-x-full",
                "outline-none",
              ].join(" ")}
            >
              <Dialog.Title className="sr-only">Edit user</Dialog.Title>
              <Dialog.Description className="sr-only">
                Enable or disable the user account and manage tool access.
              </Dialog.Description>

              <div className="flex h-full flex-col">
                <div className="border-border border-b px-5 py-4">
                  <p className="font-ui text-sm font-semibold text-text">{selectedUser?.display_name || selectedUser?.email}</p>
                  <p className="mt-1 font-ui text-xs text-muted">{selectedUser?.email}</p>
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
                  {panelError ? (
                    <div
                      className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
                      role="alert"
                    >
                      {panelError}
                    </div>
                  ) : null}

                  <div className="rounded-xl border border-border bg-surface p-4">
                    <p className="font-ui text-sm font-semibold text-text">Status</p>
                    <p className="mt-1 font-ui text-sm text-muted">
                      {selectedUser?.is_active
                        ? "This account can sign in and use authorized tools."
                        : "This account is disabled until an admin enables it."}
                    </p>
                  </div>

                  <div className="mt-4 rounded-xl border border-border bg-surface p-4">
                    <p className="font-ui text-sm font-semibold text-text">Authorized tools</p>
                    <p className="mt-1 font-ui text-sm text-muted">Select tools this user may run.</p>

                    <input
                      className="mt-3 w-full rounded-lg border border-border bg-surface px-3 py-2 font-ui text-sm text-text shadow-sm outline-none placeholder:text-muted focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                      placeholder="Filter tools…"
                      value={toolQuery}
                      onChange={(e) => setToolQuery(e.target.value)}
                    />

                    <div className="mt-3 max-h-64 overflow-y-auto rounded-lg border border-border bg-surface">
                      <div className="divide-y divide-border">
                        {filteredTools.map((tool) => (
                          <label key={tool} className="flex cursor-pointer items-center gap-3 px-3 py-2 font-ui text-sm text-text hover:bg-surface-2/40">
                            <input
                              type="checkbox"
                              className="h-4 w-4"
                              checked={draftTools.has(tool)}
                              onChange={() => {
                                setDraftTools((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(tool)) next.delete(tool);
                                  else next.add(tool);
                                  return next;
                                });
                              }}
                            />
                            <span className="min-w-0 flex-1 truncate">{tool}</span>
                          </label>
                        ))}
                        {!filteredTools.length ? (
                          <div className="px-3 py-3 font-ui text-sm text-muted">No tools match that filter.</div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="border-border border-t px-5 py-4">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
                    <Dialog.Close asChild>
                      <button
                        type="button"
                        className="inline-flex items-center justify-center rounded-lg border border-border bg-surface px-3 py-2 font-ui text-sm font-medium text-text shadow-sm transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                      >
                        Close
                      </button>
                    </Dialog.Close>
                    <button
                      type="button"
                      disabled={saving || !selectedUser}
                      className="inline-flex items-center justify-center rounded-lg border border-transparent bg-accent px-3 py-2 font-ui text-sm font-medium text-white shadow-sm transition-colors hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={async () => {
                        if (!selectedUser) return;
                        setPanelError(null);
                        setSaving(true);
                        try {
                          const response = await fetchWithAuth(`/admin/users/${selectedUser.id}/tools`, {
                            method: "PUT",
                            headers: { "content-type": "application/json" },
                            body: JSON.stringify({ tools: Array.from(draftTools).sort() }),
                          });
                          await jsonOrThrow(response);
                          await load();
                        } catch (saveError) {
                          setPanelError(saveError instanceof Error ? saveError.message : "Failed to save tools");
                        } finally {
                          setSaving(false);
                        }
                      }}
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </div>
    </main>
  );
}
```

**Step 2: Ensure the route imports compile**

Update `apps/web/app/(admin)/admin/users/page.tsx` to import from `@/components/admin/users-admin-page`.

**Step 3: Run tests**

Run:

```bash
cd apps/web && npm test -- components/admin/users-admin-page.test.tsx
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/web/components/admin/users-admin-page.tsx \
  apps/web/app/(admin)/admin/users/page.tsx
git commit -m "feat(web): add /admin/users table and edit drawer"
```

---

### Task 7: Add enable/disable controls in the slide-over + conflict error handling

**Files:**

- Modify: `apps/web/components/admin/users-admin-page.tsx`

**Step 1: Write failing tests for enabling/disabling (optional but recommended)**

Extend `apps/web/components/admin/users-admin-page.test.tsx` to:

- open a user drawer (click the row)
- click an Enable/Disable button
- assert `fetchWithAuth` called with `PATCH /admin/users/{id}`
- simulate a `409` error message and assert it renders in `panelError`

**Step 2: Implement the UI + request**

In the Status card, add an action button:

```tsx
<button
  type="button"
  className={selectedUser?.is_active ? dangerBtn : primaryBtn}
  onClick={async () => {
    if (!selectedUser) return;
    setPanelError(null);
    setSaving(true);
    try {
      const response = await fetchWithAuth(`/admin/users/${selectedUser.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ is_active: !selectedUser.is_active }),
      });
      await jsonOrThrow(response);
      await load();
    } catch (toggleError) {
      setPanelError(toggleError instanceof Error ? toggleError.message : "Failed to update user");
    } finally {
      setSaving(false);
    }
  }}
>
  {selectedUser?.is_active ? "Disable" : "Enable"}
</button>
```

Implement `primaryBtn` / `dangerBtn` class strings using the same patterns already used in the old admin page (no new colors; reuse existing Tailwind + tokens).

**Step 3: Run web tests**

Run:

```bash
cd apps/web && npm test
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/web/components/admin/users-admin-page.tsx \
  apps/web/components/admin/users-admin-page.test.tsx
git commit -m "feat(web): manage user active status in admin drawer"
```

---

### Task 8: Improve friendly error messaging for pending approval (login)

**Files:**

- Modify: `apps/web/app/login/page.tsx`

**Step 1: Write a focused unit test (optional)**

If adding tests for login is too costly, skip tests and rely on manual verification.

**Step 2: Map the known API error string to a friendlier message**

Keep API detail stable, but show a friendlier UI string:

```ts
if (!response.ok) {
  const detail = payload?.detail;
  if (detail === "User pending approval") {
    setError("Your account is pending approval. Ask an admin to enable it.");
  } else {
    setError(detail ?? "Login failed");
  }
  return;
}
```

**Step 3: Commit**

```bash
git add apps/web/app/login/page.tsx
git commit -m "fix(web): show friendly pending-approval login message"
```

---

### Task 9: Verify API behavior unchanged (tests)

**Files:** none

**Step 1: Run API tests**

Run:

```bash
cd apps/api && uv run pytest -q
```

Expected: PASS.

---

### Task 10: Web verification (tests + build)

**Files:** none

**Step 1: Run web unit tests**

Run:

```bash
cd apps/web && npm test
```

Expected: PASS.

**Step 2: Run web typecheck/build**

Run:

```bash
cd apps/web && npm run build
```

Expected: PASS.

---

### Task 11: E2E smoke verification (REQUIRED) via `noa-playwright-smoke`

**Files:** none

Follow the repo skill `noa-playwright-smoke` exactly. Minimum required outcome is that login succeeds and `/assistant` loads.

**Step 1: Ensure env files exist (copy only if missing)**

Run (repo root):

```bash
if [ ! -f apps/web/.env.local ]; then
  cp apps/web/.env.example apps/web/.env.local
fi

if [ ! -f apps/api/.env ]; then
  cp apps/api/.env.example apps/api/.env
fi
```

**Step 2: Start servers (background) + record artifacts**

Run (repo root):

```bash
ARTIFACTS=".artifacts/noa-playwright-smoke/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ARTIFACTS"
printf "%s\n" "$ARTIFACTS" >".artifacts/noa-playwright-smoke/LAST_ARTIFACTS"

( cd apps/api && exec uv run uvicorn noa_api.main:app --port 8000 ) \
  >"$ARTIFACTS/api.log" 2>&1 &
API_PID=$!

( cd apps/web && exec npm run dev -- --port 3000 ) \
  >"$ARTIFACTS/web.log" 2>&1 &
WEB_PID=$!

printf "%s\n" "$API_PID" >"$ARTIFACTS/api.pid"
printf "%s\n" "$WEB_PID" >"$ARTIFACTS/web.pid"

echo "ARTIFACTS=$ARTIFACTS"
```

**Step 3: Wait for API readiness**

Run:

```bash
for i in $(seq 1 60); do
  body="$(curl -fsS http://localhost:8000/health 2>/dev/null || true)"
  compact="$(printf "%s" "$body" | tr -d '\n' | tr -d '\r' | tr -d ' ')"
  if [ "$compact" = "{\"status\":\"ok\"}" ]; then
    echo "API ready"
    break
  fi
  sleep 1
done
```

Expected: prints `API ready`.

**Step 4: Run Playwright MCP code (NO SECRETS IN TOOL TEXT)**

Use a single `playwright_browser_run_code` snippet that reads `process.env.NOA_TEST_USER` and `process.env.NOA_TEST_PASSWORD`.

Expected: reaches `/assistant` and finds `[data-testid="thread-viewport"]`.

**Step 5: On failure, capture artifacts (required)**

- Screenshot: `$ARTIFACTS/failure.png`
- Console errors: `$ARTIFACTS/console-errors.txt`
- Network requests: `$ARTIFACTS/network-requests.txt`

**Step 6: Always cleanup (required)**

Use the skill's cleanup script reading pidfiles from `.artifacts/noa-playwright-smoke/LAST_ARTIFACTS`.

---

## Notes / Non-goals

- No backend schema changes required: user provisioning + default disabled behavior already exists and is covered by `apps/api/tests/test_auth_login.py`.
- Admin API endpoints already exist; this change focuses on UX and safer controls.
