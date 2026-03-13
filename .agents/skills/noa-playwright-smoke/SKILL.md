---
name: noa-playwright-smoke
description: Use when implementing or changing NOA (apps/web or apps/api), before claiming a change is done/fixed, or when asked to quickly verify behavior end-to-end in a real browser.
---

# NOA Playwright Smoke (Dispatcher)

This skill is intentionally dispatcher-only.

Goal: keep the main agent's context focused on implementation. A fresh subagent does the Playwright run, captures screenshots, LOOKS at them, and returns a concise report.

## Required Input: Change Checklist

If the user prompt does not include a checklist, ask for it (ask exactly one question) using this template:

```text
Change Checklist:
1) Area:
   User goal:
   Steps to reach it (in the UI):
   Expected result (what should be visible / text / layout):
   Must-not-happen (regressions to watch for):
   Checkpoints to screenshot (names):
```

Notes:

- "Expected result" must be concrete and visual (labels, buttons, layout, empty states, error states).
- "Checkpoints to screenshot" must include at least one checkpoint per checklist item.

## Secrets Rule (REQUIRED)

- Never paste credential values in chat/tool calls/subagent prompts.
- Credentials MUST come from env vars `NOA_TEST_USER` and `NOA_TEST_PASSWORD` inside the runner.

## Dispatcher Rule (REQUIRED)

Do NOT run Playwright in this agent.

Instead, spawn a fresh subagent and hand it ONLY:

- the Change Checklist
- any additional context needed to navigate (e.g. "new page is under Settings -> Billing")
- constraints (no secrets, capture many screenshots, generate index.html, cleanup)

The subagent must follow the runner instructions in:

`.agents/skills/noa-playwright-smoke/runner.md`

## Subagent Prompt Template

Use this as the subagent task prompt (adapt as needed):

```text
You are a verification subagent.

Task:
- Read and follow `.agents/skills/noa-playwright-smoke/runner.md`.
- Use the Change Checklist below to design step-by-step Playwright actions.
- Capture checkpoint screenshots for every checklist item.
- Generate a screenshot gallery at `$ARTIFACTS/index.html`.
- Capture a screen recording under `$ARTIFACTS/video/` (REQUIRED; do not record the login form). If video capture is not possible, mark the run FAIL and explain why.
- LOOK at the screenshots and decide PASS/FAIL per checklist item.
- Return a concise report to the main agent (no big logs; only file paths + key errors).

Constraints:
- Credentials come ONLY from env vars NOA_TEST_USER/NOA_TEST_PASSWORD; do not print or paste them.
- Do not screenshot a filled login form.
- Always cleanup background servers even on failure.

Change Checklist:
<paste checklist here>
```

## What The Subagent Must Return

- PASS or FAIL
- Baseline smoke: API health, /login loads, login succeeds
- Change Checklist results: PASS/FAIL per item with screenshot filenames as evidence
- Artifacts path (`ARTIFACTS=...`) and the gallery file (`$ARTIFACTS/index.html`)
- Video path (`$ARTIFACTS/video/`). If missing, the run is FAIL.
- First actionable fix if FAIL
- Cleanup complete

## Iterate

If any checklist item FAILs:

1) Main agent fixes implementation
2) Dispatch a fresh subagent again with the same checklist
3) Repeat until PASS
