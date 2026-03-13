# WHM Tools + Workflow TODO Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add WHM server inventory + WHM/CSF tools (with approval + auto preflight) and an in-chat workflow TODO tool card.

**Architecture:** Implement WHM as standard NOA backend tools in `noa_api.core.tools.registry` (no dedicated agent). Persist WHM servers in Postgres and expose admin-only CRUD/validate endpoints. Add a safe “workflow todo” tool that emits tool cards in chat and is always available.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, Pydantic v2, assistant-ui (tool UIs), httpx.

---

### Task 1: Add runtime dependency for WHM HTTP calls

**Files:**
- Modify: `apps/api/pyproject.toml`

**Step 1: Write a minimal import test**

Create: `apps/api/tests/test_httpx_dependency.py`

```python
from __future__ import annotations


def test_httpx_is_available() -> None:
    import httpx  # noqa: F401
```

**Step 2: Run the test to confirm it fails (until dependency is added)**

Run: `cd apps/api && uv run pytest -q tests/test_httpx_dependency.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'httpx'` (if httpx isn’t installed in runtime env).

**Step 3: Add httpx to runtime dependencies**

Edit: `apps/api/pyproject.toml`

- Add `"httpx>=0.27.0"` to `[project].dependencies`.

**Step 4: Sync deps and re-run the test**

Run:

- `cd apps/api && uv sync`
- `cd apps/api && uv run pytest -q tests/test_httpx_dependency.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/pyproject.toml apps/api/tests/test_httpx_dependency.py
git commit -m "chore(api): add httpx runtime dependency"
```

### Task 2: Add WHM server model + migration

**Files:**
- Modify: `apps/api/src/noa_api/storage/postgres/models.py`
- Create: `apps/api/alembic/versions/0003_whm_servers.py`
- Test: `apps/api/tests/test_whm_server_model.py`

**Step 1: Write failing unit test for safe serialization**

Create: `apps/api/tests/test_whm_server_model.py`

```python
from __future__ import annotations

from uuid import uuid4


async def test_whm_server_to_safe_dict_excludes_api_token() -> None:
    from noa_api.storage.postgres.models import WHMServer

    server = WHMServer(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
    )
    safe = server.to_safe_dict()
    assert safe["name"] == "web1"
    assert "api_token" not in safe
```

**Step 2: Run the test to confirm it fails (model missing)**

Run: `cd apps/api && uv run pytest -q tests/test_whm_server_model.py`

Expected: FAIL with `ImportError` / `AttributeError` for `WHMServer`.

**Step 3: Implement `WHMServer` model**

Edit: `apps/api/src/noa_api/storage/postgres/models.py`

Add a new SQLAlchemy model:

```python
class WHMServer(Base):
    __tablename__ = "whm_servers"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_username: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token: Mapped[str] = mapped_column(Text, nullable=False)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "base_url": self.base_url,
            "api_username": self.api_username,
            "verify_ssl": self.verify_ssl,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

**Step 4: Re-run unit test**

Run: `cd apps/api && uv run pytest -q tests/test_whm_server_model.py`

Expected: PASS.

**Step 5: Create Alembic migration**

Create: `apps/api/alembic/versions/0003_whm_servers.py`

Migration should create `whm_servers` with:

- `id` UUID default `gen_random_uuid()`
- unique index on `name`
- columns: `name`, `base_url`, `api_username`, `api_token`, `verify_ssl`, `created_at`, `updated_at`

**Step 6: Run migrations**

Run:

- `docker compose up -d postgres`
- `cd apps/api && uv run alembic upgrade head`

Expected: upgrade succeeds.

**Step 7: Commit**

```bash
git add apps/api/src/noa_api/storage/postgres/models.py apps/api/tests/test_whm_server_model.py apps/api/alembic/versions/0003_whm_servers.py
git commit -m "feat(api): add WHM server model and migration"
```

### Task 3: Add WHM server repository + server_ref resolver

**Files:**
- Create: `apps/api/src/noa_api/storage/postgres/whm_servers.py`
- Create: `apps/api/src/noa_api/core/whm/server_ref.py`
- Test: `apps/api/tests/test_whm_server_ref.py`

**Step 1: Write failing tests for server_ref resolution**

Create: `apps/api/tests/test_whm_server_ref.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest


