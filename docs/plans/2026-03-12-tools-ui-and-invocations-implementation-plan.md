# Tools UI + Invocation Stability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent tool-call crashes in the assistant transport UI and render compact Claude-style tool activity blocks that explain what tools are doing without showing raw args/results.

**Architecture:** Keep tool execution on the backend. In the web adapter, normalize tool-call parts so `argsText` and `toolCallId` are always present, merge standalone tool-result messages onto their corresponding tool-call parts, and drop internal proposal tool calls. In the Claude UI, update the generic tool fallback renderer to show a compact, human-friendly activity line with status.

**Tech Stack:** Next.js App Router, React 19, `@assistant-ui/react` (Assistant Transport runtime + primitives), Tailwind CSS, Vitest + Testing Library.

---

### Task 0: Create an isolated worktree (recommended)

**Files:** none

**Step 1:** Create a worktree
- Run: `git worktree add ../noa-tools-ui -b fix/tools-ui-and-invocations`
- Expected: new directory `../noa-tools-ui` with a clean checkout

### Task 1: Make conversion logic testable (extract helper)

**Files:**
- Create: `apps/web/components/lib/assistant-transport-converter.ts`
- Modify: `apps/web/components/lib/runtime-provider.tsx`
- Test: `apps/web/components/lib/assistant-transport-converter.test.ts`

**Step 1: Create converter helper (behavior-preserving)**

Create `apps/web/components/lib/assistant-transport-converter.ts` and move the following from `runtime-provider.tsx` into it:

- `type AssistantState`
- `coerceString()`
- `partsToContent()`
- `toThreadMessage()`
- `converter()`

Export:

```ts
export type AssistantState = {
  messages: Array<{ id?: string; role: string; parts: Array<Record<string, unknown>> }>;
  isRunning: boolean;
};

export function convertAssistantState(
  state: AssistantState,
  connectionMetadata: { pendingCommands: Array<any>; isSending: boolean },
) {
  // return { messages, isRunning }
}
```

Initially keep the logic identical so this extraction is a no-op.

**Step 2: Wire runtime provider to use extracted converter**

In `apps/web/components/lib/runtime-provider.tsx`, remove the in-file converter helpers and import `convertAssistantState`.

```ts
import { convertAssistantState } from "@/components/lib/assistant-transport-converter";
```

Then pass it to `useAssistantTransportRuntime({ converter: convertAssistantState, ... })`.

**Step 3: Add a basic smoke test for the converter**

Create `apps/web/components/lib/assistant-transport-converter.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { convertAssistantState } from "@/components/lib/assistant-transport-converter";

describe("convertAssistantState", () => {
  it("converts empty state", () => {
    const converted = convertAssistantState(
      { messages: [], isRunning: false },
      { pendingCommands: [], isSending: false },
    );
    expect(converted.isRunning).toBe(false);
    expect(converted.messages).toEqual([]);
  });
});
```

**Step 4: Run tests**
- Run: `cd apps/web && npm test`
- Expected: PASS

**Step 5: Commit**
- Run:

```bash
git add apps/web/components/lib/runtime-provider.tsx apps/web/components/lib/assistant-transport-converter.ts apps/web/components/lib/assistant-transport-converter.test.ts
git commit -m "refactor(web): extract assistant transport state converter"
```

### Task 2: Prevent tool-call crash by normalizing `toolCallId` + `argsText`

**Files:**
- Modify: `apps/web/components/lib/assistant-transport-converter.ts`
- Test: `apps/web/components/lib/assistant-transport-converter.test.ts`

**Step 1: Write failing test (tool-call always has argsText string)**

Add to `apps/web/components/lib/assistant-transport-converter.test.ts`:

```ts
it("ensures tool-call parts always have argsText", () => {
  const converted = convertAssistantState(
    {
      isRunning: false,
      messages: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool-call",
              toolName: "get_current_time",
              toolCallId: "tool-call-1",
              args: {},
            },
          ],
        },
      ],
    },
    { pendingCommands: [], isSending: false },
  );

  const message = converted.messages[0];
  expect(message?.role).toBe("assistant");
  const toolPart = (message as any)?.content?.find?.((p: any) => p?.type === "tool-call");
  expect(typeof toolPart?.argsText).toBe("string");
  expect(toolPart?.argsText).toBe("{}");
});
```

This should FAIL before implementation if `argsText` is missing.

**Step 2: Run test to verify it fails**
- Run: `cd apps/web && npm test`
- Expected: FAIL with an assertion where `toolPart.argsText` is `undefined`

**Step 3: Implement normalization in `partsToContent()`**

In `apps/web/components/lib/assistant-transport-converter.ts`:

- For `type === "tool-call"`, ensure:
  - `toolCallId` is always present (generate deterministic fallback if missing)
  - `args` is `{}` when missing
  - `argsText` is a stable JSON string (`JSON.stringify(args)`), defaulting to `{}`

Suggested implementation shape:

```ts
const coerceRecord = (value: unknown): Record<string, unknown> | undefined => {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
};

// inside tool-call branch
const toolCallId = coerceString(part.toolCallId) ?? `toolcall-${messageId}-${partIndex}`;
const args = coerceRecord(part.args) ?? {};
const rawArgsText = coerceString(part.argsText);
const argsText = rawArgsText && rawArgsText.trim() ? rawArgsText : JSON.stringify(args);
```

**Step 4: Run tests to verify it passes**
- Run: `cd apps/web && npm test`
- Expected: PASS

**Step 5: Commit**

