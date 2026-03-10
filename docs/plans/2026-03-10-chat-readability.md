# Chat Readability + Markdown Tables Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make user messages look Claude-like and fix broken markdown table rendering by preventing table column collapse and enabling horizontal swipe/scroll on mobile.

**Architecture:** Keep assistant-ui primitives and `@assistant-ui/react-markdown` (`MarkdownTextPrimitive`). Use a small markdown theme (CSS/classes + element overrides) and a right-aligned, plain-text user bubble. Avoid `overflow-wrap: anywhere` so tables keep sane intrinsic widths.

**Tech Stack:** Next.js (apps/web), Tailwind v4, `@assistant-ui/react`, `@assistant-ui/react-markdown`, `remark-gfm`, Vitest + Testing Library.

---

### Task 1: Add a regression test for wrap-break-word

**Files:**
- Create: `apps/web/app/globals.test.ts`

**Step 1: Write the failing test**

Create `apps/web/app/globals.test.ts`:

```ts
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const dirname = path.dirname(fileURLToPath(import.meta.url));

describe("globals.css", () => {
  it("does not use overflow-wrap:anywhere for wrap-break-word (tables must not collapse)", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");

    expect(css).not.toMatch(
      /\.wrap-break-word\s*\{[\s\S]*overflow-wrap:\s*anywhere\s*;/,
    );
    expect(css).toMatch(
      /\.wrap-break-word\s*\{[\s\S]*overflow-wrap:\s*break-word\s*;/,
    );
  });
});
```

**Step 2: Run test to verify it fails**

Run (from `apps/web`):

```bash
npm test -- app/globals.test.ts
```

Expected: FAIL because `.wrap-break-word` currently uses `overflow-wrap: anywhere`.

**Step 3: Commit the failing test**

```bash
git add apps/web/app/globals.test.ts
git commit -m "test(web): cover wrap-break-word overflow-wrap"
```

### Task 2: Fix wrap-break-word to avoid collapsing tables

**Files:**
- Modify: `apps/web/app/globals.css`
- Test: `apps/web/app/globals.test.ts`

**Step 1: Make the test pass (minimal change)**

Update `.wrap-break-word` in `apps/web/app/globals.css`:

```css
.wrap-break-word {
  overflow-wrap: break-word;
  word-break: break-word;
}
```

Notes:
- The key change is `overflow-wrap: break-word` (NOT `anywhere`) because `anywhere` affects min-content sizing and can collapse table columns.

**Step 2: Run test to verify it passes**

Run (from `apps/web`):

```bash
npm test -- app/globals.test.ts
```

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/app/globals.css
git commit -m "fix(web): avoid aggressive anywhere wrapping"
```

### Task 3: Add a regression test for markdown table scrolling overrides

**Files:**
- Create: `apps/web/components/assistant-ui/markdown-text.test.tsx`

**Step 1: Write the failing test**

Create `apps/web/components/assistant-ui/markdown-text.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let lastProps: any = null;

vi.mock("@assistant-ui/react-markdown", () => {
  return {
    MarkdownTextPrimitive: (props: any) => {
      lastProps = props;
      return null;
    },
  };
});

import { MarkdownText } from "./markdown-text";

describe("MarkdownText", () => {
  it("provides a horizontally scrollable table wrapper", () => {
    render(<MarkdownText />);

    expect(lastProps?.components?.table).toBeTypeOf("function");

    const Table = lastProps.components.table;
    const { container } = render(
      <Table>
        <tbody>
          <tr>
            <td>Hello</td>
          </tr>
        </tbody>
      </Table>,
    );

    const scroll = container.querySelector("[data-testid='md-table-scroll']");
    expect(scroll).toBeInTheDocument();
    expect(scroll).toHaveClass("overflow-x-auto");
  });
});
```

**Step 2: Run test to verify it fails**

Run (from `apps/web`):

```bash
npm test -- components/assistant-ui/markdown-text.test.tsx
```

Expected: FAIL because `MarkdownText` does not yet pass `components.table`.

**Step 3: Commit the failing test**

```bash
git add apps/web/components/assistant-ui/markdown-text.test.tsx
git commit -m "test(web): cover markdown table scroll wrapper"
```

### Task 4: Implement markdown table horizontal scrolling + theme tweaks

**Files:**
- Modify: `apps/web/components/assistant-ui/markdown-text.tsx`
- Test: `apps/web/components/assistant-ui/markdown-text.test.tsx`

**Step 1: Implement minimal code to pass the test**

Update `apps/web/components/assistant-ui/markdown-text.tsx` to pass a `components` override for `table`:

```tsx
"use client";

import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

import "@assistant-ui/react-markdown/styles/dot.css";

