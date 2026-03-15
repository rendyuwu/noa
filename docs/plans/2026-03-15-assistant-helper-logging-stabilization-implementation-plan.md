# Assistant Helper Logging Stabilization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Pin the remaining assistant helper failure-event vocabulary with focused tests so backend telemetry can be reconsidered against a stable helper-level field set.

**Architecture:** Keep the change local to the assistant helper seam. Add focused structured-log assertions around `run_agent_phase(...)` in `apps/api/src/noa_api/api/routes/assistant_operations.py`, then make only the smallest production code change needed to keep those helper events stable. Do not reopen route-level logging or broader `error_code` work.

**Tech Stack:** FastAPI-adjacent helper tests, stdlib logging, structlog JSON formatting, pytest, Ruff

---

### Task 1: Add structured-log capture helpers and a failing agent-failure test

**Files:**
- Modify: `apps/api/tests/test_assistant_operations.py`

**Step 1: Add the structured-log capture helpers near the top of `apps/api/tests/test_assistant_operations.py`**

Add the same minimal helper shape already used in `apps/api/tests/test_auth_login.py`:

```python
import io
import json
import logging
from contextlib import contextmanager
from collections.abc import Iterator

from noa_api.core.logging import configure_logging
from noa_api.core.logging_context import log_context


@contextmanager
def _capture_structured_logs() -> Iterator[io.StringIO]:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_formatters = {
        id(handler): handler.formatter for handler in original_handlers
    }
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    try:
        configure_logging()
        yield stream
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for existing_handler in original_handlers:
            existing_handler.setFormatter(original_formatters[id(existing_handler)])


def _load_log_payloads(stream: io.StringIO) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in stream.getvalue().splitlines()
        if line.strip()
    ]
```

**Step 2: Write the failing direct helper test for `assistant_run_failed_agent`**

Add a test like this below the existing `run_agent_phase(...)` tests:

```python
async def test_run_agent_phase_emits_structured_agent_failure_log() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")]
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})

    with _capture_structured_logs() as stream:
        with log_context(
            assistant_command_types=["add-message"],
            thread_id=str(thread_id),
            user_id=str(current_user.user_id),
        ):
            await assistant_operations.run_agent_phase(
                controller=controller,
                payload=_payload_with_user_message(thread_id),
                current_user=current_user,
                assistant_service=service,
                authorization_service=_FakeAuthorizationService(),
                canonical_state={"messages": list(service.base_messages), "isRunning": False},
                command_types=["add-message"],
            )

    payload = next(
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_run_failed_agent"
    )
    assert payload["assistant_command_types"] == ["add-message"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["user_id"] == str(current_user.user_id)
    assert payload["error_type"] == "RuntimeError"
```

**Step 3: Run the new test to verify the current behavior**

Run: `uv run pytest -q tests/test_assistant_operations.py -k agent_failure_log`

Expected: either PASS immediately or FAIL with a missing/incorrect helper field. If it passes immediately, keep the test as the stabilization guardrail.

**Step 4: Commit if you are making commits in this session**

```bash
git add apps/api/tests/test_assistant_operations.py
git commit -m "test(api): pin assistant agent failure logs"
```

### Task 2: Add failing fallback-failure helper log tests

**Files:**
- Modify: `apps/api/tests/test_assistant_operations.py`

**Step 1: Write the failing test for `assistant_error_message_persist_failed`**

Use the existing `_FakeAssistantServiceThatFailsAgentRun` with `fail_error_persistence=True`:

```python
async def test_run_agent_phase_emits_structured_error_persist_failure_log() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    service = _FakeAssistantServiceThatFailsAgentRun(
        base_messages=[_message_with_text("Trigger agent")],
        fail_error_persistence=True,
    )
    controller = _FakeController(state={"messages": [], "isRunning": True})

    with _capture_structured_logs() as stream:
        with log_context(
            assistant_command_types=["add-message"],
            thread_id=str(thread_id),
            user_id=str(current_user.user_id),
        ):
            await assistant_operations.run_agent_phase(
                controller=controller,
                payload=_payload_with_user_message(thread_id),
                current_user=current_user,
                assistant_service=service,
                authorization_service=_FakeAuthorizationService(),
                canonical_state={"messages": list(service.base_messages), "isRunning": False},
                command_types=["add-message"],
            )

    payload = next(
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_error_message_persist_failed"
    )
    assert payload["assistant_command_types"] == ["add-message"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["user_id"] == str(current_user.user_id)
```

**Step 2: Add a fake successful-agent service for refresh-failure coverage**

Add a minimal helper in the same test file:

```python
@dataclass
class _FakeAssistantServiceThatFailsStateRefresh:
    async def run_agent_turn(self, *, owner_user_id: UUID, owner_user_email: str | None, thread_id: UUID, available_tool_names: set[str], on_text_delta: Any = None) -> None:
        _ = owner_user_id, owner_user_email, thread_id, available_tool_names, on_text_delta

    async def add_message(self, *, owner_user_id: UUID, thread_id: UUID, role: str, parts: list[dict[str, object]]) -> None:
        _ = owner_user_id, thread_id, role, parts

    async def load_state(self, *, owner_user_id: UUID, thread_id: UUID) -> dict[str, object]:
        _ = owner_user_id, thread_id
        raise RuntimeError("refresh failed")
```