```bash
git add apps/web/components/lib/assistant-transport-converter.ts apps/web/components/lib/assistant-transport-converter.test.ts
git commit -m "fix(web): always provide argsText for tool calls"
```

### Task 3: Merge standalone tool results and hide proposal tool calls

**Files:**
- Modify: `apps/web/components/lib/assistant-transport-converter.ts`
- Test: `apps/web/components/lib/assistant-transport-converter.test.ts`

**Step 1: Write failing test (tool-result merges onto tool-call)**

Add:

```ts
it("merges tool-result messages onto the matching tool-call part", () => {
  const converted = convertAssistantState(
    {
      isRunning: false,
      messages: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool-call",
              toolName: "get_current_time",
              toolCallId: "tool-call-1",
              args: {},
            },
          ],
        },
        {
          id: "m2",
          role: "tool",
          parts: [
            {
              type: "tool-result",
              toolName: "get_current_time",
              toolCallId: "tool-call-1",
              result: { time: "10:00" },
              isError: false,
            },
          ],
        },
      ],
    },
    { pendingCommands: [], isSending: false },
  );

  expect(converted.messages).toHaveLength(1);
  const toolPart = (converted.messages[0] as any).content.find((p: any) => p.type === "tool-call");
  expect(toolPart.result).toEqual({ time: "10:00" });
  expect(toolPart.isError).toBe(false);
});
```

**Step 2: Write failing test (proposal tool calls are dropped)**

Add:

```ts
it("drops proposal tool calls", () => {
  const converted = convertAssistantState(
    {
      isRunning: false,
      messages: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool-call",
              toolName: "set_demo_flag",
              toolCallId: "proposal-123",
              args: { key: "demo_flag", value: true },
            },
          ],
        },
      ],
    },
    { pendingCommands: [], isSending: false },
  );

  const content = (converted.messages[0] as any).content;
  expect(content.some((p: any) => p.type === "tool-call")).toBe(false);
});
```

**Step 3: Run tests to verify they fail**
- Run: `cd apps/web && npm test`
- Expected: FAIL (message count still 2, proposal part still present)

**Step 4: Implement merge + filtering**

In `apps/web/components/lib/assistant-transport-converter.ts`:

- Preprocess `state.messages` before conversion:
  - Collect tool results from `role === "tool"` messages (`type === "tool-result"`).
  - Remove those tool messages from the output message list.
  - When converting assistant messages, attach `{ result, isError, artifact }` onto the matching
    tool-call part by `toolCallId`.
- In `partsToContent()` (or earlier), skip tool-call parts where `toolCallId` starts with `proposal-`.

**Step 5: Run tests to verify they pass**
- Run: `cd apps/web && npm test`
- Expected: PASS

**Step 6: Commit**

```bash
git add apps/web/components/lib/assistant-transport-converter.ts apps/web/components/lib/assistant-transport-converter.test.ts
git commit -m "fix(web): merge tool results and hide proposal tool calls"
```

### Task 4: Update Claude tool fallback to compact activity blocks (no raw JSON)

**Files:**
- Modify: `apps/web/components/claude/request-approval-tool-ui.tsx`
- Test: `apps/web/components/claude/request-approval-tool-ui.test.tsx`

**Step 1: Write failing UI test (no args/result JSON rendered)**

Create `apps/web/components/claude/request-approval-tool-ui.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClaudeToolFallback } from "@/components/claude/request-approval-tool-ui";

describe("ClaudeToolFallback", () => {
  it("renders a compact activity line without raw args/result", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-1"
        status={{ type: "complete" }}
        argsText='{"secret":"nope"}'
        result={{ time: "10:00" }}
        isError={false}
      />,
    );

    expect(screen.getByText(/current time/i)).toBeInTheDocument();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/10:00/)).not.toBeInTheDocument();
  });
});
```

**Step 2: Run test to verify it fails**
- Run: `cd apps/web && npm test`
- Expected: FAIL (current component renders args/result)

**Step 3: Implement compact + collapsible tool block**

In `apps/web/components/claude/request-approval-tool-ui.tsx`, rewrite `ClaudeToolFallback` to:

- Render a `<details>` with a `<summary>` row.
- Show:
  - humanized tool label
  - status badge (`running`, `complete`, `incomplete`, `requires-action`)
  - a plain-English activity message
- Do NOT render `argsText` or `result`.

Suggested mapping (minimum):

```ts
const TOOL_COPY: Record<string, { label: string; doing: string; done: string }> = {
  get_current_time: {
    label: "Current time",
    doing: "Checking the current time",
    done: "Checked the current time",
  },
  get_current_date: {
    label: "Today's date",
    doing: "Checking today's date",
    done: "Checked today's date",
  },
};
```

**Step 4: Run tests**
- Run: `cd apps/web && npm test`
- Expected: PASS

**Step 5: Commit**

```bash
git add apps/web/components/claude/request-approval-tool-ui.tsx apps/web/components/claude/request-approval-tool-ui.test.tsx
git commit -m "feat(web): show compact tool activity in Claude UI"
```

### Task 5: Manual smoke + build verification

**Step 1: Run web tests**
- Run: `cd apps/web && npm test`
- Expected: PASS

**Step 2: Run web build**
- Run: `cd apps/web && npm run build`
- Expected: build succeeds

**Step 3: Manual smoke in browser**
- Run (dev): `cd apps/web && npm run dev`
- Login and open `/assistant`
- Ask: "What time now?"
- Expected:
  - no console crash
  - tool activity shows a compact "Checking/Checked" message
  - assistant responds with the current time
