# WHM Tools Stabilization (JSON-Safe Tool Results) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent assistant transport and DB persistence from crashing when tools return non-JSON-native values (e.g. `datetime`, `UUID`).

**Architecture:** Add a small recursive JSON-safety sanitizer and apply it at key boundaries: tool execution (agent runner), tool-run persistence (ActionToolRunService), and tool-result message creation (assistant route/service). Use TDD to lock in behavior.

**Tech Stack:** FastAPI, SQLAlchemy (async), Postgres JSONB, pytest, Playwright smoke workflow.

---

### Task 1: Add `json_safe()` utility (core)

**Files:**
- Create: `apps/api/src/noa_api/core/json_safety.py`
- Test: `apps/api/tests/test_json_safety.py`

**Step 1: Write failing unit tests**

Create: `apps/api/tests/test_json_safety.py`

```python
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import uuid4

from noa_api.storage.postgres.lifecycle import ToolRisk


def test_json_safe_converts_datetime_date_uuid_enum_and_sets() -> None:
    from noa_api.core.json_safety import json_safe

    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    payload = {
        "now": now,
        "day": date(2026, 3, 13),
        "id": uuid4(),
        "risk": ToolRisk.READ,
        "tags": {"a", "b"},
    }

    safe = json_safe(payload)
    assert isinstance(safe, dict)
    assert safe["now"] == now.isoformat()
    assert safe["day"] == "2026-03-13"
    assert isinstance(safe["id"], str)
    assert safe["risk"] == "READ"
    assert sorted(safe["tags"]) == ["a", "b"]

    # Must be JSON-serializable.
    json.dumps(safe)
```

**Step 2: Run the test to confirm it fails**

Run: `cd apps/api && uv run pytest -q tests/test_json_safety.py`

Expected: FAIL with `ModuleNotFoundError` for `noa_api.core.json_safety` (or missing `json_safe`).

**Step 3: Implement minimal `json_safe()`**

Create: `apps/api/src/noa_api/core/json_safety.py`

```python
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Enum):
        return json_safe(value.value)

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]

    return str(value)
```

**Step 4: Re-run tests**

Run: `cd apps/api && uv run pytest -q tests/test_json_safety.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/json_safety.py apps/api/tests/test_json_safety.py
git commit -m "feat(api): add json-safe serialization helper"
```

---

### Task 2: Sanitize tool-run persistence via `ActionToolRunService`

**Files:**
- Modify: `apps/api/src/noa_api/storage/postgres/action_tool_runs.py`
- Modify: `apps/api/tests/test_action_tool_run_lifecycle.py`

**Step 1: Add a failing lifecycle test for non-JSON-native results**

Edit: `apps/api/tests/test_action_tool_run_lifecycle.py`

Add:

```python
from datetime import UTC, datetime
from uuid import uuid4


async def test_action_tool_run_service_sanitizes_non_json_result_values() -> None:
    repo = _FakeActionToolRunRepository(action_requests={}, tool_runs={})
    service = ActionToolRunService(repository=repo)

    run = await service.start_tool_run(
        thread_id=uuid4(),
        tool_name="test_tool",
        args={},
        action_request_id=None,
        requested_by_user_id=None,
    )

    raw = {"completed_at": datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)}
    completed = await service.complete_tool_run(tool_run_id=run.id, result=raw)

    assert completed is not None
    assert completed.result is not None
    assert completed.result["completed_at"] == "2026-03-13T12:00:00+00:00"
```

**Step 2: Run the test to confirm it fails**

Run: `cd apps/api && uv run pytest -q tests/test_action_tool_run_lifecycle.py::test_action_tool_run_service_sanitizes_non_json_result_values`

Expected: FAIL because `completed.result["completed_at"]` is a `datetime`, not a string.

**Step 3: Implement sanitization in the service layer**

Edit: `apps/api/src/noa_api/storage/postgres/action_tool_runs.py`

- Import `json_safe`.
- Before calling repository methods, sanitize `args` and `result`.

Sketch:

```python
from noa_api.core.json_safety import json_safe


def _safe_dict(value: object) -> dict[str, object]:
    safe = json_safe(value)
    if isinstance(safe, dict):
        return safe
    return {"value": safe}
```

Apply:

- `create_action_request(... args=...)` -> `args=_safe_dict(args)`
- `start_tool_run(... args=...)` -> `args=_safe_dict(args)`
- `complete_tool_run(... result=...)` -> `result=_safe_dict(result)`

**Step 4: Re-run the failing test**

Run: `cd apps/api && uv run pytest -q tests/test_action_tool_run_lifecycle.py::test_action_tool_run_service_sanitizes_non_json_result_values`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/storage/postgres/action_tool_runs.py apps/api/tests/test_action_tool_run_lifecycle.py
git commit -m "fix(api): sanitize tool run args/results for JSONB persistence"
```

---

### Task 3: Sanitize READ tool results emitted by `AgentRunner`

**Files:**
- Modify: `apps/api/src/noa_api/core/agent/runner.py`
- Modify: `apps/api/tests/test_agent_runner.py`

**Step 1: Add a failing agent runner test for tool-result message JSON safety**

Edit: `apps/api/tests/test_agent_runner.py`

Add:

```python
from datetime import UTC, datetime
from uuid import uuid4

