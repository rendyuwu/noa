# CLAUDE.md — BIGSU application rules

This app is built on **BIGSU** (Biznet Gio Standard UI). Follow every rule in
[AGENTS.md](./AGENTS.md) — they are mandatory, not advisory. This file adds the
Claude Code-specific entry points.

## Use the BIGSU skill

The **`bigsu` skill** (`.claude/skills/bigsu/SKILL.md`) contains the full
component catalog (all 45 components with import packages and when-to-use),
the standard blocks (page patterns), and the status vocabulary. Load it before building
or modifying any UI so you pick the right component instead of inventing one.

## Live documentation via MCP

The central BIGSU docs MCP server exposes `list_components`,
`get_component_docs`, `get_block`, `get_tokens`, `list_icons` and `search_docs`.
Prefer those tools for authoritative props, usage rules, and examples — they are
generated from the component sources and cannot drift. The server is configured
per environment; `.mcp.json` is gitignored here, so it is not checked in with
this package.

## Condensed hard rules (details in AGENTS.md)

1. **Imports**: UI only from `@gio/bigsu-ui`, `@gio/bigsu-app-shell`,
   `@gio/bigsu-icons`. Never other component/icon libraries.
2. **Colors**: semantic token utilities or `var(--bigsu-*)` only. Forbidden:
   `bg-green-500`, `text-red-600`, `bg-[#25CC9C]`, inline hex styles.
3. **Icons**: `<BigsuIcon name="..." />` only. Never import `lucide-react`.
4. **Layout**: every page inside `AppFrame` (wraps BIGSU `AppShell`).
4a. **Responsive**: fully responsive — use 100% of the viewport, content fluid
   and reflowing (AppShell default `contentWidth="full"`). Never fixed-width;
   never reimplement the shell's responsive chrome.
5. **One primary action** per page/dialog (`PageHeader` `primaryAction`).
6. **States required**: tables ship loading/empty/error + pagination +
   StatusChips; every form field has a label and Zod-driven error state.
7. **Destructive actions** always confirm through `ConfirmDialog tone="danger"`
   with explicit confirm wording.
8. **Light mode only** — no `dark:` classes anywhere.
9. Statuses: only the 11 standard values via `StatusChip`.
10. No BIGSU component fits? Write a proposal note; never invent a new pattern.

Run the forbidden-patterns checklist at the end of AGENTS.md over your diff
before declaring any UI task done.
