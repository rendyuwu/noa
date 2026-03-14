# WHM Smoke Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent approved WHM CHANGE tool failures (and other unexpected exceptions during `/assistant` runs) from breaking SSE transport; represent failures as normal in-thread `tool-result` errors and keep the UI responsive.

**Architecture:** Treat approval execution failures as tool failures (persist `tool-result` + audit, no post-start exceptions). Add a best-effort try/except safety net around the agent-running portion of the `/assistant` streaming callback to ensure the SSE stream always reaches `[DONE]`.

**Tech Stack:** FastAPI, assistant-stream SSE transport, async SQLAlchemy session, pytest

---

### Task 1: Add failing unit test for approved CHANGE tool execution failure

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_assistant_service.py`

**Step 1: Write the failing test**

Add a new test that approves an action request for a CHANGE tool whose `execute` raises, and assert the service does not raise.

```python
async def test_assistant_service_approve_change_tool_failure_is_persisted_and_does_not_raise(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="failing_change",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def failing_tool(*, session, **kwargs):
        _ = session, kwargs
        raise RuntimeError("boom")

    tool = ToolDefinition(
        name="failing_change",
        description="Always fails.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=failing_tool,
    )

    monkeypatch.setattr(
        "noa_api.api.routes.assistant.get_tool_definition",
        lambda name: tool if name == tool.name else None,
    )

    assistant_repo = _FakeAssistantRepository()
    service = AssistantService(
        assistant_repo,
        _FakeRunner(),
        action_tool_run_service=ActionToolRunService(repository=repo),
        session=_FakeSession(),
    )

    await service.approve_action(
        owner_user_id=owner_id,
        owner_user_email="owner@example.com",
        thread_id=thread_id,
        action_request_id=str(request.id),
        is_user_active=True,
        authorize_tool_access=lambda _tool: _allow(),
    )

    assert request.status == ActionRequestStatus.APPROVED
    run = next(iter(repo.tool_runs.values()))
    assert run.status == ToolRunStatus.FAILED
    assert run.error and "boom" in run.error

    tool_message = assistant_repo.messages[-1]
    assert tool_message["role"] == "tool"
    part = tool_message["parts"][0]
    assert part["type"] == "tool-result"
    assert part["isError"] is True
    assert "boom" in str(part["result"].get("error"))

    assert [event["event_type"] for event in assistant_repo.audits] == [
        "action_approved",
        "tool_started",
        "tool_failed",
    ]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py::test_assistant_service_approve_change_tool_failure_is_persisted_and_does_not_raise`

Expected (before fix): FAIL because `AssistantService.approve_action()` raises `HTTPException` with `detail="Approved action execution failed"`.

**Step 3: Commit**

```bash
git add apps/api/tests/test_assistant_service.py
git commit -m "test: cover approved CHANGE tool failure without raising"
```

### Task 2: Make `approve_action()` persist failures and return cleanly

@systematic-debugging

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`

**Step 1: Implement non-throwing failure handling after tool-run starts**

In `AssistantService.approve_action()`:

- Keep precondition `HTTPException`s before tool-run creation.
- After `start_tool_run(...)` and the persisted tool-call message:
  - If `get_tool_definition(...)` is `None`, fail the run, persist a tool-result with `isError: true`, write `tool_failed` audit, then `return`.
  - If `tool.risk != ToolRisk.CHANGE`, same behavior.
  - In the tool execution `except Exception`, remove the final `raise HTTPException(...)` and instead `return` after writing the tool-result + audit.

Use the same persisted message shape used elsewhere:

```python
await self._repository.create_message(
    thread_id=thread_id,
    role="tool",
    parts=[
        {
            "type": "tool-result",
            "toolName": approved.tool_name,
            "toolCallId": tool_call_id,
            "result": {"error": "..."},
            "isError": True,
        }
    ],
)
```

**Step 2: Run the new test**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py::test_assistant_service_approve_change_tool_failure_is_persisted_and_does_not_raise`

Expected: PASS.

**Step 3: Run relevant suite**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py`

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant.py
git commit -m "fix(api): keep approved tool failures in-thread"
```

### Task 3: Add failing SSE regression test for agent-turn exceptions

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Add a failing test**

Create a `_FakeAssistantService` instance whose `run_agent_turn()` raises an exception, then assert `/assistant` still:

- returns `200` with `text/event-stream`
- ends with `data: [DONE]`
- includes an assistant-visible error message

Example:

```python
async def test_assistant_route_does_not_break_stream_when_agent_turn_raises() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(owner_user_id=owner_id, thread_id=thread_id)

    async def boom(**kwargs):
        _ = kwargs
        raise RuntimeError("agent boom")

    service.run_agent_turn = boom  # type: ignore[method-assign]

    app = _build_app(
        service,
        AuthorizationUser(
            user_id=owner_id,
            email="owner@example.com",
            display_name="Owner",
            is_active=True,
            roles=["member"],
            tools=[],
        ),
    )

    payload = {
        "state": {"messages": [], "isRunning": False},
        "commands": [
            {
                "type": "add-message",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "trigger"}],
                },
            }
        ],
        "threadId": str(thread_id),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/assistant", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.rstrip().endswith("data: [DONE]")
    assert "agent boom" in response.text or "Assistant run failed" in response.text
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q apps/api/tests/test_assistant.py::test_assistant_route_does_not_break_stream_when_agent_turn_raises`

Expected (before fix): FAIL (stream truncates or request errors) because the exception escapes the streaming callback.

**Step 3: Commit**

```bash
git add apps/api/tests/test_assistant.py
git commit -m "test: keep /assistant SSE alive on agent errors"
```

### Task 4: Add a best-effort try/except safety net around the streaming agent run

@systematic-debugging

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`

