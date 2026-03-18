# WHM suspend/unsuspend UX polish (plan)

Scope: only the `whm_suspend_account` and `whm_unsuspend_account` flow, including how its workflow steps and completion receipts render in the web UI.

## Problems observed

1) Duplicate completion messaging

- After an approval-based run completes, the user sees more than one “success” receipt.
- Root cause (current architecture):
  - The approval executor persists a workflow-family completion assistant message.
  - The assistant agent also runs after an `approve-action` command and generates its own completion narrative.
  - Additionally, UI chrome (approval lifecycle + run summary card) uses “done/completed successfully” language.

2) Run summary shows `1/1 steps`

- `Run summary` renders from the `update_workflow_todo` tool-call payload (`args.todos` / `result.todos`), which in practice often contains a single terminal line.
- The canonical workflow TODOs stored in thread state (`metadata.custom.workflow`) already include the multi-step lifecycle, but are not used for display.

## Desired behavior

- For WHM suspend/unsuspend runs, only one completion narrative is shown in the thread (the agent-generated one).
- `Run summary` reflects the canonical multi-step lifecycle (preflight → reason → approval → execute → postflight), not a single summary line.
- UI chrome stays status-oriented and does not compete with the assistant’s completion text.

## Proposed changes (patch list)

### API (stop backend completion receipt for WHM account lifecycle)

File: `apps/api/src/noa_api/api/assistant/assistant_action_operations.py`

- In `execute_approved_tool_run(...)`, after building `workflow_reply_text`, do NOT persist the workflow-family completion assistant message for WHM account lifecycle.
- Keep denial behavior as-is (deny does not re-run the agent today; the backend message is still needed there).

Concrete change:

- Gate the completion message creation:
  - Before:
    - Always persists `workflow_reply_text` when `tool.workflow_family` is set.
  - After:
    - Skip persisting when `tool.workflow_family == "whm-account-lifecycle"`.

Rationale:

- `approve-action` commands re-run the agent (see `apps/api/src/noa_api/api/assistant/assistant_commands.py:196`), so the agent will produce the final completion narrative.
- Removing the backend-injected completion message eliminates the duplicated “success” block for WHM suspend/unsuspend.

### API (tighten WHM account lifecycle step model)

File: `apps/api/src/noa_api/core/workflows/whm.py`

- In `WHMAccountLifecycleTemplate.build_todos(...)`:
  - Remove the “conclusion” step (currently built via `_conclusion_step_content(...)`).
  - Keep 5 steps that match the mental model:
    1) Preflight check
    2) Reason captured
    3) Approval
    4) Execute
    5) Postflight verification

Notes:

- This reduces repetition (“Conclusion: moved from …”) and makes the canonical workflow align with what we want the user to see in `Run summary`.

### Web (render Run summary from canonical todos)

File: `apps/web/components/assistant/workflow-todo-tool-ui.tsx`

- In `WorkflowTodoToolUI.render(...)`:
  - Continue using tool-call payload todos (`args.todos` / `result.todos`) only to decide whether to render the card at all (today: terminal-only).
  - For the card’s displayed todos (counts + detail sheet content), prefer canonical todos from `extractLatestCanonicalWorkflowTodos(threadMessages)` when available.
  - Guard against mismatched workflows (multiple workflows in one thread): only substitute canonical todos when it looks like the same run. Suggested guard:
    - canonical exists AND canonical.length > payloadTodos.length AND at least one `content` string overlaps.

Expected outcome:

- Even when the `update_workflow_todo` tool payload is a single “Unsuspend …” line, `Run summary` shows the 5 canonical WHM steps.

### Web (make “finished” chrome copy neutral)

File: `apps/web/components/assistant/approval-lifecycle-ui.ts`

- Change the `finished` presentation copy to avoid “completed successfully”.
  - Example:
    - title: keep `Change complete`
    - detail: change to something status-only, e.g. `Execution finished. Review the outcome in the thread.`

### Web (reduce duplicate “Completed / done” emphasis in Run summary card)

File: `apps/web/components/assistant/workflow-todo-tool-ui.tsx`

- In `WorkflowTodoCard(...)`:
  - Make summary text structural (counts) rather than celebratory.
  - Example:
    - Before: `Completed · 5/5 steps · done`
    - After: `5/5 steps` (+ optional `· 0 cancelled`)

## Tests and verification

### Web unit tests

File: `apps/web/components/claude/workflow-todo-tool-ui.test.tsx`

- Update assertion that currently expects `/completed .* 2\/2 steps/i`.
- Add a new test case:
  - Tool payload todos: 1 terminal step.
  - Thread canonical metadata todos: 5 steps.
  - Expect the rendered summary to reflect `5/5` and the detail sheet `todos` to include canonical step strings.

### Manual verification (end-to-end)

1) Start a WHM unsuspend run without reason.
2) Provide reason.
3) Approve.
4) Confirm:
  - Only one assistant completion narrative appears (no extra “Unsuspend completed …” block injected by backend).
  - `Run summary` shows 5 steps.
  - Approval/history UI does not add another “completed successfully” sentence.

## Risks / edge cases

- Threads with multiple workflow runs: canonical metadata is attached to the last message; older receipts could accidentally show newer canonical todos. The overlap guard in `workflow-todo-tool-ui.tsx` is required.
- Other workflow families: the web changes are generic; they should be low-risk and improve correctness (canonical workflow is the documented source of truth per `docs/assistant/workflow-templates.md`).
- Deny path: do not remove backend denial message for WHM; deny does not re-run the agent today.