@dataclass
class _Server:
    id: UUID
    name: str
    base_url: str


class _Repo:
    def __init__(self, servers: list[_Server]) -> None:
        self._servers = servers

    async def list_servers(self) -> list[_Server]:
        return self._servers

    async def get_by_id(self, server_id: UUID):
        for s in self._servers:
            if s.id == server_id:
                return s
        return None


@pytest.mark.asyncio
async def test_resolve_by_uuid() -> None:
    from noa_api.core.whm.server_ref import resolve_whm_server_ref

    target = _Server(id=uuid4(), name="web1", base_url="https://whm.example.com:2087")
    repo = _Repo([target])
    result = await resolve_whm_server_ref(str(target.id), repo=repo)
    assert result.ok is True
    assert result.server_id == target.id


@pytest.mark.asyncio
async def test_resolve_ambiguous_by_name_returns_choices() -> None:
    from noa_api.core.whm.server_ref import resolve_whm_server_ref

    a = _Server(id=uuid4(), name="web1", base_url="https://a.example.com:2087")
    b = _Server(id=uuid4(), name="web1", base_url="https://b.example.com:2087")
    repo = _Repo([a, b])
    result = await resolve_whm_server_ref("web1", repo=repo)
    assert result.ok is False
    assert result.error_code == "host_ambiguous"
    assert len(result.choices) >= 2
```

**Step 2: Run tests to confirm they fail (module missing)**

Run: `cd apps/api && uv run pytest -q tests/test_whm_server_ref.py`

Expected: FAIL.

**Step 3: Implement repository protocol + SQL repo**

Create: `apps/api/src/noa_api/storage/postgres/whm_servers.py`

Include:

- `WHMServerRepositoryProtocol` with `list_servers`, `get_by_id`, `get_by_name`, `create`, `update`, `delete`.
- `SQLWHMServerRepository(session)` implementation.

**Step 4: Implement resolver**

Create: `apps/api/src/noa_api/core/whm/server_ref.py`

Implement:

- Parse UUID (if valid) and lookup by id.
- Else exact case-insensitive match by name.
- Else hostname-from-base_url exact match.
- If multiple matches -> `host_ambiguous` with bounded `choices`.

Return a small Pydantic/dataclass result:

```python
@dataclass(frozen=True)
class WHMServerRefResolution:
    ok: bool
    server_id: UUID | None
    server: object | None
    error_code: str | None
    message: str
    choices: list[dict[str, str]]
```

**Step 5: Re-run tests**

Run: `cd apps/api && uv run pytest -q tests/test_whm_server_ref.py`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/storage/postgres/whm_servers.py apps/api/src/noa_api/core/whm/server_ref.py apps/api/tests/test_whm_server_ref.py
git commit -m "feat(api): add WHM server repository and resolver"
```

### Task 4: Add WHM admin routes (CRUD + validate)

**Files:**
- Create: `apps/api/src/noa_api/api/routes/whm_admin.py`
- Modify: `apps/api/src/noa_api/api/router.py`
- Test: `apps/api/tests/test_whm_admin_routes.py`

**Step 1: Write failing route tests using dependency overrides**

Create: `apps/api/tests/test_whm_admin_routes.py`

Test strategy:

- Build a `FastAPI()` app and include the router.
- Override `get_current_auth_user` to return an admin user.
- Provide a fake WHMServerService dependency that stores servers in-memory.
- Assert `api_token` never appears in JSON response.

**Step 2: Implement routes**

Create: `apps/api/src/noa_api/api/routes/whm_admin.py`

Recommended prefix: `/admin/whm/servers`.

Include Pydantic request/response models and admin requirement mirroring `apps/api/src/noa_api/api/routes/admin.py`.

Implement `validate` by calling the WHM client `applist` and returning normalized result.

**Step 3: Wire router**

Edit: `apps/api/src/noa_api/api/router.py`

- `include_router(whm_admin_router)`.

**Step 4: Run tests**

