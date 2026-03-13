# NOA Playwright Smoke Cleanup Confirmation (Design)

**Goal:** Update the `noa-playwright-smoke` skill so the main agent keeps the local HTML evidence available until the user confirms they are done reviewing it, then performs cleanup.

## Problem Statement

The current skill tells the main agent to share the evidence URL and then clean up immediately after the subagent finishes. That closes the local gallery before the user has a chance to open and inspect `http://127.0.0.1:9999/index.html`, which breaks the intended review flow.

## Design Goals

- Keep the evidence URL alive long enough for the user to review it.
- Make cleanup ownership stay with the main agent.
- Require an explicit user confirmation before cleanup.
- Keep the change small and limited to the live skill instructions.

## Non-Goals

- Do not change the subagent execution flow.
- Do not introduce timeouts, retries, or auto-cleanup behavior.
- Do not add new helper scripts or code outside the skill document.

## Recommended Approach

Use an explicit confirmation gate in the main-agent workflow.

After the subagent returns, the main agent should:

1. Share the exact evidence URL.
2. Summarize PASS or FAIL.
3. Tell the user the local HTML report will stay available.
4. Wait for the user to confirm they are done reviewing it.
5. Only then clean up the API, web, and gallery processes.

This is the smallest change that removes the premature cleanup behavior without adding new lifecycle complexity.

## Skill Changes

### Main Agent Mode

Rewrite the final main-agent steps so they no longer say to clean up immediately after the subagent report. Instead, the instructions should explicitly separate:

- report and handoff to the user
- waiting for user confirmation
- cleanup after confirmation

### Main Agent Cleanup Contract

Add an explicit rule that the main agent must not stop smoke processes until the user confirms they are done reviewing the evidence.

### User-Facing Wording

Add a short example message so the behavior is easy for future agents to follow, such as:

`Smoke finished. Review the local HTML report at http://127.0.0.1:9999/index.html and tell me when you're done. I will wait for your confirmation before cleanup.`

## Expected Outcome

The skill keeps the smoke artifacts accessible during user review, prevents the main agent from tearing down the gallery too early, and preserves the existing ownership split where the main agent still performs cleanup.
