# Compact Tools UI + Tool-Result Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make tool calls feed results back to the LLM so the assistant can continue, and render a compact tool activity UI that hides successful tools.

**Architecture:** Implement a backend LLM/tool loop in `AgentRunner` (READ tools execute immediately; CHANGE tools emit approval and stop). Serialize tool calls/results into OpenAI Chat Completions message format so the model sees tool outputs. Update the assistant transport route to run the agent after approvals and tool-result submissions. In the Claude UI, replace the tool activity card with one-line rows and return `null` for successful tools.

**Tech Stack:** FastAPI + async SQLAlchemy, OpenAI Chat Completions (tools), Next.js + React, `@assistant-ui/react`, Tailwind, Vitest, Pytest.

---

### Task 0: Create an isolated worktree (recommended)

**Files:** none

**Step 1: Create worktree**

Run: `git worktree add ../noa-compact-tools -b fix/compact-tools-and-tool-loop`

Expected: new directory `../noa-compact-tools` with a clean checkout.

---

### Task 1: Add a failing AgentRunner test for the tool loop

**Files:**
- Modify: `apps/api/tests/test_agent_runner.py`

**Step 1: Write the failing test**

Add to `apps/api/tests/test_agent_runner.py`:

```py
async def test_agent_runner_calls_llm_again_after_tool_results() -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    class _LoopingLLM:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, object]]] = []
            self.turn = 0

        async def run_turn(self, *, messages: list[dict[str, object]], tools: list[dict[str, object]], on_text_delta=None):
            _ = tools, on_text_delta
            self.calls.append(list(messages))
            self.turn += 1

            if self.turn == 1:
                return LLMTurnResponse(
                    text="I'll check today's server date.",
                    tool_calls=[LLMToolCall(name="get_current_date", arguments={})],
                )

            # Second turn must see a tool result from the first turn.
            saw_tool_result = False
            for msg in messages:
                if msg.get("role") != "tool":
                    continue
                parts = msg.get("parts")
                if not isinstance(parts, list):
                    continue
                if any(isinstance(p, dict) and p.get("type") == "tool-result" for p in parts):
                    saw_tool_result = True
                    break
            assert saw_tool_result is True

            return LLMTurnResponse(text="Today's date is available.", tool_calls=[])

    runner = AgentRunner(
        llm_client=_LoopingLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[
            {"role": "user", "parts": [{"type": "text", "text": "What's the date?"}]},
        ],
        available_tool_names={"get_current_date"},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    # Should include: assistant text (turn 1) + tool-call + tool-result + assistant text (turn 2)
    assert any(m.role == "assistant" and any(p.get("type") == "text" for p in m.parts) for m in result.messages)
    assert any(any(p.get("type") == "tool-call" for p in m.parts) for m in result.messages)
    assert any(m.role == "tool" and any(p.get("type") == "tool-result" for p in m.parts) for m in result.messages)
    assert any(
        m.role == "assistant" and any(p.get("type") == "text" and p.get("text") == "Today's date is available." for p in m.parts)
        for m in result.messages
    )
```

**Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_agent_runner_calls_llm_again_after_tool_results`

Expected: FAIL (runner only calls the LLM once).

**Step 3: Commit**

```bash
git add apps/api/tests/test_agent_runner.py
git commit -m "test(api): cover tool-loop continuation after tool result"
```

---

### Task 2: Implement a bounded tool loop in AgentRunner

**Files:**
- Modify: `apps/api/src/noa_api/core/agent/runner.py`

**Step 1: Implement minimal loop (READ tools only)**

In `apps/api/src/noa_api/core/agent/runner.py`, update `AgentRunner.run_turn` to:

- Maintain a local `working_messages = list(thread_messages)`.
- Loop (max rounds, max tool calls):
  - Call `self._llm_client.run_turn(messages=working_messages, tools=llm_tools, on_text_delta=...)`.
  - If `text` is non-empty: append an assistant text `AgentMessage` to `output_messages` AND append the same shape to `working_messages`.
  - If there are tool calls:
    - For each tool call, execute `_process_tool_call`.
    - Append returned messages to both `output_messages` and `working_messages`.
    - If a CHANGE tool yields `request_approval`, stop looping and return.
  - If there are no tool calls: return.

Add safety caps (example constants near the top of `run_turn`):

```py
max_rounds = 4
max_tool_calls = 8
```

If caps are hit, append a final assistant message:

```py
AgentMessage(role="assistant", parts=[{"type": "text", "text": "Tool loop exceeded safety limits."}])
```

**Step 2: Run the failing test**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_agent_runner_calls_llm_again_after_tool_results`

