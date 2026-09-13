# BIGSU rules for coding agents

App build on **BIGSU** (Biznet Gio Standard UI). Rules MANDATORY for code you make or change in **`apps/admin-web`**. Rules no work other place in repo: `apps/web-embed` on purpose not BIGSU app (own ESLint config say NO to `@gio/*`), and `apps/api` be Python.

Full doc live on BIGSU docs site; `bigsu` MCP server give it as tools, machine-read doc at `<docs>/llms.txt`.

## Mandatory imports

UI come from BIGSU packages — never other component library, never hand-make:

```ts
import { AppShell, PageHeader, Breadcrumb } from '@gio/bigsu-app-shell'
import { Button, Card, DataTable, FormField, StatusChip, ConfirmDialog, bigsuToast } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'
```

`@gio/bigsu-charts` on purpose not install — nothing in panel make chart yet. Add it be dependency decision — pins be exact — not import.

## Layout

- Every page live inside shared `AppFrame` (`src/components/app-frame.tsx`), which wire BIGSU `AppShell`. Never build own layout, sidebar, top bar.
- Page order: Breadcrumb → PageHeader → maybe KPI cards → filter/search → main content → detail panel or dialog when need.

## Responsiveness (mandatory)

- App be **fully responsive — use 100% of viewport**. Content flow: fill width, reflow when viewport change. AppShell give this (default `contentWidth="full"`); never build fixed-width page, never letterbox content with dead space on wide screen.
- Build with fluid primitives: responsive grid (`sm:grid-cols-2 xl:grid-cols-3`), `min-w-0`, flex-wrap. Table, chart, card reflow.
- Do NOT rebuild responsive chrome — shell already do sidebar (expanded → 72px rail → mobile drawer) and breakpoint padding.
- 1440px cap (`contentWidth="standard"`) be opt-in, only for read-heavy prose page — not work screen.

## Colors: semantic tokens ONLY

Style only with semantic-token utility (`bg-surface`, `bg-app`, `text-text-primary`, `text-text-secondary`, `border-border-default`, `bg-action-primary`, `text-status-danger`, `bg-status-success-soft`, …) or `var(--bigsu-*)` CSS variable.

**Forbidden** (these break lint/review):

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

Icon-only button use `IconButton` with required `aria-label`.

## Buttons and actions

- Exactly **one primary action** per page (`PageHeader` `primaryAction` slot) and one per dialog. All else be `secondary`, `outline`, or `ghost`.
- `variant="destructive"` only for danger action, and every destructive or no-undo action **must** confirm through `ConfirmDialog` (`tone="danger"`, explicit `confirmLabel` — never just "OK").

## Required states

- Every `DataTable`/list: loading, empty (`emptyState`), error state, plus pagination for long list, `StatusChip` for status, and row action.
- Every form field: visible label and error state via `FormField` (placeholder-only input forbidden). Form validate with Zod through `react-hook-form` + `@hookform/resolvers/zod`.
- Status use only 11 standard value render through `StatusChip`: Draft, Submitted, Pending, In Review, Approved, Rejected, Failed, Completed, Archived, Active, Inactive.

## Theme

Light mode only. No `dark:` class, no theme toggle.

## Forbidden patterns — final checklist

Before you finish any change, check NONE of these in your diff:

- [ ] Raw palette class (`bg-green-500`, `text-red-600`, `border-zinc-*`, …)
- [ ] Any hex color (`bg-[#...]`, `text-[#...]`, inline `style` color)
- [ ] Direct `lucide-react` / `@heroicons/*` / other icon import
- [ ] UI component from non-BIGSU library (MUI, AntD, Chakra, shadcn copy, …)
- [ ] Own page chrome instead of `AppFrame`/`AppShell`
- [ ] More than one primary Button per page or dialog
- [ ] Destructive action with no `ConfirmDialog`
- [ ] Table with no loading/empty/error state
- [ ] Form input with no label or error wiring
- [ ] Any `dark:` class or dark-mode logic
- [ ] Remade/guessed Biznet Gio logo (use official SVG asset only)

If no BIGSU component fit, do not invent new visual pattern — leave proposal note for design-system team instead.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version got breaking change — API, convention, file structure all maybe differ from your training data. Read right guide in `node_modules/next/dist/docs/` (resolve from this file directory; in monorepo the `next` package maybe not visible from repo root) before you write any code. Heed deprecation notice.

This block get write and re-add by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Remove it from diff only make uncommitted change again; commit it with your work keep tree clean.

<!-- END:nextjs-agent-rules -->