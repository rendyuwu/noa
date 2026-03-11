# Sidebar Refresh + Thread History Reliability (Design)

## Problem

The current assistant sidebar misses the clean, icon-led structure of the Claude reference and feels visually heavy in a few places:

- sidebar navigation uses text-only rows instead of symbol-led navigation,
- sidebar typography is too serif-heavy for navigation/history,
- thread rows expose actions awkwardly and do not feel like a real recents list,
- the account footer pulls too much visual weight.

There is also a thread-history reliability bug:

- while a session is live, thread switching works,
- after a refresh or route remount, prior threads come back as `Untitled` and appear non-functional.

## Goal

Deliver a Claude-inspired NOA sidebar that is cleaner and easier to scan while fixing persisted thread reopening so saved conversations remain titled and usable after refreshes/remounts.

## Constraints

- Keep the existing assistant-ui thread list primitives/runtime approach.
- Preserve NOA branding rather than cloning Claude pixel-for-pixel.
- Keep the existing desktop sidebar + mobile drawer structure.
- Use same-origin web API calls only.
- Add regression coverage for both the UI shell and persisted thread behavior.

## Root Cause

### Sidebar UX

- `apps/web/components/claude/claude-thread-list.tsx` currently renders a workable shell, but navigation/history rows are too plain compared with the Claude reference.
- Sidebar text inherits `font-serif` at the root, which makes navigation/history feel less crisp than Claude's UI-font-driven sidebar.
- Thread actions are always modeled around delete, rather than a quieter overflow pattern.

### Thread History Reliability

- Generated titles are only applied in frontend memory inside `apps/web/components/lib/thread-list-adapter.ts`; they are not durably saved, so `/threads` later returns `title: null` and the UI falls back to `Untitled`.
- Existing threads are recreated by `useAssistantTransportRuntime` with an empty `initialState`; assistant-ui does not automatically fetch persisted state for prior threads on mount/switch.
- As a result, after refresh/remount the sidebar only has metadata, not hydrated conversation state, so old chats look empty/dead even though the backend still has their messages.

## Chosen Approach

Implement a medium-scope refresh with two coordinated pieces:

1. **Claude-inspired NOA sidebar polish** for navigation, recents, actions, typography, and footer weight.
2. **Durable thread persistence + runtime hydration** so titles and messages survive refreshes and thread reopening.

This gives the requested UX lift while fixing the root cause instead of papering over symptoms.

## Detailed Design

### 1) Sidebar shell and typography

File: `apps/web/components/claude/claude-thread-list.tsx`

- Keep the existing overall rail width (`~320px`) and mobile drawer behavior.
- Move sidebar navigation/history/meta content to `font-ui`.
- Keep serif emphasis in the main chat surface (`apps/web/components/claude/claude-thread.tsx`) rather than the sidebar.
- Introduce a calmer top section with NOA/assistant branding and tighter row rhythm.

Rationale: the Claude reference uses the sidebar as a UI-navigation surface first, not a reading surface.

### 2) Symbol-led navigation rows

File: `apps/web/components/claude/claude-thread-list.tsx`

- Replace text-only disabled nav items with icon-led rows for:
  - `New chat`
  - `Search`
  - `Customize`
  - `Projects`
  - `Artifacts`
  - `Code`
- Use subtle hover and active fills rather than strong boxed buttons.
- Keep currently unavailable items visually present but clearly secondary.

Reference cues from ` /home/ubuntu/noa/dev/Claude.html `:

- compact row height,
- icon + label alignment,
- restrained hover fills,
- cleaner visual rhythm than the current sidebar.

### 3) Recents list behavior

File: `apps/web/components/claude/claude-thread-list.tsx`

- Treat conversation history as a `Recents` section with tighter spacing and clearer hierarchy.
- Improve active row styling so the selected conversation reads as selected immediately.
- Replace the always-modeled delete affordance with a trailing overflow action pattern that only appears on hover/focus.
- Keep truncation stable and readable for long titles.

Rationale: Claude's history rows feel light because actions are secondary and titles remain the primary signal.

### 4) Footer simplification

File: `apps/web/components/claude/claude-thread-list.tsx`

- Reduce the visual weight of the current bottom card.
- Keep the same functional actions (`Admin`, `Logout`) but present them as a quieter account strip.
- Preserve current auth data usage (`getAuthUser`, `clearAuth`).

### 5) Persist generated titles

Files:

- `apps/api/src/noa_api/api/routes/threads.py`
- `apps/api/tests/test_threads.py`
- `apps/web/components/lib/thread-list-adapter.ts`

- Make thread title generation durable at the backend so the title returned from `/threads/{id}/title` is also saved onto the thread record.
- Keep the adapter streaming/update behavior, but rely on the backend as the source of truth for later `list()` / `fetch()` calls.

Rationale: title durability belongs at the persistence layer, not only in frontend runtime memory.

### 6) Hydrate persisted thread state on reopen

Files:

- backend: add a lightweight persisted-thread state/messages read path near the assistant/thread routes,
- frontend: `apps/web/components/lib/runtime-provider.tsx`

- Add a backend endpoint that returns the canonical saved thread state/messages for an existing thread.
- When an existing persisted thread becomes active after remount/switch, fetch that state and inject it into the assistant runtime with `unstable_loadExternalState(...)` before the thread is treated as empty.
- Keep new-thread behavior unchanged.

Rationale: `useAssistantTransportRuntime` does not auto-load existing history; we need an explicit hydration step for persisted threads.

### 6a) Avoid empty-state flash during thread hydration

Files:

- `apps/web/components/lib/runtime-provider.tsx`
- `apps/web/components/claude/claude-thread.tsx`

- When switching to an existing thread, there is an unavoidable short window where the thread runtime is empty while the persisted state fetch completes.
- Replace the current greeting-style empty landing flash with a neutral Claude-like skeleton placeholder during this hydration window.
- Only show the skeleton when the current thread is not a `new` thread (i.e., existing threads) and hydration is in-flight.
- Keep the greeting-style empty landing for truly new chats.

Rationale: users interpret the greeting landing as "new chat" state; flashing it during thread switches feels glitchy even if the conversation hydrates correctly.

### 7) Error handling

- `401` should continue to follow the existing auth-expiry path.
- Missing/deleted threads should fail cleanly rather than leaving the UI in a stuck state.
- Hydration failures should be logged and should not corrupt the thread list runtime.

## Non-Goals

- No pixel-perfect Claude clone.
- No redesign of the main chat composer/message surface beyond sidebar-adjacent touchpoints.
- No replacement of assistant-ui primitives/runtime architecture.

## Success Criteria

- Sidebar reads as cleaner, symbol-led, and more Claude-inspired while still feeling like NOA.
- Persisted threads retain their generated titles after refresh.
- Reopening a saved thread after refresh/remount restores its saved messages instead of showing an empty thread.
- Desktop and mobile sidebar interactions remain functional.

## Verification

- Web regression test for the refreshed sidebar shell/structure.
- Web regression test proving a persisted thread can be reopened after remount with saved messages.
- API regression test proving generated titles are persisted and returned by later thread-list fetches.
- Verification commands:
  - `npm test` in `apps/web`
  - targeted thread tests in `apps/api`
  - relevant build/typecheck verification after implementation
