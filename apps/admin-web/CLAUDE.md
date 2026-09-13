# CLAUDE.md — BIGSU application rules

App build on **BIGSU** (Biznet Gio Standard UI). Obey all rule in
[AGENTS.md](./AGENTS.md) — rule mandatory, no advice. This file add
Claude Code door.

## Use the BIGSU skill

**`bigsu` skill** (`.claude/skills/bigsu/SKILL.md`) hold whole
component catalog (all 45 component with import package and when-use),
standard block (page pattern), and status word-list. Load before build
or change any UI so you grab right component, no invent new one.

## Live documentation via MCP

Big BIGSU docs MCP server give `list_components`,
`get_component_docs`, `get_block`, `get_tokens`, `list_icons` and `search_docs`.
Use those tool for true props, usage rule, example — they
born from component source, cannot drift. Server set
per environment; `.mcp.json` gitignored here, so no ship with
this package.

## Condensed hard rules (details in AGENTS.md)

1. **Imports**: UI only from `@gio/bigsu-ui`, `@gio/bigsu-app-shell`,
   `@gio/bigsu-icons`. Never other component/icon library.
2. **Colors**: semantic token util or `var(--bigsu-*)` only. Forbid:
   `bg-green-500`, `text-red-600`, `bg-[#25CC9C]`, inline hex style.
3. **Icons**: `<BigsuIcon name="..." />` only. Never import `lucide-react`.
4. **Layout**: every page live inside `AppFrame` (wrap BIGSU `AppShell`).
4a. **Responsive**: full responsive — use 100% of viewport, content flow
   and reflow (AppShell default `contentWidth="full"`). Never fix-width;
   never rebuild shell responsive chrome.
5. **One primary action** per page/dialog (`PageHeader` `primaryAction`).
6. **States required**: table ship loading/empty/error + pagination +
   StatusChips; every form field got label and Zod-drive error state.
7. **Destructive actions** always confirm through `ConfirmDialog tone="danger"`
   with clear confirm word.
8. **Light mode only** — no `dark:` class anywhere.
9. Statuses: only 11 standard value via `StatusChip`.
10. No BIGSU component fit? Write proposal note; never invent new pattern.

Run forbidden-pattern checklist at end of AGENTS.md over your diff
before you say UI task done.