export const MarkdownText = (_props: any) => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      components={{
        table: ({ className, ...props }) => (
          <div
            data-testid="md-table-scroll"
            className="my-2 w-full overflow-x-auto overflow-y-hidden rounded-xl border border-[#00000015] bg-white/60 shadow-sm backdrop-blur-sm dark:border-[#6c6a6040] dark:bg-[#1f1e1b]/40"
          >
            <table
              {...props}
              className={[
                "w-max min-w-full border-collapse text-sm",
                "[&_th]:whitespace-nowrap [&_td]:whitespace-nowrap",
                "[&_th]:px-3 [&_td]:px-3 [&_th]:py-2 [&_td]:py-2",
                "[&_th]:text-left [&_th]:font-semibold",
                "[&_tr]:border-b [&_tr]:border-[#00000010] dark:[&_tr]:border-[#6c6a6040]",
                className ?? "",
              ].join(" ")}
            />
          </div>
        ),
      }}
      className={[
        // Vertical rhythm
        "[&_:is(p,ul,ol,pre,blockquote,table)]:my-2",
        // Code blocks
        "[&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-[#00000015] [&_pre]:bg-[#f5f5f0] [&_pre]:p-2",
        "dark:[&_pre]:border-[#6c6a6040] dark:[&_pre]:bg-[#393937]",
        // Inline code
        "[&_code:not(pre_code)]:rounded-md [&_code:not(pre_code)]:bg-[#00000008] [&_code:not(pre_code)]:px-1 [&_code:not(pre_code)]:py-0.5",
        "dark:[&_code:not(pre_code)]:bg-[#ffffff10]",
        // Blockquotes
        "[&_blockquote]:border-l-2 [&_blockquote]:border-[#00000015] [&_blockquote]:pl-3 [&_blockquote]:text-[#4b4a48]",
        "dark:[&_blockquote]:border-[#6c6a6040] dark:[&_blockquote]:text-[#c9c6bd]",
      ].join(" ")}
    />
  );
};
```

**Step 2: Run test to verify it passes**

Run (from `apps/web`):

```bash
npm test -- components/assistant-ui/markdown-text.test.tsx
```

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/components/assistant-ui/markdown-text.tsx
git commit -m "fix(web): make markdown tables scroll horizontally"
```

### Task 5: Add a regression test for the user bubble layout

**Files:**
- Modify: `apps/web/components/claude/claude-thread.test.tsx`

**Step 1: Write the failing test**

Add this test to `apps/web/components/claude/claude-thread.test.tsx`:

```tsx
it("right-aligns user messages and omits the avatar bubble", () => {
  mockThreadIsEmpty = false;
  mockAssistantMessage = {
    role: "user",
    isLast: true,
    status: { type: "complete", reason: "stop" },
    content: [{ type: "text", text: "Hi" }],
  };

  render(<ClaudeThread />);

  const user = screen.getByTestId("user-message");
  expect(user).toHaveClass("ml-auto");
  expect(screen.queryByText("U")).not.toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run (from `apps/web`):

```bash
npm test -- components/claude/claude-thread.test.tsx
```

Expected: FAIL because the user message currently renders an avatar `U` and is not right-aligned / lacks `data-testid="user-message"`.

**Step 3: Commit the failing test**

```bash
git add apps/web/components/claude/claude-thread.test.tsx
git commit -m "test(web): cover user bubble alignment"
```

### Task 6: Implement the Claude-like user message bubble

**Files:**
- Modify: `apps/web/components/claude/claude-thread.tsx`
- Test: `apps/web/components/claude/claude-thread.test.tsx`

**Step 1: Implement minimal changes to pass the test**

Update the user-message branch in `apps/web/components/claude/claude-thread.tsx`:

- Remove the avatar block.
- Wrap user bubble in a right-aligned row.
- Render user text as plain text (no markdown spacing).
- Add `data-testid="user-message"` and a stable alignment class (`ml-auto`).

Example implementation (within the `role === "user"` `AssistantIf`):

```tsx
const UserText = ({ part }: { part: { text: string } }) => {
  return <span className="whitespace-pre-wrap">{part.text}</span>;
};

// ... inside ChatMessage
<AssistantIf condition={(s) => s.message.role === "user"}>
  <div className="flex w-full justify-end">
    <div
      data-testid="user-message"
      className="ml-auto max-w-[75ch] rounded-2xl bg-[#DDD9CE] px-4 py-3 text-[#1a1a18] shadow-sm ring-1 ring-[#00000010] dark:bg-[#393937] dark:text-[#eee] dark:ring-[#6c6a6040]"
    >
      <div className="wrap-break-word">
        <MessagePrimitive.Parts components={{ Text: UserText }} />
      </div>
    </div>
  </div>
</AssistantIf>
```

**Step 2: Run test to verify it passes**

Run (from `apps/web`):

```bash
npm test -- components/claude/claude-thread.test.tsx
```

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/components/claude/claude-thread.tsx
git commit -m "fix(web): polish user message bubble"
```

### Task 7: Full verification (web)

**Files:**
- Verify: `apps/web/components/assistant-ui/markdown-text.tsx`
- Verify: `apps/web/components/claude/claude-thread.tsx`
- Verify: `apps/web/app/globals.css`

**Step 1: Run full test suite**

Run (from `apps/web`):

```bash
npm test
```

Expected: PASS.

**Step 2: Run production build**

Run (from `apps/web`):

```bash
npm run build
```

Expected: build succeeds.

**Step 3: Manual smoke (mobile Safari/Chrome)**

- Send a markdown table wider than the viewport; verify you can swipe horizontally inside the table.
- Verify table text stays readable (no per-character vertical wrapping).
- Verify user bubble is right-aligned, compact, and doesn’t show an avatar.
