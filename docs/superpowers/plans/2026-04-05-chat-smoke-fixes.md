# Chat Smoke Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the current assistant chat regressions reproduced in browser smoke testing: duplicate workflow UI, broken thread actions/new chat interactions, duplicate thread entries and send-side effects, stuck conversation restoration after refresh, and missing chain-of-thought rendering.

**Architecture:** Keep assistant-ui as the single source of truth for thread/runtime state. Remove duplicate workflow presentation, make sidebar thread actions non-blocking and layout-safe, make thread initialization and route sync idempotent so first-send and refresh do not create duplicate requests/items, and rewire chain-of-thought rendering to match the assistant-ui 0.12 message/chain-of-thought primitives actually used in this app.

**Tech Stack:** Next.js App Router, React 19, assistant-ui `0.12.x`, Radix Dropdown Menu, Vitest, Testing Library, playwright-cli for smoke verification.

---

## Browser evidence to preserve while fixing

- Smoke prompt: `check rendy accont on whm web2`
- Screenshot: `.playwright-cli/06b-after-new-chat.png`
- Screenshot: `.playwright-cli/07-after-refresh.png`
- Snapshot: `.playwright-cli/04-thread-actions.yaml`
- Snapshot: `.playwright-cli/07-after-refresh.yaml`
- Network symptom: first send creates `POST /api/threads` once, but `POST /api/assistant` twice and `POST /api/threads/:id/title` twice

---

### Task 1: Remove duplicate workflow progress rendering

**Files:**
- Modify: `apps/noa/components/assistant/assistant-workspace.tsx`
- Modify: `apps/noa/components/assistant/workflow-todo-tool-ui.tsx`
- Test: `apps/noa/components/assistant/workflow-todo-tool-ui.test.tsx`
- Create or modify: `apps/noa/components/assistant/assistant-workspace.test.tsx`

- [ ] **Step 1: Write the failing regression test**

Add a workspace-level test proving a workflow update only renders a single workflow progress surface when canonical workflow todos exist.

```tsx
render(<AssistantWorkspace threadId={null} />);
expect(screen.getAllByLabelText("Workflow progress")).toHaveLength(1);
```

- [ ] **Step 2: Run the targeted test and verify it fails for the right reason**

Run: `npm test -- apps/noa/components/assistant/workflow-todo-tool-ui.test.tsx apps/noa/components/assistant/assistant-workspace.test.tsx`

Expected: FAIL because both `WorkflowTodoToolUI` and `WorkflowDock` render the same progress state.

- [ ] **Step 3: Keep one workflow presentation path**

Use `WorkflowDock` as the canonical live workflow surface and stop rendering the duplicate inline progress card from `WorkflowTodoToolUI`.

```tsx
// assistant-workspace.tsx
<RequestApprovalToolUI />
<WorkflowReceiptToolUI />
<RouteThreadSync routeThreadId={threadId} />
<ThreadHeader />
<AssistantLiveDocks />
<ThreadPanel />
```

If `WorkflowTodoToolUI` must stay registered for compatibility, make it return `null` unless it is rendering information that the dock does not already show.

- [ ] **Step 4: Re-run the targeted workflow tests**

Run: `npm test -- apps/noa/components/assistant/workflow-todo-tool-ui.test.tsx apps/noa/components/assistant/assistant-workspace.test.tsx apps/noa/components/assistant/workflow-dock.test.tsx`

Expected: PASS.

---

### Task 2: Fix thread-row overflow, menu layering, and New chat clickability

**Files:**
- Modify: `apps/noa/components/layout/chat-thread-item.tsx`
- Modify: `apps/noa/components/layout/chat-shell.tsx`
- Modify: `apps/noa/components/ui/dropdown-menu.tsx`
- Test: `apps/noa/components/layout/chat-shell.test.tsx`
- Create: `apps/noa/components/layout/chat-thread-item.test.tsx`

- [ ] **Step 1: Write failing sidebar interaction tests**

