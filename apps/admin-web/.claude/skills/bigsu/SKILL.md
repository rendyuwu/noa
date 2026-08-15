---
name: bigsu
description: BIGSU (Biznet Gio Standard UI) component catalog, blocks (page patterns), and status vocabulary. Use before building or modifying any UI in this app — pick the right BIGSU component instead of inventing one, and follow the blocks exactly.
---

# BIGSU design system

BIGSU is the mandatory Biznet Gio internal design system. All UI in this app is
composed from the 45 components below. Styling uses semantic-token utilities
only (see AGENTS.md for the hard rules). The docs site (URL in `.env.example`
as `BIGSU_DOCS_URL`, MCP connection in `.mcp.json`) has live examples, full
props tables, and do/don't guidance for every component at
`<docs>/components/<slug>`.

## Component catalog (all 45)

### `@gio/bigsu-app-shell` — application chrome (9)

| Component | When to use |
|---|---|
| `AppShell` | The whole-app frame (sidebar + top bar + main + optional 360px `detailPanel`). Every page lives inside it — here via `src/components/app-frame.tsx`. |
| `TopCommandBar` | 64px top bar: app name, search trigger, notifications, help, UserMenu. Configured through AppShell's `topBar` prop. |
| `Sidebar` | Left navigation (280px) from `NavItem[]` with sections, one sublevel max, role filtering via `userRoles`, active-item teal indicator. |
| `CollapsedSidebar` | The Sidebar's built-in 72px icon-only mode (mark logo + tooltips) — user-toggled (persisted by AppShell) and engaged automatically between md and lg; below md the shell switches to a drawer. Never reimplement responsiveness. |
| `PageHeader` | Top of every page: breadcrumb, `h1` title, description, ONE `primaryAction`, optional `meta` row (StatusChip, owner, dates). |
| `Breadcrumb` | Hierarchy trail above the title; usually passed via PageHeader's `breadcrumb` prop. |
| `UserMenu` | Signed-in identity dropdown (Authentik-shaped claims: name, email, roles) with sign-out. Embedded by TopCommandBar. |
| `EnvironmentBadge` | development / staging / production indicator. Opt-in — never add it unless the team asks for it. |
| `CommandSearch` | ⌘K command palette (grouped results) with the `useCommandSearchShortcut` hook. |

### `@gio/bigsu-ui` — building blocks (29)

| Component | When to use |
|---|---|
| `Button` | Actions. Variants: primary (one per page!), secondary, outline, ghost, destructive; sizes sm/md/lg; `loading` state. |
| `IconButton` | Icon-only action (takes a BIGSU icon `name`); `aria-label` is required by the types. |
| `Card` (+ Header/Title/Description/Content/Footer) | Grouping content on a page: panels, list containers, form containers. |
| `MetricCard` | Dashboard KPI: label + value + period required, optional delta (direction/positive) and freshness. |
| `Badge` | Generic small label (neutral/success/warning/danger/info/review/brand). For workflow statuses use StatusChip instead. |
| `StatusChip` | THE way to render the 11 standard statuses — fixed color+icon+label mapping, label always visible. |
| `Input` | Single-line text field; `prefixIcon`, `suffix` (e.g. unit "IDR"), invalid state from FormField. |
| `Textarea` | Multi-line text (justifications, notes). |
| `Select` | Pick one from a list (`options` array or `SelectItem` children). |
| `Checkbox` | Boolean or multi-select toggles; supports indeterminate. |
| `RadioGroup` / `RadioItem` | Exclusive choice among few visible options. |
| `DatePicker` | Date selection — calendar popover, `fromDate`/`toDate` bounds. Use via RHF `Controller`. |
| `FileUpload` | Drag-drop + browse file zone with `accept`/`maxSizeMb` validation and removable file list. Use via `Controller`. |
| `FormField` | Wraps EVERY form control: label, required marker, helper text, error text; wires `aria-describedby`/`aria-invalid` automatically. |
| `DataTable` | All tabular lists: sorting, selection + `bulkActions`, `rowActions` menu, `searchable`, `pagination`, loading/`error`/`emptyState`, `dense`, column visibility. |
| `createIdColumn` | Column factory for monospace ID cells (e.g. REQ-2026-0118) in DataTable. |
| `FilterBar` | Search input + filter controls (Selects, DatePickers) + "Clear filters", placed above a DataTable. |
| `MultiSelect` | Choosing several options from a list (filter values, role grants) with chips for the selection. |
| `Pagination` | Standalone pager when not using DataTable's built-in pagination. |
| `Dialog` | Modal for focused tasks/details (Header/Title/Description/Footer composition). |
| `ConfirmDialog` | MANDATORY for destructive/irreversible actions: explicit `confirmLabel`, `tone="danger"` for deletes/rejects. |
| `Drawer` | Right-side 360px overlay panel for secondary detail/edit without leaving the page. |
| `Toaster` + `bigsuToast` | Feedback after actions: `bigsuToast.success/warning/danger/info(title, { description })`. `<Toaster />` is mounted in `src/app/layout.tsx`. |
| `Tabs` (+ List/Trigger/Content) | Switching views within one page (underline style). |
| `EmptyState` | "Nothing here yet" for lists/tables/search results, with optional action. |
| `ErrorState` | Failed data loads, with `onRetry`. |
| `LoadingSkeleton` | Loading placeholders (text/circle/rect) while data fetches. |
| `ApprovalStepper` | Approval chain progress: steps with approver, completed/current/pending/rejected, timestamps, comments. |
| `Timeline` | Generic chronological event list (dashboard activity feeds). |
| `AuditTrail` | Immutable actor/action event log with monospace `key=value` metadata — pairs with ApprovalStepper on detail pages. |

