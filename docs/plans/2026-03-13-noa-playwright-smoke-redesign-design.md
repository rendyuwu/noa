# NOA Playwright Smoke Skill Redesign (Design)

**Goal:** Redesign the `noa-playwright-smoke` skill so that loading the skill gives the main agent a complete, self-contained workflow for smoke verification, subagent dispatch, evidence collection, and cleanup handoff without relying on a separately-read `runner.md` file.

## Problem Statement

The current skill is dispatcher-oriented, but the critical execution contract lives in `./.agents/skills/noa-playwright-smoke/runner.md`. Loading the skill does not automatically load that runner file into the main agent context, which makes the skill easy to misapply and hard to trust. The result is a skill that appears present but still requires hidden repo knowledge to use correctly.

## Design Goals

- Make `./.agents/skills/noa-playwright-smoke/SKILL.md` the single source of truth.
- Keep the main agent responsible for deciding *what* to smoke test.
- Keep the subagent responsible for *executing* the smoke test and gathering evidence.
- Remove the old auth-helper fallback and rely on a stable backend test-only login mode.
- Produce an evidence report that can be served locally and shared back to the user as a URL.
- Move final process cleanup to the main agent so lifecycle ownership is explicit.

## Non-Goals

- Do not turn this into a full Playwright test suite.
- Do not let the smoke subagent edit application code, patch bugs, or create commits.
- Do not require LDAP availability for local smoke runs.

## Architecture Overview

### Single Source of Truth

`./.agents/skills/noa-playwright-smoke/SKILL.md` becomes the authoritative workflow document. It must contain:

- when the skill should trigger
- the main-agent responsibilities
- the required smoke checklist format
- the subagent prompt contract
- the subagent success/failure return format
- evidence generation and serving instructions
- the main-agent cleanup contract

`runner.md` is no longer required. If it remains, it is purely historical or transitional. The preferred end state is to remove it so there is no second instruction source to drift.

### Agent Responsibilities

#### Main Agent

The main agent owns planning and orchestration:

- inspect the current working tree and/or an optional commit range
- summarize the latest changes
- decide which user-visible areas need smoke coverage
- convert those areas into a concrete smoke checklist
- dispatch a fresh verification subagent with the checklist and any navigation notes
- receive PASS/FAIL plus evidence metadata from the subagent
- tell the user the report URL, for example `http://127.0.0.1:9999/index.html`
- stop the backend, frontend, and evidence server after the smoke run completes

#### Subagent

The subagent owns execution only:

- prepare local env files for the smoke run
- reuse Postgres if it is already running
- start backend and frontend for the smoke session
- run Playwright smoke steps for the checklist it was given
- collect step logs, screenshots, console/network/server logs, and a verification video
- generate the HTML evidence report
- serve the artifacts on `0.0.0.0:9999`
- return results and artifact metadata to the main agent

The subagent must not modify application code, retry by changing implementation, or perform cleanup of the long-lived processes it started for the run. It can stop an obviously failed local command during setup, but the final shutdown responsibility stays with the main agent.

## Smoke Checklist Contract

The main agent must pass an explicit checklist to the subagent. Each item should contain:

- `id`
- `title`
- `why`
- `steps`
- `expected`
- `must_not_happen`

This prevents the subagent from inferring the smoke plan from commit history alone. Commit ranges are useful context, but checklist design remains a main-agent responsibility.

## Smoke Authentication Mode

The redesigned flow removes the old auth-helper fallback. Instead, it standardizes a backend-supported smoke login mode that avoids LDAP while preserving the normal UI login flow.

The repo already contains a development-only LDAP bypass (`AUTH_DEV_BYPASS_LDAP`). The redesign should lean on that existing backend behavior instead of inventing a second fallback path. For smoke runs:

- the generated API `.env` must enable the development LDAP bypass
- the smoke test user must be active on first login
- the simplest stable contract is to use a dedicated smoke email that is also included in `AUTH_BOOTSTRAP_ADMIN_EMAILS`

This gives the subagent a deterministic login path:

1. open the normal `/login` page
2. submit the smoke test credentials through the real UI
3. let the backend bypass LDAP in development mode
4. land in the authenticated app without any LDAP dependency or token helper

This keeps the smoke run close to the real product flow while removing the external dependency that makes local verification fragile.

## Environment Preparation

The skill should describe smoke env preparation clearly and truthfully:

- tracked source of truth comes from the `master` branch versions of `apps/api/.env.example` and `apps/web/.env.example`
- the subagent materializes `apps/api/.env` and `apps/web/.env.local` locally for the smoke run
- existing local env files must not be overwritten silently

Because `.env` files are gitignored, "copy from master" must be implemented as "copy the tracked env templates from `master` and apply smoke-specific overrides locally." The skill should state that explicitly so the agent does not assume tracked `.env` files exist.

## Execution Flow

1. Main agent inspects recent changes and builds the smoke checklist.
2. Main agent dispatches a fresh smoke subagent with the checklist and optional commit range context.
3. Subagent materializes local env files from `master`-tracked env templates.
4. Subagent checks whether Postgres is already running and reuses it if available.
5. Subagent starts the API on `8000` and the web app on `3000`.
6. Subagent verifies readiness.
7. Subagent logs in through the normal UI using the smoke user.
8. Subagent executes checklist steps, recording screenshots, logs, and a final verification video.
9. Subagent builds `index.html` for the evidence bundle.
10. Subagent serves the artifacts on `0.0.0.0:9999` and returns the user-facing URL `http://127.0.0.1:9999/index.html` (or another concrete HTML path if applicable).
11. Main agent reports the result and evidence URL to the user.
12. Main agent stops the API, web, and evidence server by checking ports and killing the matching PIDs.

## Artifacts and Reporting

Each smoke run should produce a dedicated artifacts directory containing at least:

- step log (`steps.md` or `steps.txt`)
- checkpoint screenshots
- browser console errors
- network request log
- backend log
- frontend log
- video recording
- generated HTML evidence report

On failure, the subagent returns:

- the failed checklist item(s)
- the failing step(s)
- artifact locations
- a concise diagnosis
- suggested implementation directions for the main agent

On success, the subagent returns:

- PASS/FAIL per checklist item
- the artifacts directory
- the report path
- confirmation that the evidence server is listening on `0.0.0.0:9999`
- a user-facing report URL using `127.0.0.1`, such as `http://127.0.0.1:9999/index.html`

## Cleanup Ownership

Final cleanup belongs to the main agent, not the smoke subagent.

The main agent should:

- inspect the known ports (`3000`, `8000`, `9999`)
- resolve the owning PIDs
- verify the commands look like the expected NOA frontend, backend, and evidence server processes
- terminate them gracefully first, then force-kill only if necessary

This avoids ambiguous ownership and makes the smoke subagent simpler: run, collect, report, hand back control.

## Files Expected To Change

- Modify: `./.agents/skills/noa-playwright-smoke/SKILL.md`
- Delete: `./.agents/skills/noa-playwright-smoke/runner.md`
- Modify: `./.agents/skills/noa-playwright-smoke/scripts/build_gallery.py` (only if needed to surface the full evidence set, such as step logs)
- Possibly update: `apps/api/.env.example` to document the preferred smoke-mode env combination

## Open Implementation Notes

- Prefer reusing the existing `AUTH_DEV_BYPASS_LDAP` backend behavior rather than creating a new auth-helper or token injection fallback.
- If the current gallery already satisfies the evidence requirements, keep the code change minimal and update only the skill instructions.
- Historical plan documents that reference the old runner can remain as historical records; the live skill must no longer depend on them.