Run: `cd apps/api && uv run pytest -q tests/test_whm_admin_routes.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/routes/whm_admin.py apps/api/src/noa_api/api/router.py apps/api/tests/test_whm_admin_routes.py
git commit -m "feat(api): add WHM admin server APIs"
```

### Task 5: Add WHM client + CSF parsing utilities

**Files:**
- Create: `apps/api/src/noa_api/integrations/whm/__init__.py`
- Create: `apps/api/src/noa_api/integrations/whm/client.py`
- Create: `apps/api/src/noa_api/integrations/whm/csf.py`
- Test: `apps/api/tests/test_whm_csf_parsing.py`
- Test: `apps/api/tests/test_whm_client_normalization.py`

**Step 1: Write failing CSF parsing tests**

Create: `apps/api/tests/test_whm_csf_parsing.py`

Include tests for:

- `parse_csf_target("1.2.3.4")` -> ip
- `parse_csf_target("1.2.3.0/24")` -> cidr
- parsing sample CSF grep HTML -> returns verdict + bounded matches

**Step 2: Implement CSF parsing module**

Create: `apps/api/src/noa_api/integrations/whm/csf.py`

Port from `noa-old/src/integrations/whm/csf.py` with minimal edits.

**Step 3: Write failing WHM client tests using httpx.MockTransport**

Create: `apps/api/tests/test_whm_client_normalization.py`

Use `httpx.MockTransport` to simulate:

- 401/403 -> `auth_failed`
- timeout -> `timeout`
- non-JSON -> `invalid_response`
- metadata.result != 1 -> `whm_api_error`

**Step 4: Implement WHMClient**

Create: `apps/api/src/noa_api/integrations/whm/client.py`

Port and adapt `noa-old/src/integrations/whm/client.py`:

- Provide `applist`, `list_accounts`, `suspend_account`, `unsuspend_account`, `change_contact_email`.
- Provide CSF plugin helpers: `csf_grep`, `csf_request_action`.
- Keep responses normalized and avoid returning raw payloads for mutations.
- Constructor takes `timeout_seconds` and allows injecting a `transport` (for tests).

**Step 5: Run tests**

Run:

- `cd apps/api && uv run pytest -q tests/test_whm_csf_parsing.py`
- `cd apps/api && uv run pytest -q tests/test_whm_client_normalization.py`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/integrations/whm apps/api/tests/test_whm_csf_parsing.py apps/api/tests/test_whm_client_normalization.py
git commit -m "feat(api): add WHM client and CSF parsing"
```

### Task 6: Add workflow TODO tool (and make it always available)

**Files:**
- Create: `apps/api/src/noa_api/core/tools/workflow_todo.py`
- Modify: `apps/api/src/noa_api/core/tools/registry.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Test: `apps/api/tests/test_workflow_todo_tool.py`

**Step 1: Write failing unit test for tool behavior**

Create: `apps/api/tests/test_workflow_todo_tool.py`

```python
from __future__ import annotations


async def test_update_workflow_todo_echoes_list() -> None:
    from noa_api.core.tools.workflow_todo import update_workflow_todo

    todos = [
        {"content": "Preflight", "status": "in_progress", "priority": "high"},
        {"content": "Request approval", "status": "pending", "priority": "high"},
    ]
    result = await update_workflow_todo(todos=todos)
    assert result["ok"] is True
    assert result["todos"] == todos
```

**Step 2: Implement tool**

Create: `apps/api/src/noa_api/core/tools/workflow_todo.py`

```python
from __future__ import annotations

from typing import Any


async def update_workflow_todo(*, todos: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": True, "todos": todos}
```

**Step 3: Register tool in tool registry**

Edit: `apps/api/src/noa_api/core/tools/registry.py`

- Add `ToolDefinition`:
  - `name="update_workflow_todo"`
  - `risk=ToolRisk.READ`
  - strict parameters schema for the todo list

**Step 4: Make tool always available for active users**

Edit: `apps/api/src/noa_api/api/routes/assistant.py`

When building `allowed_tools`, include `update_workflow_todo` unconditionally (for active users) so it does not require allowlisting.

**Step 5: Run tests**

