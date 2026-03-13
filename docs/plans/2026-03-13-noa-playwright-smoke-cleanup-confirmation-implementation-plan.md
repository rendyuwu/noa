# NOA Playwright Smoke Cleanup Confirmation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update `noa-playwright-smoke` so the main agent shares the local HTML evidence URL, waits for the user to finish reviewing it, and only cleans up after explicit user confirmation.

**Architecture:** Keep the change inside `.agents/skills/noa-playwright-smoke/SKILL.md`. Strengthen the main-agent workflow by inserting a confirmation gate between evidence handoff and cleanup, then reinforce the same rule in the cleanup contract and the user-facing wording example.

**Tech Stack:** Markdown skill docs.

---

### Task 1: Rewrite the main-agent lifecycle to include a confirmation gate

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Update the final items in `Main Agent Mode`**

Change the main-agent steps so they explicitly require this order:

1. Read the subagent report.
2. Share `http://127.0.0.1:9999/index.html` exactly.
3. Summarize PASS or FAIL.
4. Tell the user the report stays available for review.
5. Wait for the user to confirm they are done.
6. Clean up the smoke processes only after that confirmation.

**Step 2: Keep cleanup ownership unchanged**

Make sure the skill still says the main agent owns cleanup, not the subagent.

---

### Task 2: Strengthen the cleanup contract wording

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Add an explicit prohibition**

In `Main Agent Cleanup Contract`, add clear wording that the main agent must not stop the API server, web server, gallery server, or other smoke-only helpers before the user confirms they are done reviewing the evidence.

**Step 2: Preserve completion semantics**

Keep the rule that the run is not complete until cleanup succeeds or is reported as incomplete, but make that apply after the confirmation gate instead of immediately after reporting the URL.

---

### Task 3: Add user-facing guidance for the handoff message

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Add a short example message**

Add a concise example of the message the main agent should send after a smoke run, telling the user to review the local HTML report and confirm when they are done so cleanup can proceed.

**Step 2: Keep the example short and exact**

Use the exact local URL and avoid adding alternate flows, timeouts, or auto-cleanup language.

---

### Task 4: Verify the instruction path is internally consistent

**Files:**
- Verify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Search for conflicting cleanup wording**

Run:

```bash
rg -n "clean up|cleanup|wait for the user|confirm" .agents/skills/noa-playwright-smoke/SKILL.md
```

Expected: the file consistently describes cleanup as a post-confirmation step.

**Step 2: Read the updated sections together**

Confirm that `Main Agent Mode`, the user-facing example, and `Main Agent Cleanup Contract` all describe the same sequence.

---

### Task 5: Review git diff only

**Files:**
- Verify: `.agents/skills/noa-playwright-smoke/SKILL.md`
- Verify: `docs/plans/2026-03-13-noa-playwright-smoke-cleanup-confirmation-design.md`
- Verify: `docs/plans/2026-03-13-noa-playwright-smoke-cleanup-confirmation-implementation-plan.md`

**Step 1: Inspect the diff**

Run:

```bash
git diff -- .agents/skills/noa-playwright-smoke/SKILL.md docs/plans/2026-03-13-noa-playwright-smoke-cleanup-confirmation-design.md docs/plans/2026-03-13-noa-playwright-smoke-cleanup-confirmation-implementation-plan.md
```

Expected: the change is limited to the confirmation-gate behavior and its supporting docs.