Add tests that prove:
1. desktop thread actions are non-modal and do not block sibling controls,
2. long titles are clipped within the sidebar row,
3. the row layout reserves explicit space for the action trigger.

```tsx
render(<ChatThreadItem />);
expect(screen.getByLabelText("Thread actions")).toBeInTheDocument();
expect(screen.getByRole("button", { name: /Delete/i })).not.toBeInTheDocument();
```

```tsx
render(<ChatShell user={null}><div>Thread body</div></ChatShell>);
expect(screen.getByLabelText("New chat")).toBeEnabled();
```

- [ ] **Step 2: Run the targeted sidebar tests and verify the red state**

Run: `npm test -- apps/noa/components/layout/chat-shell.test.tsx apps/noa/components/layout/chat-thread-item.test.tsx`

Expected: FAIL because the current row uses absolute positioning and the Radix dropdown still behaves modally.

- [ ] **Step 3: Replace the absolute row layout with a flex layout and make the dropdown non-modal**

Apply these constraints:

```tsx
<ThreadListItemPrimitive.Root className="group/thread mb-1 overflow-hidden rounded-xl">
  <div className="flex min-w-0 items-center gap-1">
    <ThreadListItemPrimitive.Trigger className="min-w-0 flex-1 truncate ...">
      {displayTitle}
    </ThreadListItemPrimitive.Trigger>
    <DropdownMenu modal={false} open={menuOpen} onOpenChange={setMenuOpen}>
      ...
    </DropdownMenu>
  </div>
</ThreadListItemPrimitive.Root>
```

Also tighten the sidebar viewport so horizontal overflow is clipped:

```tsx
<ScrollArea className="h-full px-2 [&_[data-radix-scroll-area-viewport]]:overflow-x-hidden">
```

Prefer menu placement that stays visually attached to the row and does not float across the main canvas.

- [ ] **Step 4: Re-run the targeted sidebar tests**

Run: `npm test -- apps/noa/components/layout/chat-shell.test.tsx apps/noa/components/layout/chat-thread-item.test.tsx`

Expected: PASS.

---

### Task 3: Make thread initialization, first send, and title generation idempotent

**Files:**
- Modify: `apps/noa/components/lib/runtime/runtime-provider.tsx`
- Modify: `apps/noa/components/assistant/assistant-route-thread-sync.tsx`
- Modify: `apps/noa/components/lib/runtime/assistant-thread-state.ts`
- Create: `apps/noa/components/lib/runtime/runtime-provider.test.tsx`
- Modify: `apps/noa/components/assistant/assistant-route-thread-sync.test.tsx`

- [ ] **Step 1: Write failing runtime tests for concurrent thread resolution**

Add a regression test that simulates multiple `body()`/send attempts before the first `initialize()` resolves and asserts only one initialization path and one title-generation path proceed for the same thread.

```tsx
await Promise.all([resolver(), resolver()]);
expect(initializeMock).toHaveBeenCalledTimes(1);
expect(generateTitleMock).toHaveBeenCalledTimes(1);
```

Also add a route-sync test covering the `/assistant -> /assistant/:threadId` transition for the already-active freshly initialized thread.

- [ ] **Step 2: Run the targeted runtime tests and verify they fail**

Run: `npm test -- apps/noa/components/lib/runtime/runtime-provider.test.tsx apps/noa/components/assistant/assistant-route-thread-sync.test.tsx`

Expected: FAIL because concurrent initialization and route sync still race.

- [ ] **Step 3: Add a single-flight guard for thread initialization and title generation**

Implement one in-flight resolver per local main thread so repeated sends reuse the same initialization promise instead of calling `initialize()` again.

```tsx
const initializingThreadIdRef = useRef<string | null>(null);
const initializingPromiseRef = useRef<Promise<string> | null>(null);
```

Use that guard inside `ensureThreadId()` so it returns the existing promise when initialization is already running.

