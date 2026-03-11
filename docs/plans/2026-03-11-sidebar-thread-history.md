# Sidebar Refresh + Thread History Reliability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refresh the assistant sidebar into a cleaner Claude-inspired NOA rail and make persisted threads keep their titles/messages after refresh or remount.

**Architecture:** Keep the existing assistant-ui primitives and Claude workspace layout, but restyle the sidebar shell around icon-led navigation, a real recents section, and a lighter account footer. Fix the thread-history bug in two layers: persist generated titles on the backend, then add an explicit thread-state hydration path so the assistant runtime reloads saved messages for existing threads after remount.

**Tech Stack:** Next.js (`apps/web`), FastAPI (`apps/api`), `@assistant-ui/react`, assistant transport, Tailwind v4, Vitest + Testing Library, pytest + httpx.

---

### Task 1: Add a failing web test for the refreshed sidebar shell

**Files:**
- Modify: `apps/web/components/claude/claude-thread-list.test.tsx`
- Verify: `apps/web/components/claude/claude-thread-list.tsx`

**Step 1: Write the failing test**

Add a new test to `apps/web/components/claude/claude-thread-list.test.tsx` that proves the sidebar exposes the new structure:

```tsx
it("renders a Claude-inspired NOA sidebar with a recents label and cleaner account actions", () => {
  render(<ClaudeThreadList />);

  expect(screen.getByText("Recents")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New chat" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Admin" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Logout" })).toBeInTheDocument();
});
```

Keep the existing footer/nav tests if they still make sense; replace expectations that are no longer true.

**Step 2: Run the test to verify it fails**

Run from `apps/web`:

```bash
npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: FAIL because the component does not yet render the new `Recents` structure.

**Step 3: Commit the failing test**

```bash
git add apps/web/components/claude/claude-thread-list.test.tsx
git commit -m "test(web): cover refreshed sidebar shell"
```

### Task 2: Implement the sidebar refresh

**Files:**
- Modify: `apps/web/components/claude/claude-thread-list.tsx`
- Test: `apps/web/components/claude/claude-thread-list.test.tsx`

**Step 1: Make the test pass with the minimum structural change**

Update `apps/web/components/claude/claude-thread-list.tsx`:

- Move sidebar nav/history/meta typography to `font-ui`.
- Add a `Recents` section label above the thread list.
- Replace the current delete-first row treatment with a quieter trailing overflow action pattern.
- Keep `ThreadListPrimitive.Root`, `ThreadListPrimitive.New`, `ThreadListPrimitive.Items`, and auth footer wiring intact.

The updated shape should roughly follow this structure:

```tsx
<ThreadListPrimitive.Root className="flex h-full flex-col bg-[#F5F5F0] dark:bg-[#2b2a27]">
  <div className="px-3 pt-3 font-ui">
    {/* branding + new chat + nav rows */}
  </div>

  <div className="mt-4 min-h-0 flex-1 px-3 font-ui">
    <p className="px-3 pb-2 text-xs font-medium uppercase tracking-[0.12em] text-[#8a8985]">
      Recents
    </p>
    <ThreadListPrimitive.Items components={{ ThreadListItem }} />
  </div>

  <div className="border-t px-3 py-3 font-ui">
    {/* quieter account strip with Admin + Logout */}
  </div>
