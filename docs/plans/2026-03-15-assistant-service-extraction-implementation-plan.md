# Assistant Service Extraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract the remaining assistant approval, denial, tool-result, and approved-tool-execution flows out of `apps/api/src/noa_api/api/routes/assistant.py` while preserving current assistant HTTP and SSE contracts.

**Architecture:** Keep `apps/api/src/noa_api/api/routes/assistant.py` as the FastAPI transport boundary and thin service facade. Move the remaining assistant-domain operation flow into focused helper modules that own validation, persistence sequencing, audit logging, and approved-tool execution, then translate assistant-domain failures back to the existing HTTP `detail` and `error_code` contract at one narrow boundary.

**Tech Stack:** FastAPI, Pydantic, async SQLAlchemy, assistant-stream, pytest, Ruff

---

### Task 1: Pin the remaining assistant service behaviors with focused failing tests

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_assistant_service.py`
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Add helper-facing characterization tests for the remaining flows**

Extend `apps/api/tests/test_assistant_service.py` with new tests that describe the extracted seams you want to create.

Start with a tool-result flow test that proves the operation helper validates thread ownership and tool-run status before completing the run:

```python
async def test_record_tool_result_rejects_foreign_thread() -> None:
    owner_id = uuid4()
    foreign_thread_id = uuid4()
    actual_thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})
    started = await repo.start_tool_run(
        thread_id=actual_thread_id,
        tool_name="get_current_time",
        args={},
        action_request_id=None,
        requested_by_user_id=owner_id,
    )

    with pytest.raises(HTTPException, match="Tool call not found"):
        await record_tool_result(
            owner_user_id=owner_id,
            owner_user_email="owner@example.com",
            thread_id=foreign_thread_id,
            tool_call_id=str(started.id),
            result={"ok": True},
            repository=_FakeAssistantRepository(),
            action_tool_run_service=ActionToolRunService(repository=repo),
        )
```

Add an approval-flow test that proves the approval helper delegates approved-tool execution rather than inlining it all inside one branch:

```python
async def test_approve_action_starts_tool_run_before_execution() -> None:
    operations = _FakeApprovedToolExecutor()
    ...
    await approve_action_request(..., approved_tool_executor=operations)
    assert operations.calls == [("execute", "set_demo_flag")]
```

Add one denial-flow test that proves the denial helper writes the assistant text message and audit record with the validated request metadata.

**Step 2: Add one route-level guardrail test**

Extend `apps/api/tests/test_assistant.py` with one additive HTTP contract test that proves an extracted assistant-domain failure still returns the same response envelope before SSE starts:

```python
assert response.status_code == 409
body = response.json()
assert body["detail"] == "Action request already decided"
assert body["error_code"] == "action_request_already_decided"
assert response.headers["x-request-id"] == body["request_id"]
```

**Step 3: Run the targeted tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant.py`

Expected: FAIL because the extracted assistant operation helpers do not exist yet.

**Step 4: Commit the failing characterization tests**

```bash
git add apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant.py
git commit -m "test(api): pin assistant service extraction seams"
```

### Task 2: Extract tool-result recording into a focused helper

@test-driven-development

**Files:**
- Create: `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant_errors.py`
- Test: `apps/api/tests/test_assistant_service.py`

**Step 1: Write the smallest helper API for recording tool results**

Create `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py` with one small entry point that owns the current `add_tool_result(...)` flow.

Suggested shape:

```python
async def record_tool_result(
    *,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    tool_call_id: str | None,
    result: dict[str, Any],
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
) -> None:
    ...
```

Inside this helper:

- parse `toolCallId`
- load the tool run
- validate ownership and `STARTED` status
- complete the tool run
- persist the `tool` message with `build_tool_result_part(...)`
- write the `tool_completed` audit entry
- emit the existing `assistant_tool_result_recorded` success log under one `log_context(...)`

**Step 2: Move the HTTP shaping behind assistant-focused error translation**

If the helper still needs to signal not-found, stale, or malformed IDs, route those failures through `apps/api/src/noa_api/api/routes/assistant_errors.py` so the extracted flow does not build ad hoc `HTTPException` objects inline.

Keep the current public values unchanged:

- `Unknown tool call id`
- `Tool call not found`
- `Tool call is not awaiting result`

**Step 3: Rewire `AssistantService.add_tool_result(...)` into a thin delegation method**

After extraction, `apps/api/src/noa_api/api/routes/assistant.py` should keep only:

```python
async def add_tool_result(...) -> None:
    await record_tool_result(
        owner_user_id=owner_user_id,
        owner_user_email=owner_user_email,
        thread_id=thread_id,
        tool_call_id=tool_call_id,
        result=result,
        repository=self._repository,
        action_tool_run_service=self._action_tool_run_service,
    )
```

**Step 4: Run the focused tests**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py -k "tool_result or tool_call"`

Expected: PASS.

**Step 5: Commit the extraction**

```bash
git add apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py apps/api/src/noa_api/api/routes/assistant.py apps/api/src/noa_api/api/routes/assistant_errors.py apps/api/tests/test_assistant_service.py
git commit -m "refactor(api): extract assistant tool result operations"
```

### Task 3: Extract action request validation and deny flow

@test-driven-development

**Files:**
- Create: `apps/api/src/noa_api/api/routes/assistant_action_operations.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant_errors.py`
- Test: `apps/api/tests/test_assistant_service.py`

**Step 1: Add a reusable action-request validation helper**

Create a helper in `apps/api/src/noa_api/api/routes/assistant_action_operations.py` that loads an action request and validates:

- parsed `actionRequestId`
- thread ownership
- requesting user ownership
- pending versus already-decided status

Suggested internal shape:

```python
async def require_pending_action_request(
    *,
    owner_user_id: UUID,
    thread_id: UUID,
    action_request_id: str | None,
    action_tool_run_service: ActionToolRunService,
) -> ActionRequest:
    ...
```

**Step 2: Extract the deny flow first**

Use the shared validator to implement:

```python
async def deny_action_request(
    *,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    action_request_id: str | None,
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
) -> None:
    ...
```

Persist exactly the current assistant text message:

```python
{
    "type": "text",
    "text": f"Denied action request for tool '{denied.tool_name}'.",
}
```

Also keep the `action_denied` audit log and `assistant_action_denied` success log with the same bound fields.

**Step 3: Rewire `AssistantService.deny_action(...)` into a thin delegation method**

Reduce the route-module method to a wrapper that passes dependencies into `deny_action_request(...)`.

**Step 4: Run the focused tests**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py -k "deny"`

Expected: PASS.

**Step 5: Commit the extraction**

```bash
git add apps/api/src/noa_api/api/routes/assistant_action_operations.py apps/api/src/noa_api/api/routes/assistant.py apps/api/src/noa_api/api/routes/assistant_errors.py apps/api/tests/test_assistant_service.py
git commit -m "refactor(api): extract assistant deny action flow"
```

### Task 4: Extract approval orchestration and approved-tool execution

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant_action_operations.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant_errors.py`
- Test: `apps/api/tests/test_assistant_service.py`

**Step 1: Pin the approved-tool execution branches with explicit tests**

In `apps/api/tests/test_assistant_service.py`, add or reorganize tests so the extracted helper directly covers:

- missing tool definition produces a failed tool run and `tool-result` error payload
- risk mismatch produces a failed tool run and `expectedRisk`/`actualRisk` payload
- execution exceptions are sanitized through `sanitize_tool_error(...)`
- successful execution produces the `tool_completed` audit event and persisted tool result

Keep the existing sanitized payload assertions exactly as they are today.

**Step 2: Extract the approval orchestration API**

Extend `apps/api/src/noa_api/api/routes/assistant_action_operations.py` with:

```python
async def approve_action_request(
    *,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    action_request_id: str | None,
    is_user_active: bool,
    authorize_tool_access: Callable[[str], Awaitable[bool]],
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
    execute_tool: ApprovedToolExecutor,
) -> None:
    ...
```

That helper should:

- reject inactive users with the existing pending-approval contract
- validate the pending action request
- validate `ToolRisk.CHANGE`
- validate tool authorization
- approve the request and start the tool run
- persist the assistant `tool-call` message
- write `action_approved` and `tool_started` audit events
- delegate the actual approved-tool execution to a smaller helper

**Step 3: Extract the approved-tool execution helper**

Still in `apps/api/src/noa_api/api/routes/assistant_action_operations.py`, add a helper that owns:

- `get_tool_definition(...)`
- `ToolRisk.CHANGE` verification
- session-aware execution via the existing `_execute_tool(...)` behavior
- sanitized exception logging and failed tool-run persistence
- final `tool-result` message and `tool_completed` or `tool_failed` audit log writes

Suggested shape:

```python
async def execute_approved_tool_run(
    *,
    started_tool_run: ToolRun,
    approved_request: ActionRequest,
    owner_user_id: UUID,
    owner_user_email: str | None,
    thread_id: UUID,
    repository: AssistantMessageAuditRepositoryProtocol,
    action_tool_run_service: ActionToolRunService,
    session: AsyncSession | None,
) -> None:
    ...