from noa_api.core.tools.registry import ToolDefinition


async def test_agent_runner_sanitizes_tool_result_message_parts(monkeypatch) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def json_unsafe_tool() -> dict[str, object]:
        return {"when": datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC), "id": uuid4()}

    tool = ToolDefinition(
        name="json_unsafe_tool",
        description="Returns non-JSON-native values.",
        risk=ToolRisk.READ,
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        execute=json_unsafe_tool,
    )

    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_definition", lambda name: tool if name == tool.name else None)
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(self, *, messages, tools, on_text_delta=None):
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(text="", tool_calls=[LLMToolCall(name=tool.name, arguments={})])
            return LLMTurnResponse(text="done", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    tool_msg = next(m for m in result.messages if m.role == "tool")
    part = tool_msg.parts[0]
    assert isinstance(part, dict)
    tool_result = part.get("result")
    assert isinstance(tool_result, dict)
    assert tool_result["when"] == "2026-03-13T12:00:00+00:00"
    assert isinstance(tool_result["id"], str)
```

**Step 2: Run the test to confirm it fails**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_agent_runner_sanitizes_tool_result_message_parts`

Expected: FAIL because the tool-result message currently contains a `datetime`/`UUID`.

**Step 3: Sanitize tool-result message parts**

Edit: `apps/api/src/noa_api/core/agent/runner.py`

- Import `json_safe`.
- In `_process_tool_call`, after `result = await ...`, create `safe_result = json_safe(result)` and ensure it is a `dict`.
- Use `safe_result` for:
  - `complete_tool_run(..., result=safe_result)`
  - tool-result message `"result": safe_result`

**Step 4: Re-run the test**

Run: `cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_agent_runner_sanitizes_tool_result_message_parts`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/agent/runner.py apps/api/tests/test_agent_runner.py
git commit -m "fix(api): sanitize tool-result messages for JSON safety"
```

---

### Task 4: Sanitize tool-result messages in `AssistantService` (approval + add-tool-result)

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/tests/test_assistant_service.py`

**Step 1: Add a failing approval-path test**

Edit: `apps/api/tests/test_assistant_service.py`

Add:

```python
from datetime import UTC, datetime

from noa_api.core.tools.registry import ToolDefinition


async def test_assistant_service_sanitizes_tool_result_messages_for_change_tools(monkeypatch) -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    request = await repo.create_action_request(
        thread_id=thread_id,
        tool_name="json_unsafe_change",
        args={},
        risk=ToolRisk.CHANGE,
        requested_by_user_id=owner_id,
    )

    async def change_tool(*, session, **kwargs):
        _ = session, kwargs
        return {"when": datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)}

    tool = ToolDefinition(
        name="json_unsafe_change",
        description="Returns non-JSON-native values.",
        risk=ToolRisk.CHANGE,
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        execute=change_tool,
    )

    monkeypatch.setattr("noa_api.api.routes.assistant.get_tool_definition", lambda name: tool if name == tool.name else None)

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

    tool_message = assistant_repo.messages[-1]
    assert tool_message["role"] == "tool"
    part = tool_message["parts"][0]
    assert part["type"] == "tool-result"
    assert part["result"]["when"] == "2026-03-13T12:00:00+00:00"
```

**Step 2: Run the test to confirm it fails**

Run: `cd apps/api && uv run pytest -q tests/test_assistant_service.py::test_assistant_service_sanitizes_tool_result_messages_for_change_tools`

Expected: FAIL because `AssistantService` currently stores the raw tool result into message parts.

**Step 3: Use sanitized/stored tool-run results when creating tool-result messages**

Edit: `apps/api/src/noa_api/api/routes/assistant.py`

- In `approve_action` success path:
  - capture the return value from `complete_tool_run(...)`.
  - use `completed.result` (already sanitized by `ActionToolRunService`) for the tool-result message.

- In `add_tool_result`:
  - capture return value from `complete_tool_run(...)`.
  - use `completed.result` for message persistence.

**Step 4: Re-run the test**

Run: `cd apps/api && uv run pytest -q tests/test_assistant_service.py::test_assistant_service_sanitizes_tool_result_messages_for_change_tools`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant_service.py
git commit -m "fix(api): sanitize assistant tool-result message persistence"
```

---

### Task 5: Verification (tests + smoke)

**Files:**
- (artifacts only)

**Step 1: Run API unit tests**

Run: `cd apps/api && uv run pytest -q`

Expected: PASS.

**Step 2: Re-run the NOA Playwright smoke workflow**

- Follow the `noa-playwright-smoke` recipe.
- In the assistant UI, run a new chat prompt that previously broke streaming:
  - `Call the tool whm_list_servers now and return only short result.`

Expected:
- `/api/assistant` completes without stream interruption.
- No `TypeError: Object of type datetime is not JSON serializable` in API logs.
- The tool-result renders and the conversation continues.

**Step 3: (Optional) Commit the design+plan docs**

```bash
git add docs/plans/2026-03-13-whm-tools-stabilization-design.md docs/plans/2026-03-13-whm-tools-stabilization-implementation-plan.md
git commit -m "docs(plans): add WHM tool result JSON-safety plan"
```
