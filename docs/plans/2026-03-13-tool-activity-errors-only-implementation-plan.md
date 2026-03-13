# Tool Activity Errors Only Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hide tool activity rows for running/success; show only tool failures (and keep approval UI).

**Architecture:** Update the Claude tool fallback renderer to return `null` unless the tool status is `incomplete` (including `isError=true`). Update unit tests to reflect the new behavior.

**Tech Stack:** Next.js + React, Tailwind, Vitest.

---

### Task 1: Update tests to express errors-only behavior

**Files:**
- Modify: `apps/web/components/claude/request-approval-tool-ui.test.tsx`

**Step 1: Write/adjust tests (red/green as needed)**

- Success complete should render nothing.
- Running/unknown should render nothing.
- Incomplete/error should render the one-line row.

**Step 2: Run tests**

Run: `cd apps/web && npm test -- components/claude/request-approval-tool-ui.test.tsx`

Expected: PASS.

---

### Task 2: Simplify ClaudeToolFallback to errors-only

**Files:**
- Modify: `apps/web/components/claude/request-approval-tool-ui.tsx`

**Step 1: Implement minimal behavior**

- Remove success linger/fade state.
- Ensure `isError === true` forces `statusType = "incomplete"`.
- Return `null` unless `statusType === "incomplete"`.

**Step 2: Run full web tests**

Run: `cd apps/web && npm test`

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/components/claude/request-approval-tool-ui.tsx apps/web/components/claude/request-approval-tool-ui.test.tsx
git commit -m "feat(web): show tool activity only on failure"
```
