# Login Page Claude Reskin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update `/login` styling to match Claude UI while preserving login behavior.

**Architecture:** Keep the component and its logic intact; only swap layout/styling to Tailwind classes aligned with the existing `/assistant` Claude styling.

**Tech Stack:** Next.js App Router, React client component, Tailwind CSS.

---

### Task 1: Reskin `LoginPage` markup with Tailwind

**Files:**
- Modify: `apps/web/app/login/page.tsx`

**Step 1: Identify UI-only changes**

- Keep `onSubmit` logic and imports as-is (`getApiUrl()`, `setAuthToken`, `setAuthUser`, `router.push`).
- Remove inline styles and legacy class usage (`page-shell`, `panel`, `input`, `button`, `button-primary`).

**Step 2: Update layout + card styling**

- Replace the `main` wrapper with centered Tailwind layout.
- Replace the form container styles with a Claude-like card: warm border, subtle shadow, slight translucency.

**Step 3: Update form fields for a11y**

- Add `id`, `name`, and `autoComplete` for email and password inputs.
- Use `label htmlFor` (not implicit label wrapping only).
- Add `role="alert"` + `aria-live="assertive"` error region.
- Wire `aria-invalid`/`aria-describedby` when `error` exists.

**Step 4: Update focus-visible states**

- Ensure inputs and button have `focus-visible` ring + offset.
- Ensure disabled button has clear disabled styling.

**Step 5: Manual check**

- Load `/login`, tab through controls, verify focus rings.
- Trigger an error (e.g., bad creds) and verify it is visible and announced.

### Task 2: Verify build

**Step 1: Run build**

Run: `npm run build`

Expected: build completes successfully.

### Task 3: Commit

**Step 1: Commit UI change**

Run:

```bash
git add apps/web/app/login/page.tsx
git commit -m "feat(web): reskin login to Claude style"
```