**Step 1: Implement catch-all around the agent-running block**

In `/assistant` `run_callback`, wrap the code inside `if should_run_agent:` (or at minimum the `assistant_service.run_agent_turn(...)` call and its streaming setup) with `try/except Exception`.

On exception:

- Set `controller.state["isRunning"] = False`
- Preserve existing messages if available, and append a simple assistant error message:

```python
controller.state["messages"] = [
    *list(controller.state.get("messages", []) or []),
    {
        "id": "assistant-error",
        "role": "assistant",
        "parts": [
            {
                "type": "text",
                "text": "Assistant run failed: ...",
            }
        ],
    },
]
```

Then flush `state_manager` if present and `await asyncio.sleep(0)` so the client receives the update.

Do not catch `asyncio.CancelledError` (it does not inherit from `Exception` in Python 3.11).

**Step 2: Run the SSE regression test**

Run: `uv run pytest -q apps/api/tests/test_assistant.py::test_assistant_route_does_not_break_stream_when_agent_turn_raises`

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant.py
git commit -m "fix(api): prevent /assistant SSE from breaking on exceptions"
```

### Task 5: Nudge LLM behavior to rely on approval UI for CHANGE tools

**Files:**
- Modify: `apps/api/src/noa_api/core/config.py`

**Step 1: Update `llm_system_prompt`**

Add a bullet similar to:

```text
- For WHM CHANGE tools: after preflight + collecting required arguments, call the CHANGE tool directly and rely on the approval card (request_approval). Do not ask the user for textual "yes/confirm".
```

**Step 2: Sanity check**

Run: `uv run pytest -q apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py`

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/api/src/noa_api/core/config.py
git commit -m "docs(prompt): prefer approval UI for CHANGE tools"
```

### Task 6: Full verification

@verification-before-completion

**Step 1: Run full API tests**

Run: `cd apps/api && uv run pytest -q`

Expected: PASS.

**Step 2: Optional lint/format**

Run:

- `cd apps/api && uv run ruff check src tests`
- `cd apps/api && uv run ruff format src tests`

---

## Notes

- Keep `SMOKE_TEST_WHM_REVIEW.md` uncommitted (local-only smoke notes).
