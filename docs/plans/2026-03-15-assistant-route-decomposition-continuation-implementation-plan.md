# Assistant Route Decomposition Continuation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Further shrink `apps/api/src/noa_api/api/routes/assistant.py` by extracting the remaining assistant orchestration flow into assistant-focused helpers while preserving the current HTTP and SSE contracts.

**Architecture:** Keep `assistant.py` as the transport boundary for request models, dependency wiring, SSE startup, and the final route-facing exception boundary. Move the remaining pre-stream preparation and in-stream agent coordination into a new `assistant_operations.py` helper so assistant-domain flow, logging context binding, and fallback handling are directly testable without routing everything through the monolithic transport callback.

**Tech Stack:** FastAPI, Pydantic, async SQLAlchemy, assistant-stream, pytest, Ruff

---

### Task 1: Pin the new orchestration seam with failing tests

@test-driven-development

**Files:**
- Create: `apps/api/tests/test_assistant_operations.py`
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Write the failing helper tests**

Create `apps/api/tests/test_assistant_operations.py` and pin the behavior you want the new orchestration module to own.

Start with a pre-stream preparation test that proves the helper validates commands, loads the current state, applies commands, then reloads the canonical state in that order:

```python
async def test_prepare_assistant_transport_reloads_canonical_state_after_commands() -> None:
    service = _FakeAssistantServiceWithCalls(
        states=[{"messages": [{"id": "before", "role": "user", "parts": []}], "isRunning": False},
                {"messages": [{"id": "after", "role": "user", "parts": []}], "isRunning": False}],
    )

    prepared = await prepare_assistant_transport(
        payload=_payload_with_add_message(),
        current_user=_active_user(),
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
    )

    assert [call[0] for call in service.calls] == ["load_state", "add_message", "load_state"]
    assert prepared.canonical_state["messages"][0]["id"] == "after"
```

Add a second helper test for the in-stream agent phase so the new seam proves it always exposes workflow todos and passes the authorized tool set through to the service:

```python
async def test_run_agent_phase_adds_workflow_todo_tool() -> None:
    controller = _FakeController()
    service = _FakeAssistantServiceWithAgentResult()

    await run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(),
        current_user=_active_user(),
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(allowed_tools={"demo_tool"}),
        canonical_state={"messages": [], "isRunning": False},
        command_types=["add-message"],
    )

    assert service.seen_available_tools == {"demo_tool", "update_workflow_todo"}
```

**Step 2: Add one route-level guardrail assertion**

Extend `apps/api/tests/test_assistant.py` with one targeted transport test that still proves pre-stream failures surface as structured HTTP errors instead of starting SSE.

Use the existing app fixture pattern and assert the additive contract remains intact:

```python
assert response.status_code == 404
body = response.json()
assert body["detail"] == "Thread not found"
assert body["error_code"] == "thread_not_found"
assert response.headers["x-request-id"] == body["request_id"]
```

**Step 3: Run the targeted tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py`

Expected: FAIL because `apps/api/src/noa_api/api/routes/assistant_operations.py` does not exist yet and the new helper imports are unresolved.

**Step 4: Commit the failing characterization tests**

```bash
git add apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py
git commit -m "test(api): pin assistant orchestration seams"
```

### Task 2: Extract pre-stream assistant preparation out of the route

@test-driven-development

**Files:**
- Create: `apps/api/src/noa_api/api/routes/assistant_operations.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Test: `apps/api/tests/test_assistant_operations.py`
- Test: `apps/api/tests/test_assistant.py`

**Step 1: Create the smallest helper for pre-stream preparation**

Add `apps/api/src/noa_api/api/routes/assistant_operations.py` with a small dataclass and one helper for the route's pre-stream work.

Suggested shape:

```python
@dataclass(slots=True)
class PreparedAssistantTransport:
    command_types: list[str]
    canonical_state: dict[str, object]


async def prepare_assistant_transport(
    *,
    payload: AssistantRequest,
    current_user: AuthorizationUser,
    assistant_service: AssistantServiceProtocol,
    authorization_service: AuthorizationService,
) -> PreparedAssistantTransport:
    command_types = [command.type for command in payload.commands]
    validate_commands(payload.commands)
    await assistant_service.load_state(
        owner_user_id=current_user.user_id,
        thread_id=payload.thread_id,
    )
    await apply_commands(
        commands=payload.commands,
        assistant_service=assistant_service,
        current_user=current_user,
        payload=payload,
        authorization_service=authorization_service,
    )
    canonical_state = await assistant_service.load_state(
        owner_user_id=current_user.user_id,
        thread_id=payload.thread_id,
    )
    return PreparedAssistantTransport(
        command_types=command_types,
        canonical_state=canonical_state,
    )
```

Keep it boring. Do not move SSE logic into this helper yet.

**Step 2: Rewire `assistant.py` to call the helper**

Replace the in-route pre-stream block in `apps/api/src/noa_api/api/routes/assistant.py` with a single call to `prepare_assistant_transport(...)`.

After rewiring, the route should read more like:

```python
prepared = await prepare_assistant_transport(
    payload=payload,
    current_user=current_user,
    assistant_service=assistant_service,
    authorization_service=authorization_service,
)
command_types = prepared.command_types
canonical_state = prepared.canonical_state
```

Preserve the existing `CancelledError` handling and pre-stream logging behavior exactly.

