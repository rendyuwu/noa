# Assistant Full-Bleed Surface Design

## Problem

The `/assistant` screen currently renders inside a padded page shell and a rounded workspace card. On mobile and desktop, that exposes the body background around the assistant surface, which makes the app feel inset instead of full-screen.

## Goal

Make `/assistant` feel edge-to-edge like Claude on all screen sizes, so the visible screen edge uses the assistant surface color instead of showing a contrasting outer frame.

## Root Cause

- `apps/web/app/(app)/assistant/page.tsx` uses `.page-shell`, which adds outer page padding.
- `apps/web/components/claude/claude-workspace.tsx` wraps the whole assistant in a rounded, bordered, shadowed card with a fixed inset height.

## Chosen Approach

Use a full-bleed workspace on all sizes.

- Replace the padded page shell on `/assistant` with a full-viewport container.
- Remove the outer card styling from `ClaudeWorkspace` so the assistant itself becomes the page surface.
- Keep the internal layout intact: desktop sidebar divider, mobile drawer, thread layout, and existing Claude-style backgrounds.

## Why This Approach

- Matches the Claude reference most closely.
- Removes the visual mismatch at the screen edge instead of masking it.
- Keeps the fix localized to the outer layout, so the internal assistant UI does not need a redesign.

## Non-Goals

- No redesign of the composer, sidebar content, or message styling.
- No route-wide theme changes outside `/assistant`.
- No change to the existing mobile drawer behavior.

## Validation

- Add a focused UI test for the assistant page/workspace wrapper classes.
- Run `npm test` in `apps/web`.
- Run `npm run build` in `apps/web`.
