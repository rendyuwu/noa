# Assistant Streaming, Loading, and Scroll Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver first-token feedback, true incremental assistant streaming, and stable thread scrolling on `/assistant` across modern browsers.

**Architecture:** Switch the assistant endpoint from the current plain-text data stream to assistant-transport SSE, keep the existing assistant-ui remote-thread runtime, and make the frontend converter preserve one running assistant message while text deltas arrive. Then tighten the Claude workspace layout so `ThreadPrimitive.Viewport` owns scrolling and long responses do not trap the page.

**Tech Stack:** FastAPI, assistant-stream, Next.js 16, React 19, `@assistant-ui/react`, Vitest, pytest

---

### Task 1: Switch the backend assistant route to assistant-transport SSE

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Write the failing API tests**

Add or update tests in `apps/api/tests/test_assistant.py` to verify the transport contract instead of the old `aui-state:` text format.

```python
def _iter_assistant_transport_events(payload: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in payload.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[len("data: ") :]
        if raw == "[DONE]":
            continue
        events.append(json.loads(raw))
    return events


async def test_assistant_route_uses_assistant_transport_sse() -> None:
    response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.rstrip().endswith("data: [DONE]")
    events = _iter_assistant_transport_events(response.text)
    assert any(event["type"] == "update-state" for event in events)
```

Update `test_assistant_route_keeps_user_message_in_streaming_state` so it parses `data: {...}` events and still proves the running state already includes the optimistic user message.

**Step 2: Run the focused API tests and confirm they fail**

Run: `uv run pytest -q tests/test_assistant.py -k "assistant_transport_sse or keeps_user_message_in_streaming_state"`

Expected: FAIL because the route still returns `text/plain` and the old tests only understand `aui-state:` lines.

**Step 3: Implement the minimal transport change**

In `apps/api/src/noa_api/api/routes/assistant.py`, swap the serializer and keep the existing `create_run(...)` state-stream logic intact.

```python
from assistant_stream.serialization import AssistantTransportResponse


stream = create_run(run_callback, state=payload.state)
return AssistantTransportResponse(stream)
```

Do not redesign the command loop. Only change the response format and any test helper code needed to decode the new SSE event stream.

**Step 4: Re-run the focused API tests**

Run: `uv run pytest -q tests/test_assistant.py -k "assistant_transport_sse or keeps_user_message_in_streaming_state"`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant.py
git commit -m "fix(api): stream assistant transport over sse"
```

### Task 2: Make the web runtime explicitly decode assistant transport and preserve a running assistant message

**Files:**
- Modify: `apps/web/components/lib/runtime-provider.tsx`
- Create: `apps/web/components/lib/runtime-provider.test.tsx`

**Step 1: Write the failing runtime tests**

Create `apps/web/components/lib/runtime-provider.test.tsx` with focused tests for the runtime configuration and the converter behavior. Extract a pure helper from `runtime-provider.tsx` if needed so the converter can be tested without mounting the whole runtime.

```tsx
it("passes assistant-transport protocol to the runtime", () => {
  render(<NoaAssistantRuntimeProvider><div /></NoaAssistantRuntimeProvider>);

  expect(useAssistantTransportRuntime).toHaveBeenCalledWith(
    expect.objectContaining({ protocol: "assistant-transport" }),
  );
});

it("marks the last assistant message as running while state.isRunning is true", () => {
  const result = convertAssistantState(
    {
      messages: [{ id: "assistant-streaming", role: "assistant", parts: [{ type: "text", text: "" }] }],
      isRunning: true,
    },
    { pendingCommands: [], isSending: false },
  );

  expect(result.messages.at(-1)?.status).toEqual({ type: "running" });
});
```

Also add a test that pending `add-message` commands still append optimistic user messages.

**Step 2: Run the focused runtime tests and confirm they fail**

Run: `npm test -- components/lib/runtime-provider.test.tsx`

Expected: FAIL because `protocol` is not set and assistant messages are always converted with `status: { type: "complete", reason: "stop" }`.

**Step 3: Implement the minimal runtime changes**

Update `apps/web/components/lib/runtime-provider.tsx` to:

- pass `protocol: "assistant-transport"` into `useAssistantTransportRuntime`,
- compute the transport `isRunning` flag once,
- mark the active assistant message as `running` while the server stream is still open,
- preserve stable message IDs when the backend provides `assistant-streaming`.

```tsx
const transportIsRunning = Boolean(state.isRunning) || connectionMetadata.isSending;

const status = isRunningAssistant
  ? ({ type: "running" } as const)
  : ({ type: "complete", reason: "stop" } as const);

return useAssistantTransportRuntime({
  api: `${getApiUrl()}/assistant`,
  protocol: "assistant-transport",
  converter,
  ...
});
```

If the current converter is hard to test, extract a small pure helper such as `convertAssistantState(...)` in the same file and test that helper directly.

**Step 4: Re-run the focused runtime tests**

Run: `npm test -- components/lib/runtime-provider.test.tsx`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/components/lib/runtime-provider.tsx apps/web/components/lib/runtime-provider.test.tsx
git commit -m "fix(web): use assistant transport runtime"
```

### Task 3: Add a first-token loading indicator in the Claude thread UI

**Files:**
- Modify: `apps/web/components/claude/claude-thread.tsx`
- Modify: `apps/web/components/claude/claude-thread.test.tsx`

**Step 1: Write the failing UI tests**

Extend `apps/web/components/claude/claude-thread.test.tsx` so the assistant-ui mock can represent a running assistant message with empty text.