**Step 3: Run the focused tests to verify the implementation**

Run: `uv run pytest -q apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_commands.py`

Expected: PASS.

**Step 4: Commit the extraction**

```bash
git add apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py
git commit -m "refactor(api): extract assistant pre-stream preparation"
```

### Task 3: Pin in-stream agent coordination and fallback behavior with focused tests

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_assistant_operations.py`
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Add a failing helper test for agent failure fallback**

Extend `apps/api/tests/test_assistant_operations.py` with a helper-level test that proves the new in-stream helper persists a safe assistant error message when agent execution fails.

```python
async def test_run_agent_phase_persists_safe_error_message_on_failure() -> None:
    controller = _FakeController()
    service = _FakeAssistantServiceThatFailsAgentRun()

    await run_agent_phase(
        controller=controller,
        payload=_payload_with_user_message(),
        current_user=_active_user(),
        assistant_service=service,
        authorization_service=_FakeAuthorizationService(),
        canonical_state={"messages": [], "isRunning": False},
        command_types=["add-message"],
    )

    assert service.added_messages[-1]["role"] == "assistant"
    assert service.added_messages[-1]["parts"] == [{"type": "text", "text": "Assistant run failed. Please try again."}]
    assert controller.state["isRunning"] is False
```

Add one more helper test for the local fallback path when message persistence or state refresh fails.

**Step 2: Keep one route-level SSE characterization test**

In `apps/api/tests/test_assistant.py`, keep a transport-level test that reads the SSE payload and proves the final state still contains the fallback text after an in-stream failure.

That route-level test should stay additive and focused; do not duplicate all helper assertions through the HTTP layer.

**Step 3: Run the targeted tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py`

Expected: FAIL because the in-stream coordination helper does not exist yet.

**Step 4: Commit the failing tests**

```bash
git add apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py
git commit -m "test(api): cover assistant agent-phase coordination"
```

### Task 4: Extract the in-stream agent coordination helper and slim the route callback

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant_operations.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Test: `apps/api/tests/test_assistant_operations.py`
- Test: `apps/api/tests/test_assistant.py`
- Test: `apps/api/tests/test_assistant_streaming.py`

**Step 1: Add a focused in-stream helper to `assistant_operations.py`**

Extend `apps/api/src/noa_api/api/routes/assistant_operations.py` with a helper that owns the agent-phase flow currently embedded in `run_callback`.

Suggested shape:

```python
async def run_agent_phase(
    *,
    controller: RunController,
    payload: AssistantRequest,
    current_user: AuthorizationUser,
    assistant_service: AssistantServiceProtocol,
    authorization_service: AuthorizationService,
    canonical_state: dict[str, object],
    command_types: list[str],
) -> None:
    ...
```

Move these responsibilities into the helper:

- resolving the allowed tool set
- always adding `update_workflow_todo`
- creating and updating the streaming placeholder
- calling `assistant_service.run_agent_turn(...)`
- persisting or locally appending the safe fallback assistant error message
- refreshing final state without letting exceptions escape the callback

Keep using the existing `assistant_streaming.py` helpers instead of re-implementing them.

**Step 2: Reduce the route callback to transport coordination**

After the extraction, `run_callback` in `apps/api/src/noa_api/api/routes/assistant.py` should mainly:

- initialize `controller.state`
- seed it with `canonical_state`
- decide whether an agent phase is needed
- call `run_agent_phase(...)`
- return the stream object

Preserve the current behavior that `asyncio.CancelledError` is re-raised and no other exception is allowed to escape the callback.

**Step 3: Run the focused assistant suite**

Run: `uv run pytest -q apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_streaming.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant_commands.py`

Expected: PASS.

**Step 4: Commit the extraction**

```bash
git add apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_streaming.py
git commit -m "refactor(api): extract assistant agent-phase orchestration"
```

### Task 5: Refresh the audit handoff and verify the backend slice

@verification-before-completion

**Files:**
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`
- Reference: `docs/plans/2026-03-15-assistant-route-decomposition-continuation-design.md`
- Reference: `docs/plans/2026-03-15-assistant-route-decomposition-continuation-implementation-plan.md`

**Step 1: Update the audit report status**

Revise `docs/reports/2026-03-14-error-handling-and-logging-audit.md` so it records this continuation pass separately from the earlier backend error-code/logging work.

Add concrete notes for:

- the new assistant orchestration extraction work completed
- what still remains in `apps/api/src/noa_api/api/routes/assistant.py`
- the updated recommended next backend-only step after this pass

Reference the new 2026-03-15 design and implementation plan docs so the next session has an obvious resume point.

**Step 2: Run the focused backend verification suite**

Run: `uv run pytest -q apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py`

Expected: PASS.

**Step 3: Run the broader backend safety checks**

Run: `uv run pytest -q`

Expected: PASS.

Run: `uv run ruff check src tests`

Expected: PASS.

If either broader check fails for an unrelated reason, record the exact failing test or lint path in the audit update before stopping.

**Step 4: Commit the docs and verification-ready state**

```bash
git add docs/reports/2026-03-14-error-handling-and-logging-audit.md docs/plans/2026-03-15-assistant-route-decomposition-continuation-design.md docs/plans/2026-03-15-assistant-route-decomposition-continuation-implementation-plan.md
git commit -m "docs: plan assistant route decomposition continuation"
```
