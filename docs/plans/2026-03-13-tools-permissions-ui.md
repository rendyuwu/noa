# Tools Permissions + Admin Users UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Default the admin sidebar to open on desktop, expand the Admin Users table with Created/Last login + clearer status, and require explicit tool allowlisting for all users (including admins) with clear permission-denied feedback.

**Architecture:** Keep the existing per-user allowlist model (`user:{id}` role tool permissions). Remove the admin tool bypass so every tool invocation is gated by allowlist + active status. Record `last_login_at` on successful login and expose `created_at`/`last_login_at` in admin user APIs for UI display.

**Tech Stack:** Next.js (apps/web) + Vitest/RTL, FastAPI (apps/api) + Pydantic + SQLAlchemy + Alembic + Pytest.

---

### Task 0: Prep a worktree/branch

**Files:** none

**Step 1: Create a feature branch (or worktree)**

Run (branch):

```bash
git checkout -b feat/tools-permissions-ui
```

Or (worktree):

```bash
git worktree add ../noa-tools-permissions-ui -b feat/tools-permissions-ui
```

Expected: new branch exists and your edits are isolated.

---

### Task 1: Admin sidebar defaults open on desktop

**Files:**
- Modify: `apps/web/components/admin/admin-sidebar-shell.tsx`
- Test: `apps/web/components/admin/admin-sidebar-shell.test.tsx`

**Step 1: Update/replace the failing test to match “open by default on desktop”**

In `apps/web/components/admin/admin-sidebar-shell.test.tsx`, update the first test to expect an expanded desktop layout and the sidebar thread list present.

Example expectation changes:

```ts
expect(container.firstElementChild).toHaveClass("md:grid-cols-[18rem_minmax(0,1fr)]");
expect(screen.getByTestId("sidebar-thread-list")).toBeInTheDocument();
```

Also update the “expand” test to validate the close action instead:

```ts
fireEvent.click(screen.getByRole("button", { name: "Close sidebar" }));
expect(container.firstElementChild).toHaveClass("md:grid-cols-1");
```

**Step 2: Run the test to confirm failure**

Run:

```bash
cd apps/web && npm test -- components/admin/admin-sidebar-shell.test.tsx
```

Expected: FAIL because the sidebar still starts collapsed.

**Step 3: Implement “open by default on desktop” without mobile duplicates**

In `apps/web/components/admin/admin-sidebar-shell.tsx`, keep the initial state deterministic (to avoid hydration mismatch), but open the desktop sidebar on mount when the desktop media query matches.

Add inside the existing `useEffect` that reads `matchMedia`:

```ts
closeOnDesktop(mediaQuery);
if (mediaQuery.matches) {
  setDesktopSidebarOpen(true);
}
```

Notes:
- This preserves mobile behavior (starts closed).
- This avoids rendering the desktop sidebar by default on mobile (which would create duplicate nav DOM in tests).

**Step 4: Re-run the test**

Run:

```bash
cd apps/web && npm test -- components/admin/admin-sidebar-shell.test.tsx
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/components/admin/admin-sidebar-shell.tsx apps/web/components/admin/admin-sidebar-shell.test.tsx
git commit -m "feat(web): default admin sidebar open on desktop"
```

---

### Task 2: Record `last_login_at` on successful login

**Files:**
- Modify: `apps/api/src/noa_api/core/auth/auth_service.py`
- Modify: `apps/api/src/noa_api/core/auth/auth_service.py` (protocol + SQL repo)
- Test: `apps/api/tests/test_auth_login.py`

**Step 1: Write a failing test for `last_login_at` on success**

In `apps/api/tests/test_auth_login.py`:

- Extend the in-memory `_User` to include `last_login_at: datetime | None = None`.
- Extend `_InMemoryAuthRepository.update_user(...)` to accept `last_login_at: datetime | None = None` and set it when provided.
- In `test_auth_service_bootstrap_admin_auto_active_and_issues_jwt`, assert `repo.users["admin@example.com"].last_login_at is not None` after `authenticate(...)`.

**Step 2: Run the test to confirm failure**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_auth_login.py::test_auth_service_bootstrap_admin_auto_active_and_issues_jwt
```

Expected: FAIL because `last_login_at` is never updated.

**Step 3: Implement `last_login_at` update in AuthService**

In `apps/api/src/noa_api/core/auth/auth_service.py`:

- Import `datetime` and `UTC`:

```py
from datetime import UTC, datetime
```

- Update `AuthRepositoryProtocol.update_user(...)` to accept `last_login_at: datetime | None = None`.
- Update `SQLAuthRepository.update_user(...)` to set `user.last_login_at = last_login_at` when provided.
- In `AuthService.authenticate(...)`, after the `if not user.is_active: raise AuthPendingApprovalError(...)` check, call:

```py
user = await self._auth_repository.update_user(user, last_login_at=datetime.now(UTC))
```

**Step 4: Re-run the targeted auth test**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_auth_login.py::test_auth_service_bootstrap_admin_auto_active_and_issues_jwt
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/auth/auth_service.py apps/api/tests/test_auth_login.py
git commit -m "feat(api): record last_login_at on successful login"
```