```

Preserve `asyncio.CancelledError` passthrough exactly.

**Step 4: Rewire `AssistantService.approve_action(...)` into a thin wrapper**

Keep the public method and signature stable, but make it delegate to the extracted helper with the current dependencies.

Leave `_execute_tool(...)` in `apps/api/src/noa_api/api/routes/assistant.py` only if the extracted helper needs it temporarily. If it is cleaner, move the session-aware tool execution helper into the new extracted module and delete the private route-module helper entirely.

**Step 5: Run the focused assistant service tests**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py`

Expected: PASS.

**Step 6: Commit the extraction**

```bash
git add apps/api/src/noa_api/api/routes/assistant_action_operations.py apps/api/src/noa_api/api/routes/assistant.py apps/api/src/noa_api/api/routes/assistant_errors.py apps/api/tests/test_assistant_service.py
git commit -m "refactor(api): extract assistant approval operations"
```

### Task 5: Tighten the assistant HTTP translation boundary

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant_errors.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant_action_operations.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py`
- Test: `apps/api/tests/test_assistant_service.py`
- Test: `apps/api/tests/test_assistant.py`

**Step 1: Introduce assistant-domain failure types or translation helpers**

Add small assistant-focused failure types or helper constructors in `apps/api/src/noa_api/api/routes/assistant_errors.py` so the extracted operation modules no longer need to assemble route-shaped `HTTPException` objects inline.

One acceptable pattern is:

```python
class AssistantDomainError(Exception):
    status_code: int
    detail: str
    error_code: str | None


def to_assistant_http_error(exc: AssistantDomainError) -> HTTPException:
    ...
```

Another acceptable pattern is a small set of explicit helper functions like `action_request_not_found_error()` and `tool_call_not_awaiting_result_error()`.

The key rule: the extracted operation modules should not repeat `assistant_http_error(...)` branches inline.

**Step 2: Update the extracted modules to use the new translation seam**

Replace repeated route-shaped raises in:

- `apps/api/src/noa_api/api/routes/assistant_action_operations.py`
- `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py`

Keep all existing status codes, `detail`, and `error_code` values unchanged.

**Step 3: Keep the route behavior stable**

Verify `apps/api/src/noa_api/api/routes/assistant.py` still treats these failures the same way at the HTTP boundary and still logs them using the current event names.

**Step 4: Run the translation-focused tests**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant.py -k "action_request or tool_call or pending approval"`

Expected: PASS.

**Step 5: Commit the translation cleanup**

```bash
git add apps/api/src/noa_api/api/routes/assistant_errors.py apps/api/src/noa_api/api/routes/assistant.py apps/api/src/noa_api/api/routes/assistant_action_operations.py apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant.py
git commit -m "refactor(api): centralize assistant error translation"
```

### Task 6: Verify the backend slice and refresh the handoff docs

@verification-before-completion

**Files:**
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`
- Reference: `docs/plans/2026-03-15-assistant-service-extraction-design.md`
- Reference: `docs/plans/2026-03-15-assistant-service-extraction-implementation-plan.md`

**Step 1: Update the audit report**

Revise `docs/reports/2026-03-14-error-handling-and-logging-audit.md` to capture:

- the extracted assistant action and tool-result operation seams
- the thinner assistant-domain HTTP translation boundary
- what still remains after this pass, especially broader logging adoption and non-assistant `error_code` follow-up
- the new design and implementation plan docs as the current resume point

**Step 2: Run the focused assistant suite**

Run: `uv run pytest -q apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py`

Expected: PASS.

**Step 3: Run the broader backend safety checks**

Run: `uv run pytest -q`

Expected: PASS.

Run: `uv run ruff check src tests`

Expected: PASS.

If a broader check fails for an unrelated reason, record the exact failure in the audit update before stopping.

**Step 4: Commit the docs and verification-ready state**

```bash
git add docs/reports/2026-03-14-error-handling-and-logging-audit.md docs/plans/2026-03-15-assistant-service-extraction-design.md docs/plans/2026-03-15-assistant-service-extraction-implementation-plan.md
git commit -m "docs: plan assistant service extraction"
```

---

## Deferred Follow-up (Do Not Mix Into This Pass)

After this plan is complete and verified, the next backend-only follow-up should cover:

1. wider `log_context(...)` adoption across non-assistant success paths
2. remaining selective non-assistant `error_code` gaps
3. telemetry reconsideration only after the structured log/event field set stabilizes
