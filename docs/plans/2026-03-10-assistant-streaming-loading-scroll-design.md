# Assistant Streaming, Loading, and Scroll Design

## Problem

The Claude-style assistant surface has three visible UX failures during a run:

- No explicit loading state appears while waiting for the first assistant token.
- Assistant text does not visibly stream token-by-token; the page appears blank and then the full response arrives at once.
- After the response renders, the chat view can stop scrolling, especially on constrained mobile layouts.

## Goal

Make `/assistant` feel responsive and readable across modern browsers by:

- showing immediate feedback after send,
- rendering assistant text incrementally as it arrives,
- preserving stable scrolling and auto-scroll behavior during and after streaming.

## Root Cause

- `apps/api/src/noa_api/api/routes/assistant.py` returns `DataStreamResponse`, which uses a `text/plain` data stream. That is a likely cause of browser-side buffering instead of incremental paint.
- `apps/web/components/lib/runtime-provider.tsx` uses `useAssistantTransportRuntime` without explicitly switching protocol, so the runtime follows the data-stream default rather than the explicit SSE assistant transport path.
- `apps/web/components/claude/claude-thread.tsx` does not render a dedicated in-progress assistant placeholder before text deltas arrive.
- `apps/web/components/claude/claude-workspace.tsx` and `apps/web/components/claude/claude-thread.tsx` use a fixed-height shell with `overflow-hidden` and missing `min-h-0` layout constraints, which can trap scroll in nested flex/grid containers.

## Chosen Approach

Use a transport-first fix.

- Move the live `/assistant` response path to assistant-transport SSE (`text/event-stream`) instead of the current plain-text data stream.
- Configure the frontend runtime to decode that assistant-transport stream explicitly.
- Add an explicit running assistant placeholder so the UI shows activity before the first token.
- Preserve one stable assistant message while text deltas append into it.
- Repair the thread layout so `ThreadPrimitive.Viewport` is the real scroll container and can keep auto-scroll working.

## Why This Approach

- Targets the most likely root cause of batched response rendering instead of only masking the symptoms.
- Keeps the existing assistant-ui architecture, thread persistence model, tool UI, and command flow intact.
- Improves all three user-visible failures together: first-token feedback, live streaming, and scroll stability.
- Scales better across modern browsers than a UI-only patch or a parallel custom live-stream channel.

## Transport Design

- Keep the current assistant-ui runtime and command contract (`add-message`, `approve-action`, `deny-action`, `add-tool-result`, `threadId`).
- On the backend, return assistant-transport SSE for `/assistant` so the browser receives `text/event-stream` and incremental `data:` events.
- On the frontend, set `useAssistantTransportRuntime(..., { protocol: "assistant-transport" })` so the matching SSE decoder is used deliberately.
- Preserve the existing state-converter pattern in `apps/web/components/lib/runtime-provider.tsx`, including optimistic user messages and final canonical state reload.
- Keep the final persisted state load after the run completes, but avoid any design that waits for that final load before painting streamed text.
- Ensure the Next.js proxy forwards streaming headers/body without buffering or content transformation.

## Loading And First-Token Design

- Create a visible assistant in-progress row as soon as a user send starts.
- Render a lightweight Claude-like loading indicator when the active assistant message is running and has no text yet.
- Reuse the same visual message container when the first text delta arrives so the screen does not flash from blank to full answer.
- Keep the optimistic user message visible immediately after send.
- If the run errors or is cancelled before first token, convert the placeholder into the existing incomplete/error state instead of dropping it.

## Streaming Design

- Stream text into one stable assistant message rather than remounting the whole message list for every delta.
- Preserve message identity across deltas so React updates content in place.
- Avoid index-based fallback IDs for the streaming assistant message when a stable temporary ID can be used.
- Reconcile the final persisted assistant message onto the same conversation without a visible flash when the permanent message ID arrives.
- Keep tool-call and approval parts compatible with the same message pipeline, but do not block text paint on final persistence.

## Scroll And Layout Design

- Make `ThreadPrimitive.Viewport` the only intended scroll container for the thread.
- Add the missing `min-h-0` and `min-w-0` constraints through the workspace grid, thread column, and viewport chain so nested flex/grid children can shrink correctly.
- Keep the composer outside the scrollable viewport but inside the same flex column.
- Use assistant-ui viewport auto-scroll behavior for run start, streamed growth while at bottom, thread initialize, and thread switch.
- Do not force-scroll users who have manually scrolled upward; preserve normal readback behavior and rely on a scroll-to-bottom affordance when needed.
- Keep wide markdown content such as tables constrained inside the message content area so it does not break page-level scrolling.

## Non-Goals

- No redesign of Claude visual styling outside the loading affordance needed for in-progress assistant responses.
- No replacement of assistant-ui primitives or migration to a different chat runtime.
- No change to thread persistence semantics, approval workflows, or backend authorization behavior.

## Validation

- Add focused web tests for the loading placeholder, bottom composer/viewport layout, and no-empty-state behavior after send.
- Add API coverage for assistant transport response format and streaming state transitions if needed.
- Verify manually in modern desktop browsers and mobile Safari/Chrome that:
  - the user message remains visible immediately,
  - the loading indicator appears before first token,
  - assistant text streams incrementally,
  - the thread can scroll during and after long responses,
  - long markdown and table output does not trap scrolling.
- Run `npm test` and `npm run build` in `apps/web`.
- Run `uv run pytest -q tests/test_assistant.py` in `apps/api`.
