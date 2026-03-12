# Sidebar UI Dark Theme + Token Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `#1F1E1D` the global background, standardize on theme tokens instead of hard-coded hex, and polish the assistant sidebar (18rem width, 16px icon rail padding, hover + active thread styling).

**Architecture:** Single dark theme driven by CSS variables in `apps/web/app/globals.css` (consumed via Tailwind token colors). Assistant/sidebar components stop using hard-coded light/dark hex pairs and instead use semantic tokens (`bg-bg`, `bg-surface`, `text-text`, etc.). Selected thread styling uses assistant-ui's built-in `data-active` / `aria-current` state.

**Tech Stack:** Next.js (apps/web), Tailwind CSS v4 tokens + CSS variables, assistant-ui primitives, Radix UI, Vitest; smoke verification via repo skill `noa-playwright-smoke`.

---

## Worktree

This plan assumes you are working in the dedicated worktree:

- Worktree path: `.worktrees/fix/sidebar-ui-theme-tokens`
- Branch: `fix/sidebar-ui-theme-tokens`

## Safety (secrets)

- `noa-playwright-smoke` must read credentials from `NOA_TEST_USER` / `NOA_TEST_PASSWORD` via `process.env` inside Playwright.
- Do not print credentials.
- Do not commit `.env`, `.env.local`, or any artifacts.

---

### Task 1: Capture BEFORE Playwright smoke artifacts

**Files:** none

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

Expected: files are created only if missing.

**Step 2: Run the repo skill `noa-playwright-smoke` (baseline)**

- Start servers, login, reach `/assistant`, assert `[data-testid="thread-viewport"]`.
- Capture a success screenshot to the artifacts directory (eg, `before-assistant.png`).
- Cleanup must run even on failure.

Expected: PASS and an artifacts directory recorded for later comparison.

---

### Task 2: Add a failing test for the new global background token

**Files:**

- Modify: `apps/web/app/globals.test.ts`

**Step 1: Write the failing test**

Add:

```ts
it("sets the global background token to #1F1E1D", () => {
  const css = readFileSync(path.join(dirname, "globals.css"), "utf8");
  expect(css).toMatch(/--bg:\s*30\s+3\.3%\s+11\.8%\s*;/);
});
```

**Step 2: Run the test to confirm failure**

Run:

```bash
npm test -- app/globals.test.ts
```

Expected: FAIL (regex does not match the current `--bg`).

**Step 3: Commit the failing test**

```bash
git add apps/web/app/globals.test.ts
git commit -m "test(web): require dark bg token"
```

---

### Task 3: Implement the global dark theme tokens + flat background

**Files:**

- Modify: `apps/web/app/globals.css`
- Test: `apps/web/app/globals.test.ts`

**Step 1: Update `:root` tokens in `apps/web/app/globals.css`**

Replace the existing light theme values with a dark palette (starting point):

```css
:root {
  --bg: 30 3.3% 11.8%;
  --surface: 30 3% 14.5%;
  --surface-2: 30 3% 18%;
  --border: 30 3% 26%;
  --text: 30 9% 92%;
  --muted: 30 5% 70%;
  --accent: 22 90% 54%;
  --accent-ink: 22 96% 20%;

  --line: var(--border);

  --radius-lg: 10px;
  --radius-xl: 14px;

  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.45);
  --shadow-md: 0 10px 30px rgba(0, 0, 0, 0.55);

  --font-body: ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", Palatino, "Times New Roman", Times, serif;
  --font-ui: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, "Apple Color Emoji",
    "Segoe UI Emoji";
}
```

**Step 2: Make the body background flat**

In the `body { ... }` rule, remove the light `radial-gradient(...)` background shorthand and use a flat background:

```css
body {
  margin: 0;
  color: hsl(var(--text));
  background-color: hsl(var(--bg));
  font-family: var(--font-body);
  text-rendering: optimizeLegibility;
}
```

**Step 3: Run the globals test**

Run:

```bash
npm test -- app/globals.test.ts
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/web/app/globals.css
git commit -m "fix(web): switch to global dark theme tokens"
```

---

### Task 4: Update assistant shell background classes (remove hard-coded hex)

**Files:**

- Modify: `apps/web/app/(app)/assistant/page.tsx`
- Modify: `apps/web/components/claude/claude-workspace.tsx`
- Modify: `apps/web/components/claude/claude-thread.tsx`
- Modify: `apps/web/components/claude/claude-thread-list.tsx`
- Test: `apps/web/components/claude/claude-workspace.test.tsx`

