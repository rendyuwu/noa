# Tool Success Linger + Fade-Out Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep successful tool activity rows visible for 1s, then fade/collapse smoothly before unmounting.

**Architecture:** Implement a small state machine in `ClaudeToolFallback` using `useEffect` timers (linger 1000ms + transition 200ms). Apply Tailwind transition classes to an outer wrapper. Use Vitest fake timers to test the delayed unmount.

**Tech Stack:** Next.js + React, Tailwind, Vitest.

---

### Task 1: Add a failing test for delayed unmount

**Files:**
- Modify: `apps/web/components/claude/request-approval-tool-ui.test.tsx`

**Step 1: Write the failing test**

Add a new test that asserts success rows linger and unmount later:

```tsx
import { act } from "react";

it("lingers for 1s then fades out before unmounting", () => {
  vi.useFakeTimers();

  render(
    <ClaudeToolFallback
      toolName="get_current_time"
      toolCallId="tool-call-delay"
      status={{ type: "complete" }}
      result={{ time: "10:00" }}
      isError={false}
    />,
  );

  // visible initially
  expect(screen.getByText(/^Current time$/i)).toBeInTheDocument();

  // still visible during linger
  act(() => {
    vi.advanceTimersByTime(1000);
  });
  expect(screen.getByText(/^Current time$/i)).toBeInTheDocument();

  // after transition window, unmounted
  act(() => {
    vi.advanceTimersByTime(250);
  });
  expect(screen.queryByText(/^Current time$/i)).not.toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run: `cd apps/web && npm test -- components/claude/request-approval-tool-ui.test.tsx`

Expected: FAIL (component currently unmounts immediately on success).

**Step 3: Commit**

```bash
git add apps/web/components/claude/request-approval-tool-ui.test.tsx
git commit -m "test(web): cover delayed tool success fade-out"
```

---

### Task 2: Implement linger + fade-out in ClaudeToolFallback

**Files:**
- Modify: `apps/web/components/claude/request-approval-tool-ui.tsx`

**Step 1: Implement minimal state machine**

In `ClaudeToolFallback`:

- Import hooks:

```tsx
import { useEffect, useRef, useState } from "react";
```

- Add state:

```tsx
const [hideState, setHideState] = useState<"visible" | "exiting" | "hidden">("visible");
const timeouts = useRef<number[]>([]);
```

- On each render, compute `isSuccessfulComplete = statusType === "complete" && !isError`.

- In `useEffect`:
  - Clear existing timers.
  - If not successful complete, set `visible`.
  - If successful complete:
    - set `visible`
    - set timer 1000ms -> set `exiting`
    - set timer 1200ms -> set `hidden`

- Render logic:
  - If `hideState === "hidden"` return `null`.
  - Render the existing one-line row inside an outer wrapper with transitions:

```tsx
const wrapperClass = [
  "overflow-hidden transition-all duration-200 ease-out",
  hideState === "exiting" ? "max-h-0 opacity-0" : "max-h-20 opacity-100",
].join(" ");
```

And optionally add `translate-y` for a slightly nicer dissolve:

```tsx
hideState === "exiting" ? "-translate-y-0.5" : "translate-y-0"
```

**Step 2: Run tests**

Run: `cd apps/web && npm test -- components/claude/request-approval-tool-ui.test.tsx`

Expected: PASS.

**Step 3: Run full web tests**

Run: `cd apps/web && npm test`

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/web/components/claude/request-approval-tool-ui.tsx
git commit -m "feat(web): linger and fade successful tool activity"
```

---

### Task 3: Verify behavior in dev

**Step 1: Run dev server**

Run: `cd apps/web && npm run dev -- --hostname 0.0.0.0 --port 3000`

**Step 2: Manual check**

- Trigger a READ tool (time/date).
- Confirm:
  - Row shows `complete` and stays for ~1s
  - Row fades/collapses smoothly
  - Row disappears fully