```tsx
it("shows a loading indicator for a running assistant message before first token", () => {
  mockThreadIsEmpty = false;
  mockAssistantMessage = {
    role: "assistant",
    isLast: true,
    status: { type: "running" },
    content: [{ type: "text", text: "" }],
  };

  render(<ClaudeThread />);

  expect(screen.getByLabelText("Claude is thinking")).toBeInTheDocument();
});
```

Keep the existing empty-thread and landing tests. Add one more test proving the standard bottom composer still renders once the thread has messages.

**Step 2: Run the focused thread tests and confirm they fail**

Run: `npm test -- components/claude/claude-thread.test.tsx`

Expected: FAIL because the assistant message block has no loading indicator.

**Step 3: Implement the minimal UI change**

In `apps/web/components/claude/claude-thread.tsx`, add a small loading component and render it only when the current assistant message is running and has no non-empty text yet.

```tsx
const showLoading = useAssistantState(({ message }) =>
  message.role === "assistant" &&
  message.status.type === "running" &&
  message.content.every((part) => part.type !== "text" || part.text.trim() === ""),
);
```

Render the indicator inside the existing assistant message container so the first text delta replaces it in place rather than flashing a brand-new block onto the page.

**Step 4: Re-run the focused thread tests**

Run: `npm test -- components/claude/claude-thread.test.tsx`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/components/claude/claude-thread.tsx apps/web/components/claude/claude-thread.test.tsx
git commit -m "fix(web): show assistant first-token loading state"
```

### Task 4: Repair the scroll container chain in the Claude workspace

**Files:**
- Modify: `apps/web/components/claude/claude-workspace.tsx`
- Modify: `apps/web/components/claude/claude-thread.tsx`
- Modify: `apps/web/components/claude/claude-workspace.test.tsx`
- Modify: `apps/web/components/claude/claude-thread.test.tsx`
- Modify if needed: `apps/web/components/assistant-ui/markdown-text.tsx`

**Step 1: Write the failing layout tests**

Add assertions that the workspace and thread chain include the `min-h-0` / `min-w-0` classes needed for nested grid and flex scrolling, and that the viewport opts into assistant-ui auto-scroll behavior explicitly.

```tsx
it("makes the thread viewport the scroll container", () => {
  render(<ClaudeThread />);

  const viewport = screen.getByTestId("thread-viewport");
  expect(viewport).toHaveClass("min-h-0");
  expect(viewport).toHaveClass("overflow-y-auto");
  expect(viewport).toHaveAttribute("data-auto-scroll", "true");
});
```

In `claude-workspace.test.tsx`, add expectations for `min-h-0` / `min-w-0` on the grid child that hosts `ClaudeThread`.

**Step 2: Run the focused layout tests and confirm they fail**

Run: `npm test -- components/claude/claude-workspace.test.tsx components/claude/claude-thread.test.tsx`

Expected: FAIL because the current containers do not expose the needed shrink/scroll classes or viewport props.

**Step 3: Implement the minimal layout fix**

Update `apps/web/components/claude/claude-workspace.tsx` and `apps/web/components/claude/claude-thread.tsx` so the layout chain can shrink cleanly:

- grid wrapper: `min-h-0`,
- thread host column: `min-h-0 min-w-0`,
- thread root: `min-h-0`,
- viewport: `min-h-0 grow overflow-y-auto`.

Pass the viewport scroll options explicitly:

```tsx
<ThreadPrimitive.Viewport
  autoScroll
  scrollToBottomOnRunStart
  scrollToBottomOnInitialize
  scrollToBottomOnThreadSwitch
  className="min-h-0 grow overflow-y-auto"
>
```

Only touch `apps/web/components/assistant-ui/markdown-text.tsx` if long markdown content still widens or traps the thread after the viewport fix; keep that change minimal and scoped to overflow containment.

**Step 4: Re-run the focused layout tests**

Run: `npm test -- components/claude/claude-workspace.test.tsx components/claude/claude-thread.test.tsx`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/components/claude/claude-workspace.tsx apps/web/components/claude/claude-thread.tsx apps/web/components/claude/claude-workspace.test.tsx apps/web/components/claude/claude-thread.test.tsx apps/web/components/assistant-ui/markdown-text.tsx
git commit -m "fix(web): restore assistant viewport scrolling"
```

### Task 5: Run the final verification sweep

**Files:**
- No planned file changes; fix only what the verification results prove is broken.

**Step 1: Run the focused web tests**

Run: `npm test -- components/lib/runtime-provider.test.tsx components/claude/claude-thread.test.tsx components/claude/claude-workspace.test.tsx`

Expected: PASS.

**Step 2: Run the full web test suite**

Run: `npm test`

Expected: PASS.

**Step 3: Run the production web build**

Run: `npm run build`

Expected: PASS.

**Step 4: Run the assistant API test file**

Run: `uv run pytest -q tests/test_assistant.py`

Expected: PASS.

**Step 5: Manual browser verification**

Verify in desktop Chrome plus at least one mobile browser session:

- user message stays visible immediately after pressing Enter,
- loading indicator appears before first token,
- assistant text visibly streams chunk-by-chunk,
- long responses still allow scrolling,
- switching threads still scrolls to the latest content.

**Step 6: Final commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant.py apps/web/components/lib/runtime-provider.tsx apps/web/components/lib/runtime-provider.test.tsx apps/web/components/claude/claude-thread.tsx apps/web/components/claude/claude-thread.test.tsx apps/web/components/claude/claude-workspace.tsx apps/web/components/claude/claude-workspace.test.tsx apps/web/components/assistant-ui/markdown-text.tsx
git commit -m "fix(web): restore live assistant streaming behavior"
```

If `apps/web/components/assistant-ui/markdown-text.tsx` was not changed, leave it out of the final `git add` command.