**Step 1: Update the failing test first**

In `apps/web/components/claude/claude-workspace.test.tsx`, change:

```ts
expect(main).toHaveClass("bg-[#F5F5F0]");
```

to:

```ts
expect(main).toHaveClass("bg-bg");
```

**Step 2: Run the test to confirm failure**

Run:

```bash
npm test -- components/claude/claude-workspace.test.tsx
```

Expected: FAIL (until component classes are updated).

**Step 3: Minimal implementation to pass**

Apply these focused background changes:

- `apps/web/app/(app)/assistant/page.tsx`

```tsx
<main className="min-h-dvh bg-bg p-0">
```

- `apps/web/components/claude/claude-workspace.tsx`

```tsx
<section className="relative h-dvh w-full overflow-hidden bg-bg">
```

Mobile drawer background:

```tsx
"bg-bg shadow-[0_1rem_3rem_rgba(0,0,0,0.22)]",
```

- `apps/web/components/claude/claude-thread.tsx`

```tsx
<ThreadPrimitive.Root className="relative flex h-full min-h-0 flex-col items-stretch bg-bg p-4 pt-14 font-serif">
```

- `apps/web/components/claude/claude-thread-list.tsx`

```tsx
<ThreadListPrimitive.Root className="flex h-full flex-col bg-bg">
```

**Step 4: Run web tests**

Run:

```bash
npm test
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/app/(app)/assistant/page.tsx \
  apps/web/components/claude/claude-workspace.tsx \
  apps/web/components/claude/claude-thread.tsx \
  apps/web/components/claude/claude-thread-list.tsx \
  apps/web/components/claude/claude-workspace.test.tsx
git commit -m "fix(web): use theme tokens for assistant backgrounds"
```

---

### Task 5: Set desktop sidebar width to 18rem

**Files:**

- Modify: `apps/web/components/claude/claude-workspace.tsx`
- Test: `apps/web/components/claude/claude-workspace.test.tsx`

**Step 1: Add a failing assertion**

In `apps/web/components/claude/claude-workspace.test.tsx`, add an assertion that the grid class contains the new width token:

```ts
expect(grid!.className).toContain("md:grid-cols-[18rem_minmax(0,1fr)]");
```

**Step 2: Run the test to confirm failure**

Run:

```bash
npm test -- components/claude/claude-workspace.test.tsx
```

Expected: FAIL.

**Step 3: Implement**

In `apps/web/components/claude/claude-workspace.tsx`, change:

```ts
desktopSidebarOpen ? "md:grid-cols-[320px_minmax(0,1fr)]" : "md:grid-cols-1",
```

to:

```ts
desktopSidebarOpen ? "md:grid-cols-[18rem_minmax(0,1fr)]" : "md:grid-cols-1",
```

Optional (mobile drawer width):

```ts
"fixed inset-y-0 left-0 z-50 w-[18rem] max-w-[86vw]",
```

**Step 4: Run tests**

Run:

```bash
npm test -- components/claude/claude-workspace.test.tsx
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/components/claude/claude-workspace.tsx apps/web/components/claude/claude-workspace.test.tsx
git commit -m "fix(web): set assistant sidebar width to 18rem"
```

---

### Task 6: Normalize sidebar icon rail padding to ~16px and add hover for nav items

**Files:**

- Modify: `apps/web/components/claude/claude-thread-list.tsx`
- Test: `apps/web/components/claude/claude-thread-list.test.tsx`

**Step 1: Write a failing test for the New chat row padding**

In `apps/web/components/claude/claude-thread-list.test.tsx`, add:

```ts
const newChat = screen.getByRole("button", { name: "New chat" });
expect(newChat.className).toContain("px-4");
```

**Step 2: Run the test to confirm failure**

Run:

