# Assistant Full-Bleed Surface Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `/assistant` render edge-to-edge on all screen sizes so the assistant surface reaches the screen edge instead of sitting inside a framed card.

**Architecture:** Keep the existing Claude workspace structure, but move the full-screen surface responsibility to the route container and workspace root. Only the outer shell changes; the internal thread, sidebar, and drawer layout stay intact.

**Tech Stack:** Next.js App Router, React, Tailwind v4 utilities, Vitest, Testing Library

---

### Task 1: Add failing layout tests for the full-bleed shell

**Files:**
- Create: `apps/web/components/claude/claude-workspace.test.tsx`
- Modify: `apps/web/app/(app)/assistant/page.tsx`

**Step 1: Write the failing test**

Create a focused test that renders `ClaudeWorkspace` and asserts the outer workspace wrapper no longer includes framed-card classes like `rounded-2xl`, `border`, or `shadow`, and does include a full-height class like `h-dvh`.

Create a focused test for `AssistantPage` that asserts the route wrapper no longer uses `page-shell` and instead renders a full-viewport assistant surface container.

**Step 2: Run test to verify it fails**

Run: `npm test -- claude-workspace.test.tsx`

Expected: FAIL because the workspace still uses the inset card shell and the page still uses `page-shell`.

**Step 3: Write minimal implementation**

Update `apps/web/app/(app)/assistant/page.tsx` to use a full-viewport `main` wrapper with the assistant surface background.

Update `apps/web/components/claude/claude-workspace.tsx` so the top-level workspace container becomes the full-bleed surface and removes the rounded/bordered/shadowed card treatment.

**Step 4: Run test to verify it passes**

Run: `npm test -- claude-workspace.test.tsx`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/app/(app)/assistant/page.tsx apps/web/components/claude/claude-workspace.tsx apps/web/components/claude/claude-workspace.test.tsx
git commit -m "fix(web): make assistant workspace full-bleed"
```

### Task 2: Run full verification for the assistant route

**Files:**
- Verify only: `apps/web/app/(app)/assistant/page.tsx`
- Verify only: `apps/web/components/claude/claude-workspace.tsx`
- Verify only: `apps/web/components/claude/claude-workspace.test.tsx`

**Step 1: Run the full web test suite**

Run: `npm test`

Expected: PASS with all web tests green.

**Step 2: Run the production build**

Run: `npm run build`

Expected: PASS and `/assistant` is listed in the generated routes.

**Step 3: Check git status**

Run: `git status --short`

Expected: only the intended route/workspace/test changes remain.

**Step 4: Commit verification follow-up if needed**

If verification required any code adjustment, commit it with a focused message.
