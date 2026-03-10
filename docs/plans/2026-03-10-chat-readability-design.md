# Chat Readability + Markdown Tables (Design)

## Problem

The Claude-style chat surface has two readability issues:

- User messages look visually heavy/awkward (spacing and alignment differ from the Claude reference).
- Markdown output can break, especially tables on mobile, where columns collapse and text wraps per-character.

## Goal

Make chat output readable and Claude-like by:

- styling user messages as compact, right-aligned bubbles,
- rendering markdown with stable typography,
- ensuring markdown tables scroll horizontally on mobile (no vertical per-character wrap).

## Root Cause

- The global `.wrap-break-word` utility in `apps/web/app/globals.css` uses `overflow-wrap: anywhere`, which allows aggressive line breaks inside long runs of text. When applied to markdown content containing tables, it can cause table columns to shrink so far that text wraps at nearly every character.
- User messages use the same markdown renderer and inherit paragraph/list spacing rules (eg, `my-2` on `p/ul/ol/...`) in `apps/web/components/assistant-ui/markdown-text.tsx`, which makes the user bubble feel oversized.

## Constraints

- Keep assistant-ui primitives/runtime and the existing Claude-themed surface; changes should be localized to message rendering/styling.
- Preserve code block behavior (code blocks scroll when needed).
- Do not introduce a new design system; match existing Tailwind patterns and warm neutral palette.

## Chosen Approach

Implement a small message-rendering theme:

1. **User message bubble**: render user text as plain text (not markdown) and style as a compact, right-aligned bubble similar to claude.ai.
2. **Markdown theme for assistant output**: keep `MarkdownTextPrimitive` but add element-level styling and table-specific behavior.
3. **Table scrolling**: wrap tables in a horizontally scrollable container (`overflow-x: auto`) and keep table cells from wrapping (`white-space: nowrap`).
4. **Word wrapping**: reduce over-aggressive wrapping by changing `.wrap-break-word` from `overflow-wrap: anywhere` to `overflow-wrap: break-word` (or override inside markdown/table scope) so tables no longer collapse.

## Detailed Design

### User Message Rendering

- Update `apps/web/components/claude/claude-thread.tsx`:
  - Right-align the user bubble (`ml-auto` / flex alignment) to match Claude.
  - Remove the user avatar inside the bubble (Claude’s web UI uses a simple bubble on mobile/most contexts).
  - Render user message text as plain text (preserve newlines with `whitespace-pre-wrap`).
  - Keep the bubble width readable (`max-w-[75ch]`) with tighter padding and reduced vertical rhythm.

Rationale: user messages typically contain raw text; markdown is less important and paragraph margins make the bubble look “bloated”.

### Assistant Markdown Rendering

- Update `apps/web/components/assistant-ui/markdown-text.tsx`:
  - Keep `remark-gfm` enabled.
  - Add a markdown “theme” via className + element overrides:
    - consistent spacing for headings/paragraphs/lists/blockquote,
    - readable inline code styling,
    - code blocks remain scrollable (`pre { overflow: auto; }`).

### Markdown Table Scrolling

- In `apps/web/components/assistant-ui/markdown-text.tsx` (via `components` overrides) or CSS scoped to the markdown root:
  - Wrap `table` in a container with:
    - `overflow-x: auto`, `overflow-y: hidden`, and `-webkit-overflow-scrolling: touch` (mobile momentum),
    - subtle border + radius to match the surface.
  - Style table:
    - `width: max-content` (or similar) so it can be wider than the message column,
    - `white-space: nowrap` for `th, td` to prevent vertical “accordion” rows.

Expected behavior: on narrow screens, users can swipe horizontally inside the table without the layout breaking.

### Word Wrapping Utility

- Update `apps/web/app/globals.css`:
  - Change `.wrap-break-word` to avoid `anywhere` (which breaks tables/cell layout) and use `break-word` instead.
  - Keep `word-break: break-word` for long unbroken strings.

If we need to preserve `anywhere` for other UI, we can scope a safer rule to markdown content instead (eg, apply `anywhere` to `p` but not within `table`).

## Non-Goals

- No redesign of the overall Claude workspace, sidebar, or composer.
- No changes to backend streaming or message data format.
- No introduction of a new markdown renderer library.

## Success Criteria

- User message bubble is compact, right-aligned, and visually consistent with Claude.
- Markdown paragraphs/lists/code blocks render with stable spacing.
- Tables do not collapse into per-character vertical text; tables are horizontally scrollable on mobile.

## Verification

- Manual (mobile + desktop):
  - Send a user message with multiple paragraphs and ensure it stays compact.
  - Render a markdown table wider than the message column; verify horizontal scrolling works and rows stay readable.
  - Verify code blocks scroll and do not overflow the page.
- Build: run `npm run build` in `apps/web`.