Expected: PASS.

**Step 3: Run the full API test suite**

Run: `cd apps/api && uv run pytest -q`

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/core/agent/runner.py
git commit -m "feat(api): loop LLM turns until tool calls complete"
```

---

### Task 3: Make RuleBasedLLMClient terminate after tool results (dev mode)

**Files:**
- Modify: `apps/api/src/noa_api/core/agent/runner.py`
- Modify: `apps/api/tests/test_agent_runner.py`

**Step 1: Write the failing test**

Add to `apps/api/tests/test_agent_runner.py`:

```py
async def test_rule_based_llm_responds_to_date_tool_result() -> None:
    client = RuleBasedLLMClient()
    turn = await client.run_turn(
        messages=[
            {"role": "user", "parts": [{"type": "text", "text": "What's the date?"}]},
            {
                "role": "assistant",
                "parts": [
                    {"type": "tool-call", "toolName": "get_current_date", "toolCallId": "tc-1", "args": {}}
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {"type": "tool-result", "toolName": "get_current_date", "toolCallId": "tc-1", "result": {"date": "2026-03-12"}, "isError": False}
                ],
            },
        ],
        tools=[],
        on_text_delta=None,
    )
    assert "2026-03-12" in turn.text
    assert turn.tool_calls == []
```

**Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_rule_based_llm_responds_to_date_tool_result`

Expected: FAIL (client currently re-triggers the tool call).

**Step 3: Implement tool-result awareness**

In `RuleBasedLLMClient.run_turn` (same file), before inspecting the last user text:

- Scan messages from the end for a tool `tool-result` part.
- If it is `get_current_date` and result contains `date`, respond with a text that includes that date and no tool calls.
- If it is `get_current_time` and result contains `time`, respond similarly.

Minimal sketch:

```py
for message in reversed(messages):
    if message.get("role") != "tool":
        continue
    parts = message.get("parts")
    if not isinstance(parts, list):
        continue
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "tool-result":
            continue
        name = part.get("toolName")
        result = part.get("result")
        if name == "get_current_date" and isinstance(result, dict) and isinstance(result.get("date"), str):
            return LLMTurnResponse(text=f"Today's date is {result['date']}.", tool_calls=[])
        if name == "get_current_time" and isinstance(result, dict) and isinstance(result.get("time"), str):
            return LLMTurnResponse(text=f"The current time is {result['time']}.", tool_calls=[])
```

**Step 4: Run tests**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_rule_based_llm_responds_to_date_tool_result`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/agent/runner.py apps/api/tests/test_agent_runner.py
git commit -m "fix(api): make rule-based LLM stop after tool results"
```

---

### Task 4: Serialize tool-call/tool-result parts for OpenAI-compatible models

**Files:**
- Modify: `apps/api/src/noa_api/core/agent/runner.py`
- Modify: `apps/api/tests/test_agent_runner_streaming.py`

**Step 1: Write the failing test (non-streaming)**

Add to `apps/api/tests/test_agent_runner_streaming.py`:

```py
async def test_openai_client_includes_tool_calls_and_tool_results_in_messages() -> None:
    captured: dict[str, object] = {}

    class _FakeCompletions:
        async def create(self, **kwargs: object):
            captured.update(kwargs)

            class _Msg:
                content = "ok"
                tool_calls = []

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    client = OpenAICompatibleLLMClient(model="gpt-4o-mini", api_key="test", base_url=None, system_prompt="")
    client._client = _FakeOpenAI()  # type: ignore[attr-defined]

    await client.run_turn(
        messages=[
            {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
            {
                "role": "assistant",
                "parts": [
                    {"type": "tool-call", "toolName": "get_current_date", "toolCallId": "tc-1", "args": {}}
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {"type": "tool-result", "toolName": "get_current_date", "toolCallId": "tc-1", "result": {"date": "2026-03-12"}, "isError": False}
                ],
            },
        ],
        tools=[{"type": "function", "function": {"name": "get_current_date", "description": "", "parameters": {"type": "object"}}}],
        on_text_delta=None,
    )

    msgs = captured.get("messages")
    assert isinstance(msgs, list)
    assert any(isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls") for m in msgs)
    assert any(isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id") == "tc-1" for m in msgs)
```

**Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner_streaming.py::test_openai_client_includes_tool_calls_and_tool_results_in_messages`

Expected: FAIL (client currently only emits text-only messages).

**Step 3: Implement serialization in OpenAICompatibleLLMClient**

In `OpenAICompatibleLLMClient.run_turn`, when building `llm_messages`:

- For each internal message:
  - Extract text parts.
  - Extract tool-call parts and map them to OpenAI `tool_calls`.
  - For `role == "tool"`, map tool-result parts to OpenAI tool messages.

Minimal mapping logic:

```py
tool_calls_out = []
for part in parts:
    if isinstance(part, dict) and part.get("type") == "tool-call":
        tool_calls_out.append(
            {
                "id": part.get("toolCallId") or "tool-call",
                "type": "function",
                "function": {
                    "name": part.get("toolName") or "tool",
                    "arguments": json.dumps(part.get("args") or {}),
                },
            }
        )
```

For tool results:

```py
if role == "tool":
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "tool-result":
            llm_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": part.get("toolCallId") or "tool-call",
                    "content": json.dumps(part.get("result") or {}),
                }
            )
    continue
```

For assistant messages with tool calls and optional text:

```py
msg: dict[str, Any] = {"role": role, "content": "\n".join(text_parts) if text_parts else ""}
if tool_calls_out:
    msg["tool_calls"] = tool_calls_out
llm_messages.append(msg)
```

Apply the same mapping in both streaming and non-streaming branches.

**Step 4: Run the test again**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner_streaming.py::test_openai_client_includes_tool_calls_and_tool_results_in_messages`

Expected: PASS.

**Step 5: Run full API tests**

