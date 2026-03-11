# Thread Hydration Skeleton Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop flashing the greeting landing while switching to an existing thread by showing a neutral Claude-like skeleton until persisted state hydration finishes.

**Architecture:** Track “hydration in-flight” for the active `remoteId` inside `NoaAssistantRuntimeProvider`, expose it via a small context/hook, and render a skeleton inside `ClaudeThread` only when an existing thread is empty because hydration is still pending.

**Tech Stack:** Next.js (`apps/web`), `@assistant-ui/react`, Tailwind, Vitest + Testing Library.

---

### Task 1: Add a failing web test for the hydration skeleton

**Files:**
- Test: `apps/web/components/claude/claude-thread.test.tsx`

**Step 1: Write the failing test**

Add a test that simulates an empty, existing thread with hydration in-flight and asserts the greeting does not render.

```tsx
it("shows a skeleton placeholder while hydrating an existing thread", () => {
  mockThreadIsEmpty = true;
  mockThreadListItemStatus = "regular";
  mockIsHydrating = true;

  render(<ClaudeThread />);

  expect(screen.getByLabelText("Loading conversation")).toBeInTheDocument();
  expect(screen.queryByText(/Morning, Casey/)).not.toBeInTheDocument();
});
```

**Step 2: Run the test to verify it fails**

Run from `apps/web`:

```bash
npm test -- components/claude/claude-thread.test.tsx
```

Expected: FAIL because there is no skeleton yet.

### Task 2: Expose hydration state to the thread UI

**Files:**
- Create: `apps/web/components/lib/thread-hydration.tsx`
- Modify: `apps/web/components/lib/runtime-provider.tsx`

**Step 1: Create a minimal context + hook**

Create `apps/web/components/lib/thread-hydration.tsx`:

```tsx
"use client";

import type { PropsWithChildren } from "react";
import { createContext, useContext } from "react";

type ThreadHydrationState = {
  isHydrating: boolean;
};

const ThreadHydrationContext = createContext<ThreadHydrationState>({ isHydrating: false });

export function ThreadHydrationProvider({
  isHydrating,
  children,
}: PropsWithChildren<{ isHydrating: boolean }>) {
  return (
    <ThreadHydrationContext.Provider value={{ isHydrating }}>
      {children}
    </ThreadHydrationContext.Provider>
  );
}

export function useThreadHydration() {
  return useContext(ThreadHydrationContext);
}
```

**Step 2: Wire provider value from the existing hydration effect**

In `apps/web/components/lib/runtime-provider.tsx`:

- Track `hydratedRemoteId` in React state so failures can end hydration (and remove the skeleton) deterministically.
- Derive `shouldHydrate` synchronously from `remoteId`, `messageCount`, and `hydratedRemoteId` so the skeleton can appear on the first render after a thread switch.
- Wrap `children` with `ThreadHydrationProvider`.

**Step 3: Run web tests**

```bash
npm test -- components/lib/runtime-provider.test.tsx
```

Expected: PASS.

### Task 3: Render a Claude-like skeleton during hydration

**Files:**
- Modify: `apps/web/components/claude/claude-thread.tsx`
- Test: `apps/web/components/claude/claude-thread.test.tsx`

**Step 1: Add a small skeleton component**

- Add a `ThreadHydrationSkeleton` that uses subtle `animate-pulse` bars and a composer-shaped placeholder.
- Include `aria-label="Loading conversation"` on the skeleton wrapper for testability.

**Step 2: Swap the empty landing during hydration**

- Read `threadListItem.status` via `useAssistantState`.
- Read `isHydrating` via `useThreadHydration()`.
- Inside `ThreadPrimitive.Empty`, render the skeleton when `isHydrating` is true and the thread is not `new`.

**Step 3: Run the thread tests**

```bash
npm test -- components/claude/claude-thread.test.tsx
```

Expected: PASS.

### Task 4: Full verification

**Step 1: Run the web test suite**

```bash
npm test
```

Expected: PASS.

**Step 2: Run the web production build**

```bash
npm run build
```

Expected: PASS.

### Task 5: Commit

```bash
git add \
  docs/plans/2026-03-11-sidebar-thread-history-design.md \
  docs/plans/2026-03-11-thread-hydration-skeleton.md \
  apps/web/components/lib/thread-hydration.tsx \
  apps/web/components/lib/runtime-provider.tsx \
  apps/web/components/claude/claude-thread.tsx \
  apps/web/components/claude/claude-thread.test.tsx

git commit -m "fix(web): show skeleton during thread hydration"
```