`@gio/bigsu-icons` provides `BigsuIcon` — the only icon entry point
(43 aliases: `dashboard`, `requests`, `approvals`, `create`, `edit`, `delete`,
`search`, `filter`, `export`, `statusSuccess`, `statusDanger`, …).

### `@gio/bigsu-charts` — the seven standard charts (7)

Install only when the app charts; series colors are the positional `chart-1`…`chart-6`
tokens (never status colors), every chart REQUIRES `aria-label` plus a stated time
period and data freshness. No other charting library is permitted.

| Component | When to use |
|---|---|
| `LineChart` | Trends over time, position-focused (latency, error rate). |
| `AreaChart` | Volume over time — quantity reads from the filled area; `stacked` for composition. |
| `BarChart` | Categorical comparison; `orientation="horizontal"`, `stacked` for parts-of-whole. |
| `DonutChart` | Composition snapshot, max 6 segments (aggregate the rest into “Other”). |
| `Sparkline` | Tiny axis-less trend inside a MetricCard's children slot. |
| `ScatterChart` | Correlation between two measures; per-series marker shapes. |
| `ComposedChart` | Bars + line on one chart (cost bars under a budget line) — single axis only. |

## Responsiveness (mandatory)

Every app is fully responsive — **use 100% of the viewport**. Content is fluid,
fills the available width, and reflows as the viewport changes. AppShell ships
this (default `contentWidth="full"`); never build fixed-width pages, never
letterbox with dead space, and never reimplement the shell's responsive chrome
(sidebar expanded → 72px rail → mobile drawer). Use fluid grids/flex and
`min-w-0`. The 1440px cap (`contentWidth="standard"`) is opt-in for reading-heavy
prose only.

## Page patterns

Full block docs with live examples: `<docs>/blocks/<name>`.

- **Page structure** (`page-structure`): AppShell → Breadcrumb → PageHeader (one
  primary action) → optional KPI cards → filter/search → main content → detail
  panel/dialog. See every page in `src/app/`.
- **Dashboards** (`dashboards`): MetricCard grid (label/value/period/delta/freshness)
  + drill-down lists. No chart component in v1 — compose MetricCards + DataTable.
  Example: `src/app/page.tsx`.
- **Tables** (`tables`): FilterBar + DataTable with monospace ID column,
  StatusChips, row actions, selection + bulk actions, pagination, and all three
  data states. Example: `src/app/requests/requests-view.tsx`.
- **Forms** (`forms`): FormField around every control, react-hook-form +
  zodResolver, helper text, explicit submit/cancel, `bigsuToast` + redirect on
  success. Example: `src/app/requests/new/new-request-form.tsx`.
- **Approval workflows** (`approval-workflows`): detail page + right detail panel
  with ApprovalStepper + AuditTrail; Approve/Reject through ConfirmDialog.
  Example: `src/app/requests/[id]/page.tsx`.
- **Status & feedback** (`status-feedback`): StatusChip for entity states,
  bigsuToast for action feedback, EmptyState/ErrorState/LoadingSkeleton for data
  states.
- **Navigation** (`navigation`): noun-based nav items, max depth 2, role-aware
  via `roles` on NavItem + `userRoles` on the Sidebar. See `src/lib/nav.ts`.

## Status vocabulary (the only 11 statuses)

Render exclusively through `StatusChip`; never invent new statuses or colors:

`Draft`, `Submitted`, `Pending`, `In Review`, `Approved`, `Rejected`,
`Failed`, `Completed`, `Archived`, `Active`, `Inactive`

## Docs site routes (prefix with BIGSU_DOCS_URL)

- `/components` — catalog index; `/components/<slug>` — per-component page
  (examples, props API, do/don't, a11y), e.g. `/components/button`,
  `/components/data-table`, `/components/form-field`, `/components/app-shell`
- `/blocks/page-structure`, `/blocks/forms`, `/blocks/tables`,
  `/blocks/dashboards`, `/blocks/approval-workflows`,
  `/blocks/status-feedback`, `/blocks/navigation`
- `/foundations/color`, `/foundations/typography`, `/foundations/iconography`,
  `/foundations/design-tokens`, `/foundations/accessibility`
- `/get-started/agents` — agent onboarding; `/llms.txt` — machine-readable
  index; `/md/<path>` — markdown variant of any page
- MCP endpoint: `/api/mcp/mcp` (already configured in `.mcp.json`)