---

### Task 3: Add `created_at` and `last_login_at` to admin user API responses

**Files:**
- Modify: `apps/api/src/noa_api/core/auth/authorization.py`
- Modify: `apps/api/src/noa_api/api/routes/admin.py`
- Test: `apps/api/tests/test_rbac.py`

**Step 1: Write a failing RBAC/service test for the new fields**

In `apps/api/tests/test_rbac.py`:

- Extend `_RepoUser` to include:

```py
from datetime import datetime

created_at: datetime
last_login_at: datetime | None
```

- Update any `_RepoUser(...)` instantiations in this file to provide `created_at=...` (and `last_login_at=...` where relevant).
- Add a new test that asserts the service returns these fields:

```py
from datetime import UTC, datetime

async def test_authorization_service_list_users_includes_created_and_last_login() -> None:
    repo = _InMemoryAuthorizationRepository()
    user_id = uuid4()
    now = datetime.now(UTC)
    repo.users[user_id] = _RepoUser(
        id=user_id,
        email="member@example.com",
        display_name="Member",
        is_active=True,
        created_at=now,
        last_login_at=None,
    )
    repo.user_roles[user_id] = {"member"}
    service = AuthorizationService(repository=repo)

    users = await service.list_users()
    assert users[0].created_at == now
    assert users[0].last_login_at is None
```

**Step 2: Run the test to confirm failure**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_rbac.py::test_authorization_service_list_users_includes_created_and_last_login
```

Expected: FAIL because `AuthorizationUser` does not yet carry these fields.

**Step 3: Implement fields on AuthorizationUser + populate them**

In `apps/api/src/noa_api/core/auth/authorization.py`:

- Import datetime:

```py
from datetime import datetime
```

- Extend `AuthorizationUser` to include (with defaults so existing callsites keep working):

```py
created_at: datetime | None = None
last_login_at: datetime | None = None
```

- Update `AuthorizationService.list_users(...)` to set `created_at=user.created_at` and `last_login_at=user.last_login_at`.
- Update `AuthorizationService.set_user_active(...)` and `AuthorizationService.set_user_tools(...)` to also set these fields from the `User` record.

**Step 4: Extend admin route models and mapping**

In `apps/api/src/noa_api/api/routes/admin.py`:

- Import datetime:

```py
from datetime import datetime
```

- Add fields to `AdminUserResponse`:

```py
created_at: datetime
last_login_at: datetime | None
```

- Update `_to_user_response(...)` to fill them:

```py
created_at=user.created_at,
last_login_at=user.last_login_at,
```

**Step 5: Re-run the targeted RBAC test**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_rbac.py::test_authorization_service_list_users_includes_created_and_last_login
```

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/core/auth/authorization.py apps/api/src/noa_api/api/routes/admin.py apps/api/tests/test_rbac.py
git commit -m "feat(api): include created_at and last_login_at in admin users"
```

---

### Task 4: Render `Created` + `Last login` columns and improved status labels in the Admin Users table

**Files:**
- Modify: `apps/web/components/admin/users-admin-page.tsx`
- Test: `apps/web/components/admin/users-admin-page.test.tsx`

**Step 1: Write a failing UI test for the new columns + pending/disabled status**

In `apps/web/components/admin/users-admin-page.test.tsx`:

- Update the test payload users to include `created_at` and `last_login_at`.
- Add a new test that verifies:
  - The table header includes `Created` and `Last login`.
  - An inactive user with `last_login_at: null` shows `Pending approval`.
  - An inactive user with a non-null `last_login_at` shows `Disabled`.

**Step 2: Run the failing test**

Run:

```bash
cd apps/web && npm test -- components/admin/users-admin-page.test.tsx
```

Expected: FAIL until the UI renders the new columns/labels.

**Step 3: Implement deterministic timestamp formatting**

In `apps/web/components/admin/users-admin-page.tsx`, add a small helper (local to the file) to avoid locale/timezone flake:

```ts
function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}
```

**Step 4: Extend the AdminUser type and render the new columns**

- Extend `AdminUser`:

```ts
created_at?: string;
last_login_at?: string | null;
```

- Update `<thead>` to add two columns after Email:
  - `Created`
  - `Last login`

- Update the empty-state row `colSpan` to match the new column count.

- Update each row to render:

```ts
const createdLabel = formatTimestamp(user.created_at);
const lastLoginLabel = user.last_login_at ? formatTimestamp(user.last_login_at) : "Never";
```

- Replace the Status cell label with:

```ts
const isActive = user.is_active !== false;
const hasLoggedIn = Boolean(user.last_login_at);
const statusLabel = isActive ? "Active" : hasLoggedIn ? "Disabled" : "Pending approval";
```

**Step 5: Re-run the UI tests**

Run:

```bash
cd apps/web && npm test -- components/admin/users-admin-page.test.tsx
```

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/web/components/admin/users-admin-page.tsx apps/web/components/admin/users-admin-page.test.tsx
git commit -m "feat(web): show created/last login and clearer user status"
```