Also make the title-generation dedupe key stable for the persisted thread identity:

```tsx
const generatedTitleKey = item.remoteId ?? item.id;
```

- [ ] **Step 4: Prevent route sync from re-switching to the already-active just-created thread**

Strengthen `RouteThreadSync` so route updates caused by `ThreadUrlSync` do not call `switchToThread()` again when the active thread already represents the same `remoteId` or when the route key was just satisfied.

```tsx
if (normalizedRouteThreadId && activeRemoteId === normalizedRouteThreadId) {
  setRouteError(null);
  return;
}
```

If needed, also harden `getActiveThreadListItem()` usage so it always resolves the active persisted item consistently while the main thread transitions from draft to persisted state.

- [ ] **Step 5: Re-run the targeted runtime tests**

Run: `npm test -- apps/noa/components/lib/runtime/runtime-provider.test.tsx apps/noa/components/assistant/assistant-route-thread-sync.test.tsx`

Expected: PASS.

---

### Task 4: Fix stuck hydration and duplicate visible thread entries on refresh

**Files:**
- Modify: `apps/noa/components/lib/runtime/runtime-provider.tsx`
- Modify: `apps/noa/components/lib/runtime/thread-runtime-state.ts`
- Modify: `apps/noa/components/assistant/assistant-thread-panel.tsx`
- Modify: `apps/noa/components/lib/runtime/thread-list-adapter.ts`
- Test: `apps/noa/components/assistant/assistant-thread-panel.test.tsx`
- Create or modify: `apps/noa/components/lib/runtime/thread-runtime-state.test.ts`
- Modify: `apps/noa/components/lib/runtime/thread-list-adapter.test.ts`

- [ ] **Step 1: Write failing hydration + duplicate-thread tests**

Cover these cases:
1. hydration leaves the blank restore state when persisted messages are loaded,
2. the restore UI exposes a recovery path if hydration stalls or errors,
3. repeated refresh/list cycles do not show the same `remoteId` more than once.

```ts
expect(getThreadRuntimeState({...}).isHydrating).toBe(false);
```

```tsx
expect(screen.getByText("Restoring conversation…")).toBeInTheDocument();
expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument();
```

- [ ] **Step 2: Run the targeted hydration tests and confirm they fail**

Run: `npm test -- apps/noa/components/assistant/assistant-thread-panel.test.tsx apps/noa/components/lib/runtime/thread-runtime-state.test.ts apps/noa/components/lib/runtime/thread-list-adapter.test.ts`

Expected: FAIL because the current hydration state machine can stay in a restoring state forever and the list path does not protect against duplicate visible entries.

- [ ] **Step 3: Tighten hydration completion and expose a recovery UI**

Ensure hydration state clears deterministically after a successful `unstable_loadExternalState(...)` path and add a non-blank failure/retry state when restoring cannot complete.

```tsx
{isHydrating ? <RestoreState retry={retry} /> : null}
```

Do not leave the page as spinner-only forever.

- [ ] **Step 4: Normalize visible thread identity**

Use the persisted `remoteId` as the dedupe identity for visible thread items during list/render synchronization so a refresh or route switch cannot show the same thread more than once.

```ts
const seen = new Set<string>();
```

Apply the normalization at the narrowest layer that prevents duplicate render entries without fighting assistant-ui runtime state.

- [ ] **Step 5: Re-run the targeted hydration tests**

Run: `npm test -- apps/noa/components/assistant/assistant-thread-panel.test.tsx apps/noa/components/lib/runtime/thread-runtime-state.test.ts apps/noa/components/lib/runtime/thread-list-adapter.test.ts`

Expected: PASS.

---

### Task 5: Rewire chain-of-thought rendering to the assistant-ui 0.12 pattern actually used here