Run: `cd apps/api && uv run pytest -q`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/core/agent/runner.py apps/api/tests/test_agent_runner_streaming.py
git commit -m "fix(api): include tool calls/results in OpenAI message serialization"
```

---

### Task 5: Run the agent after approve-action and add-tool-result commands

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Write failing route tests**

Add two tests to `apps/api/tests/test_assistant.py` (near other route tests):

```py
async def test_assistant_route_runs_agent_after_add_tool_result_command() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        messages=[{"id": str(uuid4()), "role": "assistant", "parts": [{"type": "text", "text": "From DB"}]}],
        runner_messages=[AgentMessage(role="assistant", parts=[{"type": "text", "text": "Follow-up after tool result"}])],
        runner_text_deltas=["Follow-up"],
    )
    app = _build_app(
        service,
        AuthorizationUser(user_id=owner_id, email="owner@example.com", display_name="Owner", is_active=True, roles=["member"], tools=[]),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [{"type": "add-tool-result", "toolCallId": "tool-call-1", "result": {"ok": True}}],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert "Follow-up after tool result" in response.text


async def test_assistant_route_runs_agent_after_approve_action_command() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        messages=[{"id": str(uuid4()), "role": "assistant", "parts": [{"type": "text", "text": "From DB"}]}],
        runner_messages=[AgentMessage(role="assistant", parts=[{"type": "text", "text": "Follow-up after approval"}])],
        runner_text_deltas=["Follow-up"],
    )
    app = _build_app(
        service,
        AuthorizationUser(user_id=owner_id, email="owner@example.com", display_name="Owner", is_active=True, roles=["member"], tools=[]),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [{"type": "approve-action", "actionRequestId": "ar-1"}],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert "Follow-up after approval" in response.text
```

**Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest -q tests/test_assistant.py::test_assistant_route_runs_agent_after_add_tool_result_command`

Expected: FAIL (agent is not run for add-tool-result).

Run: `cd apps/api && uv run pytest -q tests/test_assistant.py::test_assistant_route_runs_agent_after_approve_action_command`

Expected: FAIL (agent is not run for approve-action).

**Step 3: Update should_run_agent logic**

In `apps/api/src/noa_api/api/routes/assistant.py`, change:

```py
should_run_agent = any(
    isinstance(command, AddMessageCommand) and command.message.role == "user"
    for command in payload.commands
)
```

To include approvals and tool results:

```py
should_run_agent = any(
    (
        isinstance(command, AddMessageCommand) and command.message.role == "user"
    )
    or isinstance(command, ApproveActionCommand)
    or isinstance(command, AddToolResultCommand)
    for command in payload.commands
)
```

**Step 4: Run the new tests**

Run:

- `cd apps/api && uv run pytest -q tests/test_assistant.py::test_assistant_route_runs_agent_after_add_tool_result_command`
- `cd apps/api && uv run pytest -q tests/test_assistant.py::test_assistant_route_runs_agent_after_approve_action_command`

Expected: PASS.

**Step 5: Run full API tests**

Run: `cd apps/api && uv run pytest -q`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant.py
git commit -m "feat(api): continue agent after approvals and tool results"
```

---

### Task 6: Make the Claude tool UI compact and hide successful tools

**Files:**
- Modify: `apps/web/components/claude/request-approval-tool-ui.tsx`
- Modify: `apps/web/components/claude/request-approval-tool-ui.test.tsx`

**Step 1: Write failing UI test (success hides)**

Update `apps/web/components/claude/request-approval-tool-ui.test.tsx`:

```tsx
it("hides successful tools after completion", () => {
  render(
    <ClaudeToolFallback
      toolName="get_current_time"
      toolCallId="tool-call-1"
      status={{ type: "complete" }}
      result={{ time: "10:00" }}
      isError={false}
    />,
  );

  expect(screen.queryByText(/current time/i)).not.toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run: `cd apps/web && npm test -- apps/web/components/claude/request-approval-tool-ui.test.tsx`

Expected: FAIL (component currently renders on success).

**Step 3: Implement compact + hide-on-success**

In `apps/web/components/claude/request-approval-tool-ui.tsx`:

- Change `ClaudeToolGroup` to be a minimal wrapper and render nothing when children are empty.
- Change `ClaudeToolFallback`:
  - If computed `statusType === "complete"` and not error, `return null`.
  - Replace the `<details>` UI with a one-line row `<div>`.
  - Keep the activity copy mapping (no raw args/result rendering).

**Step 4: Update/replace the remaining tests**

Replace tests to align with the new UI:

- Running renders a row and includes the activity text.
- Missing status + no result defaults to running.
- Error defaults to incomplete.
- Success returns null.

**Step 5: Run web tests**

Run: `cd apps/web && npm test`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/web/components/claude/request-approval-tool-ui.tsx apps/web/components/claude/request-approval-tool-ui.test.tsx
git commit -m "feat(web): compact tool rows and hide successful tool activity"
```

---

### Task 7: Verify builds

**Step 1: API tests**

Run: `cd apps/api && uv run pytest -q`

Expected: PASS.

**Step 2: Web tests**

Run: `cd apps/web && npm test`

Expected: PASS.

**Step 3: Web build**

Run: `cd apps/web && npm run build`

Expected: build succeeds.