Run: `cd apps/api && uv run pytest -q tests/test_workflow_todo_tool.py`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/core/tools/workflow_todo.py apps/api/src/noa_api/core/tools/registry.py apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_workflow_todo_tool.py
git commit -m "feat(api): add workflow todo tool"
```

### Task 7: Add WHM tools (READ + preflight)

**Files:**
- Create: `apps/api/src/noa_api/core/tools/whm/__init__.py`
- Create: `apps/api/src/noa_api/core/tools/whm/read_tools.py`
- Create: `apps/api/src/noa_api/core/tools/whm/preflight_tools.py`
- Modify: `apps/api/src/noa_api/core/tools/registry.py`
- Test: `apps/api/tests/test_whm_tools_read.py`

**Step 1: Write failing tests for schemas + basic error handling**

Create: `apps/api/tests/test_whm_tools_read.py`

Test scope (unit-level, no real HTTP):

- Resolver errors return structured `error_code` + `choices`.
- `whm_list_servers` never returns tokens.

Use an in-memory repository + fake session object pattern (similar to other tests).

**Step 2: Implement read tools**

Create: `apps/api/src/noa_api/core/tools/whm/read_tools.py`

Implement async tool functions accepting `session: AsyncSession` and using `SQLWHMServerRepository` + `WHMClient`.

Tools:

- `whm_list_servers`
- `whm_validate_server`
- `whm_list_accounts`
- `whm_search_accounts`

**Step 3: Implement preflight tools**

Create: `apps/api/src/noa_api/core/tools/whm/preflight_tools.py`

Tools:

- `whm_preflight_account`
- `whm_preflight_csf_entries`

Use CSF parsing module to return bounded evidence.

**Step 4: Register tools in registry**

Edit: `apps/api/src/noa_api/core/tools/registry.py` to include these tool definitions and parameter schemas.

**Step 5: Run tests**

Run: `cd apps/api && uv run pytest -q tests/test_whm_tools_read.py`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/core/tools/whm apps/api/src/noa_api/core/tools/registry.py apps/api/tests/test_whm_tools_read.py
git commit -m "feat(api): add WHM read and preflight tools"
```

### Task 8: Add WHM tools (CHANGE: account actions)

**Files:**
- Create: `apps/api/src/noa_api/core/tools/whm/account_change_tools.py`
- Modify: `apps/api/src/noa_api/core/tools/registry.py`
- Test: `apps/api/tests/test_whm_tools_account_change.py`

**Step 1: Write failing tests for idempotency behavior**

Create: `apps/api/tests/test_whm_tools_account_change.py`

Test with a fake WHMClient that:

- Returns preflight state (suspended/contact email)
- Tracks whether a mutation method was invoked

Assertions:

- If already suspended, `whm_suspend_account` returns `no-op` and does not call mutation.
- If contact email already matches, `whm_change_contact_email` returns `no-op`.

**Step 2: Implement change tools**

Create: `apps/api/src/noa_api/core/tools/whm/account_change_tools.py`

Implement:

- `whm_suspend_account`
- `whm_unsuspend_account`
- `whm_change_contact_email`

Each tool:

- Requires `reason`.
- Preflight via `listaccts` exact search for username.
- Mutate only if needed.
- Postflight verify (re-check account state).

**Step 3: Register tools**

Edit: `apps/api/src/noa_api/core/tools/registry.py` with ToolRisk.CHANGE.

**Step 4: Run tests**

Run: `cd apps/api && uv run pytest -q tests/test_whm_tools_account_change.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/tools/whm/account_change_tools.py apps/api/src/noa_api/core/tools/registry.py apps/api/tests/test_whm_tools_account_change.py
git commit -m "feat(api): add WHM account change tools"
```

### Task 9: Add WHM tools (CHANGE: CSF actions + TTL)

**Files:**
- Create: `apps/api/src/noa_api/core/tools/whm/csf_change_tools.py`
- Modify: `apps/api/src/noa_api/core/tools/registry.py`
- Test: `apps/api/tests/test_whm_tools_csf_change.py`

**Step 1: Write failing tests for CSF multi-entry results + TTL minutes conversion**

Create: `apps/api/tests/test_whm_tools_csf_change.py`

