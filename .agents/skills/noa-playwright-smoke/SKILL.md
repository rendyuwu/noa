---
name: noa-playwright-smoke
description: Use when implementing or changing NOA (apps/web or apps/api), before claiming a change is done/fixed, or when asked to quickly verify behavior end-to-end in a real browser.
---

# NOA Playwright Smoke

Loading this skill is sufficient. `SKILL.md` is the only live instruction source for this workflow. Do not depend on any secondary instruction file.

Goal: keep the implementation agent focused on code while a fresh verification subagent prepares the local smoke environment, executes the browser smoke flow, captures evidence, and reports PASS or FAIL. The main agent owns the smoke checklist, evidence handoff, the user confirmation gate, and cleanup.

## When To Use This Skill

Use this skill when:

- implementing or changing NOA in `apps/web` or `apps/api`
- fixing a bug that should be checked end-to-end in a real browser
- asked to quickly verify live NOA behavior with Playwright
- about to claim a NOA change is done or fixed

## Operating Modes

### Main Agent Mode

The main agent must:

1. Inspect the requested change, touched files, and any user notes.
2. Build the Change Checklist itself. Commit ranges are optional context only; they help explain what changed, but they do not replace checklist authoring.
3. Dispatch a fresh subagent with the checklist, local context, and the contract in this file.
4. Read the subagent report, share the local evidence URL `http://127.0.0.1:9999/index.html` exactly, and summarize PASS or FAIL.
5. Tell the user the local HTML evidence will remain available for review and wait for explicit confirmation that they are done reviewing it.
6. Clean up any smoke processes only after that user confirmation, including API, web, and gallery server processes started for the run.

Do not delegate planning to the subagent. The subagent executes the checklist; the main agent decides what to verify.

Recommended handoff message:

`Smoke finished. Review the local HTML report at http://127.0.0.1:9999/index.html and tell me when you are done. I will wait for your confirmation before cleanup.`

### Subagent Mode

The subagent must:

1. Prepare the local environment without editing code.
2. Reuse Postgres if it is already running; otherwise start the local dev Postgres needed for NOA.
3. Materialize env files only from tracked templates on `master`/the checked-out repository state (`apps/api/.env.example`, `apps/web/.env.example`) and never invent ad hoc env files.
4. Start the API and web servers.
5. Run the Change Checklist in Playwright.
6. Capture step logs, screenshots, logs, and video evidence.
7. Generate HTML evidence with `.agents/skills/noa-playwright-smoke/scripts/build_gallery.py` and serve it on `0.0.0.0:9999` with a simple static file server.
8. Return a concise PASS or FAIL report plus the user-facing URL `http://127.0.0.1:9999/index.html`.
9. Avoid any code edits, rebases, commits, or cleanup-only refactors.

The subagent reports what it started so the main agent can clean it up.

## Required Change Checklist Format

Every smoke run needs a checklist. If the user does not provide one, the main agent derives it from the implementation and sends it to the subagent.

Each checklist item must include:

- `id`
- `title`
- `why`
- `steps`
- `expected`
- `must_not_happen`

Use this structure:

```md
Change Checklist:
- id: login
  title: User can sign in and reach the assistant
  why: Confirms the smoke account can enter the product before feature checks begin
  steps:
    - Open `/login`
    - Sign in with the local smoke account
    - Wait for the assistant view to load
  expected:
    - Login form renders
    - Authentication succeeds
    - The assistant thread viewport is visible after login
  must_not_happen:
    - Auth errors
    - Blank screen
    - Redirect loop
```

Checklist items should be concrete and visual. `expected` should name visible UI states, labels, layouts, or success conditions. `must_not_happen` should call out regressions to watch for.

## Local Smoke Auth Contract

Use the backend development LDAP bypass for local smoke authentication. Do not add a separate token-helper fallback.

Required local contract:

- `apps/api/.env` comes from `apps/api/.env.example` if missing
- `AUTH_DEV_BYPASS_LDAP=true`
- the dedicated smoke email is included in `AUTH_BOOTSTRAP_ADMIN_EMAILS`
- the smoke user signs in through the normal `/login` UI against the local API

This flow proves the development bypass path works as intended for smoke verification while still exercising the real login screen and session behavior.

## Secrets And Artifact Rules

- Never paste credential values into chat, tool calls, or subagent prompts.
- Do not screenshot a filled login form.
- Keep artifacts local.
- Do not commit artifacts, env files, or captured tokens.

## Subagent Prompt Template

Use this template when dispatching the verification subagent:

```text
You are a NOA Playwright smoke verification subagent. Follow `.agents/skills/noa-playwright-smoke/SKILL.md` in subagent mode only.

Do not edit code, create commits, or change the checklist.

Required context:
- Repo root: <repo-root>
- Change Checklist:
<paste checklist here>
- Optional implementation context:
<paths, routes, or commit range notes if helpful>

Required execution contract:
- Reuse Postgres if it is already running; otherwise start the local dev Postgres needed by NOA.
- Materialize env files only from tracked env templates if they are missing.
- Configure local smoke auth with `AUTH_DEV_BYPASS_LDAP=true` and ensure the smoke email is included in `AUTH_BOOTSTRAP_ADMIN_EMAILS`.
- Start the API and web servers.
- Run the smoke checklist through Playwright.
- Capture a step log (`steps.md` or `steps.txt`), screenshots, browser console errors, network requests, server logs, and video.
- Generate HTML evidence with `.agents/skills/noa-playwright-smoke/scripts/build_gallery.py`.
- Serve the artifacts directory on `0.0.0.0:9999` (for example `python3 -m http.server 9999 --bind 0.0.0.0`) and include the user-facing URL `http://127.0.0.1:9999/index.html` in the report.
- Record the PID and command details for every long-lived process you start so the main agent can clean them up.
- Report PASS/FAIL per checklist item with evidence filenames.
- Do not perform cleanup; the main agent owns cleanup after you return.

Return only:
- PASS or FAIL
- Baseline smoke results (`/health`, `/login`, login success)
- Checklist results with evidence references
- `ARTIFACTS=...`
- `http://127.0.0.1:9999/index.html`
- Started process details that must be cleaned up
- First actionable fix if FAIL
```

## Expected Subagent Evidence Bundle

The subagent should leave an artifacts directory containing at least:

- `steps.md` or `steps.txt`
- `shots/`
- `video/`
- `console-errors.txt`
- `network-requests.txt`
- `api.log`
- `web.log`
- `index.html`

If useful, the subagent may also include `report.md` or other small summary files. The gallery should expose the full smoke bundle, not only screenshots.

## Main Agent Cleanup Contract

After the subagent returns and the user confirms they are done reviewing the evidence, the main agent must stop anything the run started, including:

- API server
- web dev server
- evidence gallery server bound to `0.0.0.0:9999`
- any extra smoke-only helper process started for the run

Do not stop smoke processes before the user confirms they are done reviewing the evidence URL.

Do not tell the user a run is complete until cleanup has either succeeded or been explicitly reported as incomplete.

## Iterate

If any checklist item fails:

1. Fix the implementation in the main agent.
2. Rebuild the checklist if the behavior changed.
3. Dispatch a fresh subagent with the updated checklist.
4. Repeat until the smoke run passes or a blocker is identified.