</ThreadListPrimitive.Root>
```

For icons, prefer the existing repo pattern (`@radix-ui/react-icons`) unless `lucide-react` gives a clearly better fit.

**Step 2: Run the test to verify it passes**

Run from `apps/web`:

```bash
npm test -- components/claude/claude-thread-list.test.tsx
```

Expected: PASS.

**Step 3: Commit**

```bash
git add apps/web/components/claude/claude-thread-list.tsx apps/web/components/claude/claude-thread-list.test.tsx
git commit -m "fix(web): refresh Claude-style sidebar shell"
```

### Task 3: Add a failing API test for durable generated titles

**Files:**
- Modify: `apps/api/tests/test_threads.py`
- Verify: `apps/api/src/noa_api/api/routes/threads.py`

**Step 1: Write the failing test**

Add a test to `apps/api/tests/test_threads.py` proving title generation is persisted to later list calls:

```python
async def test_threads_title_endpoint_persists_generated_title_for_later_list_fetch() -> None:
    app = FastAPI()
    app.include_router(threads_router)

    service = _FakeThreadService()
    owner_id = uuid4()
    app.dependency_overrides[get_thread_service] = lambda: service
    app.dependency_overrides[get_current_auth_user] = lambda: AuthorizationUser(
        user_id=owner_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )

    thread, _ = await service.create_thread(owner_user_id=owner_id, title=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_response = await client.post(
            f"/threads/{thread.id}/title",
            json={"messages": [{"role": "user", "content": "Need a deployment rollback checklist"}]},
        )
        list_response = await client.get("/threads")

    assert title_response.status_code == 200
    assert list_response.status_code == 200
    assert list_response.json()["threads"][0]["title"] == "Need a deployment rollback checklist"
```

You will need to update `_FakeThreadService` if the test needs persistence behavior that it does not currently model.

**Step 2: Run the test to verify it fails**

Run from `apps/api`:

```bash
uv run pytest -q tests/test_threads.py -k persists_generated_title
```

Expected: FAIL because the generated title is returned but not saved onto the thread.

**Step 3: Commit the failing test**

```bash
git add apps/api/tests/test_threads.py
git commit -m "test(api): cover persisted generated thread titles"
```

### Task 4: Persist generated titles in the threads API

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/threads.py`
- Modify: `apps/api/tests/test_threads.py`
- Test: `apps/api/tests/test_threads.py`

**Step 1: Implement the minimal persistence change**

Update `apps/api/src/noa_api/api/routes/threads.py` so `POST /threads/{id}/title` saves the generated title before returning it.

One minimal way:

```python
thread = await thread_service.update_thread_title(
    owner_user_id=current_user.user_id,
    thread_id=id,
    title=title,
)
if thread is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
return GenerateTitleResponse(title=title)
```

If `_FakeThreadService` in `apps/api/tests/test_threads.py` does not already support this path, extend it in the smallest possible way.

**Step 2: Run the targeted test to verify it passes**

Run from `apps/api`:

```bash
uv run pytest -q tests/test_threads.py -k persists_generated_title
```

Expected: PASS.

**Step 3: Run the full thread route test file**

```bash
uv run pytest -q tests/test_threads.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/api/routes/threads.py apps/api/tests/test_threads.py
git commit -m "fix(api): persist generated thread titles"
```

### Task 5: Add a failing API test for persisted thread-state hydration

**Files:**
- Modify: `apps/api/tests/test_assistant.py`
- Verify: `apps/api/src/noa_api/api/routes/assistant.py`

**Step 1: Write the failing test**

Add a route test to `apps/api/tests/test_assistant.py` for a read-only hydration endpoint:

```python
async def test_assistant_thread_state_route_returns_canonical_saved_messages() -> None:
    owner_id = uuid4()
    thread_id = uuid4()
    service = _FakeAssistantService(
        owner_user_id=owner_id,
        thread_id=thread_id,
        messages=[
            {
                "id": str(uuid4()),
                "role": "user",
                "parts": [{"type": "text", "text": "Hello from storage"}],
            }
        ],
    )
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/assistant/threads/{thread_id}/state")

    assert response.status_code == 200
    assert response.json()["messages"][0]["parts"][0]["text"] == "Hello from storage"
    assert response.json()["isRunning"] is False
```

**Step 2: Run the test to verify it fails**

Run from `apps/api`:

```bash
uv run pytest -q tests/test_assistant.py -k thread_state_route
```

Expected: FAIL because the route does not exist yet.

**Step 3: Commit the failing test**

```bash
git add apps/api/tests/test_assistant.py
git commit -m "test(api): cover persisted assistant thread state"
```

### Task 6: Implement the assistant thread-state hydration endpoint

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Test: `apps/api/tests/test_assistant.py`

**Step 1: Implement the route with existing service logic**

Add a read-only route to `apps/api/src/noa_api/api/routes/assistant.py` near the existing assistant transport route:

```python
@router.get("/assistant/threads/{thread_id}/state")
async def get_assistant_thread_state(
    thread_id: UUID,
    current_user: AuthorizationUser = Depends(_require_active_user),
    assistant_service: AssistantService = Depends(get_assistant_service),
) -> dict[str, object]:
    return await assistant_service.load_state(
        owner_user_id=current_user.user_id,
        thread_id=thread_id,
    )
```

If FastAPI needs an explicit response model, add a small `BaseModel` for the returned state.

**Step 2: Run the targeted test to verify it passes**

Run from `apps/api`:

```bash
uv run pytest -q tests/test_assistant.py -k thread_state_route
```

Expected: PASS.

**Step 3: Run the full assistant route test file**

```bash
uv run pytest -q tests/test_assistant.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant.py
git commit -m "feat(api): expose persisted assistant thread state"
```

### Task 7: Add a failing web test for runtime hydration after remount

**Files:**
- Modify: `apps/web/components/lib/runtime-provider.test.tsx`
- Verify: `apps/web/components/lib/runtime-provider.tsx`

**Step 1: Write the failing test**

Extend `apps/web/components/lib/runtime-provider.test.tsx` so it proves persisted thread state is fetched and loaded into the runtime for an existing thread.

Mock pieces you need:

- `fetchWithAuth` to resolve with saved thread state,
- `useAssistantApi().thread().unstable_loadExternalState` to capture the injected state.

Example test shape:

```tsx
it("hydrates persisted thread state for an existing remote thread", async () => {
  const loadExternalState = vi.fn();
  fetchWithAuth.mockResolvedValue(
    new Response(JSON.stringify({
      messages: [{ id: "m1", role: "user", parts: [{ type: "text", text: "Saved" }] }],
      isRunning: false,
    }))
  );

  mockAssistantApi.thread = () => ({ unstable_loadExternalState: loadExternalState });

  render(
    <NoaAssistantRuntimeProvider>
      <div />
    </NoaAssistantRuntimeProvider>,
  );

  await waitFor(() => {
    expect(fetchWithAuth).toHaveBeenCalledWith("/assistant/threads/thread-1/state");
    expect(loadExternalState).toHaveBeenCalledWith(
      expect.objectContaining({ isRunning: false }),
    );
  });
});
```

**Step 2: Run the test to verify it fails**

Run from `apps/web`:

```bash
npm test -- components/lib/runtime-provider.test.tsx
```

Expected: FAIL because the provider does not yet fetch/load persisted thread state.

**Step 3: Commit the failing test**

```bash
git add apps/web/components/lib/runtime-provider.test.tsx
git commit -m "test(web): cover persisted thread hydration"
```

### Task 8: Implement runtime hydration for persisted threads

**Files:**
- Modify: `apps/web/components/lib/runtime-provider.tsx`
- Possibly modify: `apps/web/components/lib/fetch-helper.ts`
- Test: `apps/web/components/lib/runtime-provider.test.tsx`

**Step 1: Implement the minimum hydration behavior**

Update `apps/web/components/lib/runtime-provider.tsx` so existing persisted threads fetch canonical state and inject it into the runtime.

One practical shape:

```tsx
function useHydratePersistedThreadState() {
  const api = useAssistantApi();
  const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId);

  useEffect(() => {
    if (!remoteId) return;

    let cancelled = false;

    void (async () => {
      const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
      const state = await jsonOrThrow<AssistantState>(response);
      if (cancelled) return;
      api.thread().unstable_loadExternalState(state);
    })().catch((error) => {
      console.error("Failed to hydrate persisted thread state", error);
    });

    return () => {
      cancelled = true;
    };
  }, [api, remoteId]);
}
```

Then call the hook from inside the runtime-aware provider flow so it runs in the correct assistant context.

Keep YAGNI:

- do not refactor the converter unless the test forces it,
- do not change new-thread initialization behavior.

**Step 2: Run the targeted test to verify it passes**

Run from `apps/web`:

```bash
npm test -- components/lib/runtime-provider.test.tsx
```

Expected: PASS.

**Step 3: Run the related sidebar/runtime tests**

```bash
npm test -- components/claude/claude-thread-list.test.tsx components/lib/runtime-provider.test.tsx components/claude/claude-workspace.test.tsx
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/web/components/lib/runtime-provider.tsx apps/web/components/lib/runtime-provider.test.tsx
git commit -m "fix(web): hydrate persisted assistant threads"
```

### Task 9: Full verification

**Files:**
- Verify: `apps/web/components/claude/claude-thread-list.tsx`
- Verify: `apps/web/components/lib/runtime-provider.tsx`
- Verify: `apps/api/src/noa_api/api/routes/threads.py`
- Verify: `apps/api/src/noa_api/api/routes/assistant.py`

**Step 1: Run focused web tests**

From `apps/web`:

```bash
npm test -- components/claude/claude-thread-list.test.tsx components/lib/runtime-provider.test.tsx components/claude/claude-thread.test.tsx components/claude/claude-workspace.test.tsx
```

Expected: PASS.

**Step 2: Run focused API tests**

From `apps/api`:

```bash
uv run pytest -q tests/test_threads.py tests/test_assistant.py
```

Expected: PASS.

**Step 3: Run the web production build**

From `apps/web`:

```bash
npm run build
```

Expected: PASS.

**Step 4: Manual smoke check**

- Start a thread, let a title generate, refresh, and confirm the sidebar still shows the generated title.
- Click a saved thread after refresh and confirm saved messages reappear.
- Check desktop + mobile sidebar behavior for active row styling, overflow actions, and footer actions.
