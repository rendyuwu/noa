---
name: bigsu
description: BIGSU (Biznet Gio Standard UI) component catalog, blocks (page patterns), and status vocabulary. Use before building or modifying any UI in this app — pick the right BIGSU component instead of inventing one, and follow the blocks exactly.
---
# BIGSU design system

BIGSU be mandatory Biznet Gio inside design system. All UI in app made from 45 component below. Style use semantic-token utility only (see AGENTS.md for hard rule). Docs site (URL in `.env.example` as `BIGSU_DOCS_URL`, MCP connect in `.mcp.json`) have live example, full props table, and do/don't guide for every component at `<docs>/components/<slug>`.

## Component catalog (all 45)

### `@gio/bigsu-app-shell` — application chrome (9)

| Component | When to use |
|---|---|
| `AppShell` | Whole-app frame (sidebar + top bar + main + optional 360px `detailPanel`). Every page live inside — here via `src/components/app-frame.tsx`. |
| `TopCommandBar` | 64px top bar: app name, search trigger, notification, help, UserMenu. Set through AppShell `topBar` prop. |
| `Sidebar` | Left nav (280px) from `NavItem[]` with section, one sublevel max, role filter via `userRoles`, active-item teal mark. |
| `CollapsedSidebar` | Sidebar built-in 72px icon-only mode (mark logo + tooltip) — user-toggle (AppShell remember) and turn on自 between md and lg; below md shell go drawer. Never rebuild responsive. |
| `PageHeader` | Top of every page: breadcrumb, `h1` title, description, ONE `primaryAction`, optional `meta` row (StatusChip, owner, date). |
| `Breadcrumb` | Hierarchy trail above title; usually pass via PageHeader `breadcrumb` prop. |
| `UserMenu` | Signed-in identity dropdown (Authentik-shape claim: name, email, role) with sign-out. TopCommandBar hold it. |
| `EnvironmentBadge` | development / staging / production sign. Opt-in — never add unless team ask. |
| `CommandSearch` | ⌘K command palette (group result) with `useCommandSearchShortcut` hook. |

### `@gio/bigsu-ui` — building blocks (29)

| Component | When to use |
|---|---|
| `Button` | Action. Variant: primary (one per page!), secondary, outline, ghost, destructive; size sm/md/lg; `loading` state. |
| `IconButton` | Icon-only action (take BIGSU icon `name`); type demand `aria-label`. |
| `Card` (+ Header/Title/Description/Content/Footer) | Group content on page: panel, list container, form container. |
| `MetricCard` | Dashboard KPI: label + value + period must, optional delta (direction/positive) and freshness. |
| `Badge` | Generic small label (neutral/success/warning/danger/info/review/brand). For workflow status use StatusChip. |
| `StatusChip` | THE way to draw 11 standard status — fixed color+icon+label map, label always show. |
| `Input` | One-line text field; `prefixIcon`, `suffix` (e.g. unit "IDR"), invalid state from FormField. |
| `Textarea` | Many-line text (justification, note). |
| `Select` | Pick one from list (`options` array or `SelectItem` children). |
| `Checkbox` | Boolean or multi-pick toggle; do indeterminate. |
| `RadioGroup` / `RadioItem` | One choice among few visible option. |
| `DatePicker` | Date pick — calendar popover, `fromDate`/`toDate` bound. Use via RHF `Controller`. |
| `FileUpload` | Drag-drop + browse file zone with `accept`/`maxSizeMb` check and removable file list. Use via `Controller`. |
| `FormField` | Wrap EVERY form control: label, required mark, helper text, error text; wire `aria-describedby`/`aria-invalid`自. |
| `DataTable` | All table list: sort, select + `bulkActions`, `rowActions` menu, `searchable`, `pagination`, loading/`error`/`emptyState`, `dense`, column visibility. |
| `createIdColumn` | Column factory for monospace ID cell (e.g. REQ-2026-0118) in DataTable. |
| `FilterBar` | Search input + filter control (Select, DatePicker) + "Clear filters", put above DataTable. |
| `MultiSelect` | Pick many option from list (filter value, role grant) with chip for pick. |
| `Pagination` | Standalone pager when no use DataTable built-in pagination. |
| `Dialog` | Modal for focus task/detail (Header/Title/Description/Footer compose). |
| `ConfirmDialog` | MUST for destructive/no-undo action: clear `confirmLabel`, `tone="danger"` for delete/reject. |
| `Drawer` | Right-side 360px overlay panel for second detail/edit without leave page. |
| `Toaster` + `bigsuToast` | Feedback after action: `bigsuToast.success/warning/danger/info(title, { description })`. `<Toaster />` sit in `src/app/layout.tsx`. |
| `Tabs` (+ List/Trigger/Content) | Swap view inside one page (underline style). |
| `EmptyState` | "Nothing here yet" for list/table/search result, with optional action. |
| `ErrorState` | Failed data load, with `onRetry`. |
| `LoadingSkeleton` | Loading placeholder (text/circle/rect) while data fetch. |
| `ApprovalStepper` | Approval chain progress: step with approver, completed/current/pending/rejected, timestamp, comment. |
| `Timeline` | Generic time-order event list (dashboard activity feed). |
| `AuditTrail` | No-change actor/action event log with monospace `key=value` metadata — pair with ApprovalStepper on detail page. |