Use fake WHMClient + fake CSF grep HTML outputs to simulate:

- blocked -> unblock actionable
- not blocked -> no-op
- TTL allowlist add rejects CIDR/IPv6 with per-entry error

**Step 2: Implement CSF change tools**

Create: `apps/api/src/noa_api/core/tools/whm/csf_change_tools.py`

Implement tools:

- `whm_csf_unblock`
- `whm_csf_allowlist_remove`
- `whm_csf_allowlist_add_ttl`
- `whm_csf_denylist_add_ttl`

Implementation notes:

- Preflight via `csf_grep` + CSF parsing.
- For TTL tools: accept `duration_minutes` and convert into `timeout` + `dur` for CSF plugin request.
- Postflight via `csf_grep` to confirm effective verdict changed as expected.

**Step 3: Register tools**

Edit: `apps/api/src/noa_api/core/tools/registry.py`.

**Step 4: Run tests**

Run: `cd apps/api && uv run pytest -q tests/test_whm_tools_csf_change.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/tools/whm/csf_change_tools.py apps/api/src/noa_api/core/tools/registry.py apps/api/tests/test_whm_tools_csf_change.py
git commit -m "feat(api): add WHM CSF change tools"
```

### Task 10: Add web tool UI for workflow TODO (in-chat)

**Files:**
- Create: `apps/web/components/claude/workflow-todo-tool-ui.tsx`
- Modify: `apps/web/components/claude/claude-workspace.tsx`
- Test: `apps/web/components/claude/workflow-todo-tool-ui.test.tsx`

**Step 1: Write failing UI test**

Create: `apps/web/components/claude/workflow-todo-tool-ui.test.tsx`

Test that the renderer:

- Shows each todo item content.
- Shows a visual status label.

**Step 2: Implement tool UI**

Create: `apps/web/components/claude/workflow-todo-tool-ui.tsx`

Use `makeAssistantToolUI({ toolName: "update_workflow_todo", render })`.

Render a compact checklist card using `args.todos` (or `result.todos` fallback).

**Step 3: Register tool UI**

Edit: `apps/web/components/claude/claude-workspace.tsx`

- Add `<WorkflowTodoToolUI />` near `<RequestApprovalToolUI />`.

**Step 4: Run web tests**

Run: `cd apps/web && npm test` (or if no test runner exists, add a minimal one OR run `npm run build` as verification).

Expected: build/typecheck passes.

**Step 5: Commit**

```bash
git add apps/web/components/claude/workflow-todo-tool-ui.tsx apps/web/components/claude/workflow-todo-tool-ui.test.tsx apps/web/components/claude/claude-workspace.tsx
git commit -m "feat(web): render workflow todo tool cards"
```

### Task 11: Update default system prompt for WHM workflows

**Files:**
- Modify: `apps/api/src/noa_api/core/config.py`

**Step 1: Update default prompt text**

Edit: `apps/api/src/noa_api/core/config.py` `Settings.llm_system_prompt` default to include:

- Create/update workflow TODOs.
- Preflight before WHM CHANGE tools.
- Convert durations to minutes for TTL.

**Step 2: Run API tests**

Run: `cd apps/api && uv run pytest -q`

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/api/src/noa_api/core/config.py
git commit -m "chore(api): improve default LLM system prompt for WHM"
```

### Task 12: End-to-end manual verification

**Step 1: Start Postgres + API + Web**

Run:

- `docker compose up -d postgres`
- `cd apps/api && uv run alembic upgrade head && uv run uvicorn noa_api.main:app --reload --port 8000`
- `cd apps/web && npm install && npm run dev`

**Step 2: Add WHM server via Admin UI**

- Confirm server is listed (token not shown).
- Run validate and confirm success.

**Step 3: Simulate “release IP” workflow**

Prompt example:

"Release IP 1.2.3.4 on web1 because customer got blocked"

Expected behavior:

- A workflow TODO card appears with steps.
- A preflight tool runs and shows CSF evidence/verdict.
- A CHANGE tool is proposed and approval is requested.
- After approve, tool executes and returns postflight verification.
- TODO updates to completed.