---

### Task 5: Remove admin tool bypass (tools are disabled until allowlisted)

**Files:**
- Modify: `apps/api/src/noa_api/core/auth/authorization.py`
- Test: `apps/api/tests/test_rbac.py`

**Step 1: Update the RBAC test to reflect the new policy**

In `apps/api/tests/test_rbac.py`, replace:

- `test_authorization_service_admin_bypasses_tool_checks`

With two tests:

1) Admin denied with no permissions:

```py
async def test_authorization_service_admin_requires_explicit_tool_permissions() -> None:
    repo = _InMemoryAuthorizationRepository()
    service = AuthorizationService(repository=repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is False
```

2) Admin allowed when role permissions include tool:

```py
async def test_authorization_service_admin_allows_when_role_grants_tool() -> None:
    repo = _InMemoryAuthorizationRepository()
    repo.role_tools["admin"] = {"get_current_time"}
    service = AuthorizationService(repository=repo)
    user = AuthorizationUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Admin",
        is_active=True,
        roles=["admin"],
        tools=[],
    )
    assert await service.authorize_tool_access(user, "get_current_time") is True
```

**Step 2: Run the targeted tests to confirm failure**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_rbac.py::test_authorization_service_admin_requires_explicit_tool_permissions
```

Expected: FAIL until the bypass is removed.

**Step 3: Implement the policy change**

In `apps/api/src/noa_api/core/auth/authorization.py`, update `AuthorizationService.authorize_tool_access(...)`:

- Remove the admin early return:

```py
if "admin" in user.roles:
    return True
```

- Keep the existing role tool permission check for all users.

**Step 4: Re-run RBAC tests**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_rbac.py
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/auth/authorization.py apps/api/tests/test_rbac.py
git commit -m "feat(api): require allowlisted tools for admins"
```

---

### Task 6: Explicit tool permission denial feedback (SimondayCE Team)

**Files:**
- Modify: `apps/api/src/noa_api/core/agent/runner.py`
- Test: `apps/api/tests/test_agent_runner.py`

**Step 1: Write a failing AgentRunner test**

In `apps/api/tests/test_agent_runner.py`, add:

```py
async def test_agent_runner_emits_clear_message_when_tool_not_allowed() -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(self, *, messages, tools, on_text_delta=None):
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(
                    text="I'll check the server time.",
                    tool_calls=[LLMToolCall(name="get_current_time", arguments={})],
                )
            return LLMTurnResponse(text="", tool_calls=[])

    runner = AgentRunner(
        llm_client=_TwoTurnLLM(),
        action_tool_run_service=ActionToolRunService(repository=repo),
    )

    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "What time is it?"}]}],
        available_tool_names=set(),
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    texts = [m.parts[0].get("text") for m in result.messages if isinstance(m.parts[0], dict)]
    assert any("SimondayCE Team" in (t or "") for t in texts)
```

**Step 2: Run the targeted test to confirm failure**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_agent_runner_emits_clear_message_when_tool_not_allowed
```

Expected: FAIL until the message is updated.

**Step 3: Implement the new denial message**

In `apps/api/src/noa_api/core/agent/runner.py`, update the unavailable-tool branch:

From:

```py
"text": f"Tool '{tool_call.name}' is not available for this user.",
```

To (exact message to keep stable for tests):

```py
"text": (
    f"You don't have permission to use tool '{tool_call.name}'. "
    "Please ask SimondayCE Team to enable tool access for your account."
),
```

**Step 4: Re-run the test**

Run:

```bash
cd apps/api && uv run pytest -q tests/test_agent_runner.py::test_agent_runner_emits_clear_message_when_tool_not_allowed
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/agent/runner.py apps/api/tests/test_agent_runner.py
git commit -m "feat(api): clarify tool permission denied messaging"
```

---

### Task 7: Full verification

**Files:** none

**Step 1: Run API tests**

Run:

```bash
cd apps/api && uv run pytest -q
```

Expected: PASS.

**Step 2: Run web tests**

Run:

```bash
cd apps/web && npm test
```

Expected: PASS.

**Step 3: Run web build (optional but recommended)**

Run:

```bash
cd apps/web && npm run build
```

Expected: build succeeds.
