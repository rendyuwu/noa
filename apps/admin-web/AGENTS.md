# BIGSU rules for coding agents

This application is built on **BIGSU** (Biznet Gio Standard UI). These rules are
mandatory for any code you generate or modify in **`apps/admin-web`**. They do
not apply anywhere else in this repo: `apps/web-embed` is deliberately not a
BIGSU app (its own ESLint config refuses `@gio/*`), and `apps/api` is Python.

The full documentation lives on the BIGSU docs site; the `bigsu` MCP server
serves it as tools, and machine-readable docs are at `<docs>/llms.txt`.

## Mandatory imports

UI comes from the BIGSU packages — never from other component libraries and
never hand-rolled:

```ts
import { AppShell, PageHeader, Breadcrumb } from '@gio/bigsu-app-shell'
import { Button, Card, DataTable, FormField, StatusChip, ConfirmDialog, bigsuToast } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'
```

`@gio/bigsu-charts` is deliberately not installed — nothing in this panel charts
yet. Adding it is a dependency decision (C2 pins exactly), not an import.

## Layout

- Every page renders inside the shared `AppFrame` (`src/components/app-frame.tsx`),
  which wires BIGSU `AppShell`. Never build a custom layout, sidebar, or top bar.
- Page order: Breadcrumb → PageHeader → optional KPI cards → filter/search →
  main content → detail panel or dialog when needed.

## Responsiveness (mandatory)

- The app is **fully responsive — use 100% of the viewport**. Content is fluid:
  it fills the available width and reflows as the viewport changes. AppShell
  ships this (its default is `contentWidth="full"`); never build a fixed-width
  page and never letterbox content with dead space on wide screens.
- Build with fluid primitives: responsive grids (`sm:grid-cols-2 xl:grid-cols-3`),
  `min-w-0`, flex-wrap. Tables, charts, and cards reflow.
- Do NOT reimplement responsive chrome — the shell already handles the sidebar
  (expanded → 72px rail → mobile drawer) and breakpoint padding.
- The 1440px cap (`contentWidth="standard"`) is opt-in, only for reading-heavy
  prose pages — not operational screens.

## Colors: semantic tokens ONLY

Style exclusively with semantic-token utilities (`bg-surface`, `bg-app`,
`text-text-primary`, `text-text-secondary`, `border-border-default`,
`bg-action-primary`, `text-status-danger`, `bg-status-success-soft`, …) or
`var(--bigsu-*)` CSS variables.

**Forbidden** (these fail lint/review):

```
bg-green-500   text-red-600   border-slate-200      ← raw Tailwind palette
bg-[#25CC9C]   text-[#0F6F57] border-[#25CC9C]      ← hard-coded hex
style={{ color: '#16A34A' }}                        ← inline hex styles
```

## Icons: BigsuIcon only

```tsx
<BigsuIcon name="dashboard" size="md" />   // ✅
import { Home } from 'lucide-react'        // ❌ never
import { HomeIcon } from '@heroicons/...'  // ❌ never
```

Icon-only buttons use `IconButton` with a required `aria-label`.

## Buttons and actions

- Exactly **one primary action** per page (the `PageHeader` `primaryAction`
  slot) and one per dialog. Everything else is `secondary`, `outline`, or `ghost`.
- `variant="destructive"` only for dangerous actions, and every destructive or
  irreversible action **must** confirm through `ConfirmDialog`
  (`tone="danger"`, explicit `confirmLabel` — never just "OK").

## Required states

- Every `DataTable`/list: loading, empty (`emptyState`), and error states, plus
  pagination for long lists, `StatusChip` for statuses, and row actions.
- Every form field: a visible label and an error state via `FormField`
  (placeholder-only inputs are forbidden). Forms validate with Zod through
  `react-hook-form` + `@hookform/resolvers/zod`.
- Statuses use only the 11 standard values rendered through `StatusChip`:
  Draft, Submitted, Pending, In Review, Approved, Rejected, Failed, Completed,
  Archived, Active, Inactive.

## Theme

Light mode only. No `dark:` classes, no theme toggles.

## Forbidden patterns — final checklist

Before finishing any change, verify NONE of these appear in your diff:

- [ ] Raw palette classes (`bg-green-500`, `text-red-600`, `border-zinc-*`, …)
- [ ] Arbitrary hex colors (`bg-[#...]`, `text-[#...]`, inline `style` colors)
- [ ] Direct `lucide-react` / `@heroicons/*` / other icon imports
- [ ] UI components from non-BIGSU libraries (MUI, AntD, Chakra, shadcn copies, …)
- [ ] Custom page chrome instead of `AppFrame`/`AppShell`
- [ ] More than one primary Button per page or dialog
- [ ] Destructive action without `ConfirmDialog`
- [ ] Table without loading/empty/error states
- [ ] Form input without a label or error wiring
- [ ] Any `dark:` class or dark-mode logic
- [ ] Recreated/approximated Biznet Gio logo (use the official SVG assets only)

If no BIGSU component fits, do not invent a new visual pattern — leave a
proposal note for the design-system team instead.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