**Files:**
- Modify: `apps/noa/components/assistant/assistant-thread-panel.tsx`
- Modify: `apps/noa/components/assistant/assistant-chain-of-thought.tsx`
- Modify: `apps/noa/components/lib/runtime/assistant-transport-converter.ts`
- Test: `apps/noa/components/assistant/assistant-chain-of-thought.test.tsx`
- Test: `apps/noa/components/lib/runtime/assistant-transport-converter.test.ts`

- [ ] **Step 1: Write failing chain-of-thought regression coverage**

Add a test that mirrors the real runtime condition where reasoning parts are present but `aui.chainOfThought.source` is not available, then verify the message still renders a visible Thinking section using the supported assistant-ui primitive path.

```tsx
render(<AssistantMessageWithActions />);
expect(screen.getByRole("button", { name: /Thinking/i })).toBeInTheDocument();
```

- [ ] **Step 2: Run the targeted chain-of-thought tests and confirm red**

Run: `npm test -- apps/noa/components/assistant/assistant-chain-of-thought.test.tsx apps/noa/components/lib/runtime/assistant-transport-converter.test.ts`

Expected: FAIL because the current component returns `null` whenever `aui.chainOfThought.source` is missing in the live scope.

- [ ] **Step 3: Align rendering with the official assistant-ui reasoning/COT pattern for v0.12**

Do not rely on `useAui().chainOfThought.source` being globally available in this app's current message rendering path. Move the Thinking UI to the supported message/chain-of-thought scope and keep `reasoning` parts preserved by the converter.

```tsx
<MessagePrimitive.Parts
  components={{
    ChainOfThought: AssistantChainOfThought,
  }}
/>
```

or the equivalent official primitive composition required by the current assistant-ui version in this repo.

- [ ] **Step 4: Re-run the targeted chain-of-thought tests**

Run: `npm test -- apps/noa/components/assistant/assistant-chain-of-thought.test.tsx apps/noa/components/lib/runtime/assistant-transport-converter.test.ts`

Expected: PASS.

---

### Task 6: Full verification and browser smoke re-run

**Files:**
- No new source files expected

- [ ] **Step 1: Run the focused web test set**

Run:

```bash
npm test -- \
  apps/noa/components/assistant/assistant-workspace.test.tsx \
  apps/noa/components/assistant/workflow-todo-tool-ui.test.tsx \
  apps/noa/components/assistant/workflow-dock.test.tsx \
  apps/noa/components/layout/chat-shell.test.tsx \
  apps/noa/components/layout/chat-thread-item.test.tsx \
  apps/noa/components/lib/runtime/runtime-provider.test.tsx \
  apps/noa/components/lib/runtime/thread-runtime-state.test.ts \
  apps/noa/components/lib/runtime/thread-list-adapter.test.ts \
  apps/noa/components/assistant/assistant-route-thread-sync.test.tsx \
  apps/noa/components/assistant/assistant-thread-panel.test.tsx \
  apps/noa/components/assistant/assistant-chain-of-thought.test.tsx \
  apps/noa/components/lib/runtime/assistant-transport-converter.test.ts
```

Expected: PASS.

- [ ] **Step 2: Run typecheck**

Run: `npm run typecheck`

Expected: PASS.

- [ ] **Step 3: Run browser smoke test against localhost:3000**

Repeat the exact smoke flow:
1. login as `smoke@example.com` with any password,
2. open `/assistant`,
3. send `check rendy accont on whm web2`,
4. verify one workflow progress surface only,
5. open thread actions and confirm Rename/Delete menu appears without blocking New chat,
6. click New chat and confirm the page resets cleanly,
7. refresh an existing thread and confirm it does not stay on `Restoring conversation…`,
8. verify no duplicate thread rows for the same conversation,
9. verify a visible Thinking / chain-of-thought affordance appears when reasoning/tool activity is present.

- [ ] **Step 4: Capture final proof artifacts**

Save updated snapshots/screenshots for the fixed states so the smoke-test evidence can be compared directly with:
- `.playwright-cli/06b-after-new-chat.png`
- `.playwright-cli/07-after-refresh.png`