**Step 3: Write the failing test for `assistant_state_refresh_failed`**

```python
async def test_run_agent_phase_emits_structured_state_refresh_failure_log() -> None:
    current_user = _active_user()
    thread_id = uuid4()
    controller = _FakeController(state={"messages": [], "isRunning": True})

    with _capture_structured_logs() as stream:
        with log_context(
            assistant_command_types=["add-message"],
            thread_id=str(thread_id),
            user_id=str(current_user.user_id),
        ):
            await assistant_operations.run_agent_phase(
                controller=controller,
                payload=_payload_with_user_message(thread_id),
                current_user=current_user,
                assistant_service=_FakeAssistantServiceThatFailsStateRefresh(),
                authorization_service=_FakeAuthorizationService(),
                canonical_state={"messages": [], "isRunning": False},
                command_types=["add-message"],
            )

    payload = next(
        payload
        for payload in _load_log_payloads(stream)
        if payload["event"] == "assistant_state_refresh_failed"
    )
    assert payload["assistant_command_types"] == ["add-message"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["user_id"] == str(current_user.user_id)
```

**Step 4: Run the new tests to verify the gap**

Run: `uv run pytest -q tests/test_assistant_operations.py -k "persist_failure_log or state_refresh_failure_log"`

Expected: either PASS immediately or FAIL with missing context fields on one of the two helper events.

**Step 5: Commit if you are making commits in this session**

```bash
git add apps/api/tests/test_assistant_operations.py
git commit -m "test(api): pin assistant fallback failure logs"
```

### Task 3: Make the smallest production change only if the tests prove it is needed

**Files:**
- Modify if needed: `apps/api/src/noa_api/api/routes/assistant_operations.py`
- Verify: `apps/api/tests/test_assistant_operations.py`

**Step 1: Inspect the failing assertion**

If any helper log test fails, identify whether the missing field should come from the caller's `log_context(...)` or from explicit `extra={...}` on the helper log call.

**Step 2: Prefer preserving the existing outer-context model**

Only add explicit `extra` fields if the rendered JSON drops fields that should be stable for telemetry readiness. Keep the existing event names unless the test proves one is misleading.

Minimal acceptable shape if a fix is required:

```python
logger.exception(
    "assistant_state_refresh_failed",
    extra={
        "assistant_command_types": command_types,
        "thread_id": str(payload.thread_id),
        "user_id": str(current_user.user_id),
        "error_type": "RuntimeError",
    },
)
```

Do not add `request_id` here unless it already flows from the outer request context.

**Step 3: Re-run the focused helper tests**

Run: `uv run pytest -q tests/test_assistant_operations.py -k "agent_failure_log or persist_failure_log or state_refresh_failure_log"`

Expected: PASS

**Step 4: Commit if you are making commits in this session**

```bash
git add apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/tests/test_assistant_operations.py
git commit -m "fix(api): stabilize assistant helper failure logs"
```

### Task 4: Run focused assistant verification

**Files:**
- Verify: `apps/api/tests/test_assistant_operations.py`
- Verify: `apps/api/tests/test_assistant.py`
- Verify: `apps/api/tests/test_assistant_service.py`

**Step 1: Run the focused assistant slice**

Run: `uv run pytest -q tests/test_assistant_operations.py tests/test_assistant.py tests/test_assistant_service.py`

Expected: PASS

**Step 2: Run Ruff for the touched backend files**

Run: `uv run ruff check src tests`

Expected: `All checks passed!`

**Step 3: Commit if you are making commits in this session**

```bash
git add apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/tests/test_assistant_operations.py
git commit -m "test(api): verify assistant helper log stability"
```

### Task 5: Run full backend verification and reassess telemetry readiness

**Files:**
- Review: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`
- Review: `docs/plans/2026-03-15-assistant-helper-logging-stabilization-design.md`
- Review: `docs/plans/2026-03-15-assistant-helper-logging-stabilization-implementation-plan.md`

**Step 1: Run the full backend suite**

Run: `uv run pytest -q`

Expected: PASS

**Step 2: Review the final diff**

Run: `git diff -- apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/tests/test_assistant_operations.py docs/plans/2026-03-15-assistant-helper-logging-stabilization-design.md docs/plans/2026-03-15-assistant-helper-logging-stabilization-implementation-plan.md`

Expected: only the targeted helper stabilization and planning docs are changed.

**Step 3: Reassess whether telemetry revisit is now ready**

Use the verification results plus the newly pinned helper events to answer:

- are the remaining assistant helper failure events now test-pinned?
- does the audit still need to say telemetry is deferred, or is there now enough stability for a dedicated telemetry design pass?

Do not update `docs/reports/2026-03-14-error-handling-and-logging-audit.md` in this pass unless that readiness judgment materially changes.

**Step 4: Commit if you are making commits in this session**

```bash
git add apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/tests/test_assistant_operations.py docs/plans/2026-03-15-assistant-helper-logging-stabilization-design.md docs/plans/2026-03-15-assistant-helper-logging-stabilization-implementation-plan.md
git commit -m "test(api): pin assistant helper failure log fields"
```