```bash
npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: FAIL.

**Step 3: Implement padding normalization**

In `apps/web/components/claude/claude-thread-list.tsx`:

- Replace `px-3` / nested `px-1` wrappers with a consistent `px-4` rail.
- Ensure nav rows + New chat row have a shared hover fill using tokens (eg, `hover:bg-surface-2/60`).

Example (New chat button):

```tsx
className="flex w-full items-center gap-3 rounded-lg px-4 py-2 font-ui text-sm text-text transition hover:bg-surface-2/60 active:scale-[0.99]"
```

**Step 4: Run tests**

Run:

```bash
npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/components/claude/claude-thread-list.tsx apps/web/components/claude/claude-thread-list.test.tsx
git commit -m "fix(web): normalize sidebar rail padding and hover"
```

---

### Task 7: Add active + hover styling for selected thread rows

**Files:**

- Modify: `apps/web/components/claude/claude-thread-list.tsx`
- Test: `apps/web/components/claude/claude-thread-list.test.tsx`

**Step 1: Adjust the test harness to render a thread item**

Update the `@assistant-ui/react` mock in `apps/web/components/claude/claude-thread-list.test.tsx` so `ThreadListPrimitive.Items` renders one item via the provided `components.ThreadListItem`.

Example mock shape:

```ts
ThreadListPrimitive: {
  // ...
  Items: ({ components }: any) => (
    <div data-testid="thread-items">{components.ThreadListItem({})}</div>
  ),
},
ThreadListItemPrimitive: {
  Root: ({ children, ...props }: any) => (
    <div data-active="true" {...props}>
      {children}
    </div>
  ),
  // ...
},
```

**Step 2: Add a failing assertion for active styling**

After rendering `ClaudeThreadList`, locate the `Untitled` thread trigger and assert its row container includes a `data-[active]:...` class.

Example:

```ts
const trigger = screen.getByRole("button", { name: "Untitled" });
const row = trigger.closest("div[data-active]");
expect(row?.className ?? "").toContain("data-[active]:");
```

**Step 3: Run the test to confirm failure**

Run:

```bash
npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: FAIL.

**Step 4: Implement active + hover styles using assistant-ui state**

Refactor the `ThreadListItem` row to apply hover + active styling on `ThreadListItemPrimitive.Root`.

Example starting point:

```tsx
<ThreadListItemPrimitive.Root
  className="group flex items-center gap-2 rounded-lg px-4 py-2 transition hover:bg-surface-2/60 data-[active]:bg-surface-2"
>
  <ThreadListItemPrimitive.Trigger
    className="min-w-0 flex-1 rounded-md text-left font-ui text-sm text-text outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
  >
    <span className="block truncate">
      <ThreadListItemPrimitive.Title fallback="Untitled" />
    </span>
  </ThreadListItemPrimitive.Trigger>
  {/* actions */}
</ThreadListItemPrimitive.Root>
```

**Step 5: Run tests**

Run:

```bash
npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/web/components/claude/claude-thread-list.tsx apps/web/components/claude/claude-thread-list.test.tsx
git commit -m "fix(web): add active and hover styles to thread rows"
```

---

### Task 8: Tokenize remaining hard-coded colors that break contrast under the new theme

**Files:**

- Modify: `apps/web/components/claude/claude-thread.tsx`
- Modify: `apps/web/app/login/page.tsx`
- Modify: `apps/web/components/assistant-ui/markdown-text.tsx`
- Modify: `apps/web/components/claude/request-approval-tool-ui.tsx`

**Step 1: Run web tests to identify breakages**

Run:

```bash
npm test
```

Expected: PASS (unit tests should remain green); this step is a guardrail before changing many classes.

**Step 2: Replace bright/light-only backgrounds with semantic surfaces**

Examples of replacements:

- `bg-white` / `bg-white/70` -> `bg-surface` / `bg-surface/70`
- `bg-[#f5f5f0]` -> `bg-surface-2`
- `text-[#1a1a18]` -> `text-text`
- `text-[#6b6a68]` -> `text-muted`
- `border-[#00000015]` -> `border-border`
- focus rings -> `focus-visible:ring-accent/30` + `focus-visible:ring-offset-bg`

Do this in small chunks (one file at a time) and re-run `npm test` between files.

**Step 3: Commit per file (or per small group)**

Example:

```bash
git add apps/web/components/claude/claude-thread.tsx
git commit -m "refactor(web): tokenise claude thread surface colors"
```

Repeat for the other files as needed.

---

### Task 9: Capture AFTER Playwright smoke artifacts

**Files:** none

**Step 1: Run `noa-playwright-smoke` again**

- Verify `/login` -> `/assistant` still works.
- Capture a success screenshot to the artifacts directory (eg, `after-assistant.png`).
- Cleanup must run.

Expected: PASS with artifacts recorded.

---

### Task 10: Final verification

**Files:** none

**Step 1: Web tests**

Run:

```bash
cd apps/web
npm test
npm run build
```

Expected: PASS.

**Step 2: API tests (baseline sanity)**

Run:

```bash
cd apps/api
uv run pytest -q
```

Expected: PASS.