`@gio/bigsu-icons` give `BigsuIcon` — only icon door (43 alias: `dashboard`, `requests`, `approvals`, `create`, `edit`, `delete`, `search`, `filter`, `export`, `statusSuccess`, `statusDanger`, …).

### `@gio/bigsu-charts` — the seven standard charts (7)

Install only when app chart; series color be positional `chart-1`…`chart-6` token (never status color), every chart MUST have `aria-label` plus stated time period and data freshness. No other chart library allowed.

| Component | When to use |
|---|---|
| `LineChart` | Trend over time, position-focus (latency, error rate). |
| `AreaChart` | Volume over time — quantity read from filled area; `stacked` for composition. |
| `BarChart` | Category compare; `orientation="horizontal"`, `stacked` for part-of-whole. |
| `DonutChart` | Composition snapshot, max 6 segment (lump rest into "Other"). |
| `Sparkline` | Tiny no-axis trend inside MetricCard children slot. |
| `ScatterChart` | Correlation between two measure; per-series marker shape. |
| `ComposedChart` | Bar + line on one chart (cost bar under budget line) — one axis only. |

## Responsiveness (mandatory)

Every app fully responsive — **use 100% of viewport**. Content fluid, fill available width, reflow as viewport change. AppShell give this (default `contentWidth="full"`); never build fix-width page, never letterbox with dead space, never rebuild shell responsive chrome (sidebar open → 72px rail → mobile drawer). Use fluid grid/flex and `min-w-0`. 1440px cap (`contentWidth="standard"`) opt-in for read-heavy prose only.

## Page patterns

Full block doc with live example: `<docs>/blocks/<name>`.

- **Page structure** (`page-structure`): AppShell → Breadcrumb → PageHeader (one
  primary action) → optional KPI card → filter/search → main content → detail
  panel/dialog. See every page in `src/app/`.
- **Dashboards** (`dashboards`): MetricCard grid (label/value/period/delta/freshness)
  + drill-down list. No chart component in v1 — build MetricCard + DataTable.
  Example: `src/app/page.tsx`.
- **Tables** (`tables`): FilterBar + DataTable with monospace ID column,
  StatusChip, row action, select + bulk action, pagination, and all three
  data state. Example: `src/app/requests/requests-view.tsx`.
- **Forms** (`forms`): FormField around every control, react-hook-form +
  zodResolver, helper text, clear submit/cancel, `bigsuToast` + redirect on
  win. Example: `src/app/requests/new/new-request-form.tsx`.
- **Approval workflows** (`approval-workflows`): detail page + right detail panel
  with ApprovalStepper + AuditTrail; Approve/Reject through ConfirmDialog.
  Example: `src/app/requests/[id]/page.tsx`.
- **Status & feedback** (`status-feedback`): StatusChip for entity state,
  bigsuToast for action feedback, EmptyState/ErrorState/LoadingSkeleton for data
  state.
- **Navigation** (`navigation`): noun-base nav item, max depth 2, role-aware
  via `roles` on NavItem + `userRoles` on Sidebar. See `src/lib/nav.ts`.

## Status vocabulary (the only 11 statuses)

Draw only through `StatusChip`; never make new status or color:

`Draft`, `Submitted`, `Pending`, `In Review`, `Approved`, `Rejected`,
`Failed`, `Completed`, `Archived`, `Active`, `Inactive`

## Docs site routes (prefix with BIGSU_DOCS_URL)

- `/components` — catalog index; `/components/<slug>` — per-component page
  (example, props API, do/don't, a11y), e.g. `/components/button`,
  `/components/data-table`, `/components/form-field`, `/components/app-shell`
- `/blocks/page-structure`, `/blocks/forms`, `/blocks/tables`,
  `/blocks/dashboards`, `/blocks/approval-workflows`,
  `/blocks/status-feedback`, `/blocks/navigation`
- `/foundations/color`, `/foundations/typography`, `/foundations/iconography`,
  `/foundations/design-tokens`, `/foundations/accessibility`
- `/get-started/agents` — agent onboard; `/llms.txt` — machine-read
  index; `/md/<path>` — markdown version of any page
- MCP endpoint: `/api/mcp/mcp` (already set in `.mcp.json`)