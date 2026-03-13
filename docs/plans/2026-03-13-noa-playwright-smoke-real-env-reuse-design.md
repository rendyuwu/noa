# NOA Playwright Smoke Real Env Reuse (Design)

**Goal:** Update the `noa-playwright-smoke` skill so smoke verification can reuse real local `.env` files from the main `master` checkout, including valid LLM API keys, while still keeping local smoke-only env edits uncommitted.

## Problem Statement

The current skill only allows env materialization from tracked templates such as `apps/api/.env.example` and `apps/web/.env.example`. That keeps the setup reproducible, but it blocks a real smoke run in a worktree when the required LLM API key only exists in the local `master` checkout's uncommitted `.env` files. As a result, agents are pushed toward mock behavior or incomplete verification.

## Design Goals

- Allow smoke runs to use real local secrets when they already exist in the developer's `master` checkout.
- Support the common worktree flow where smoke runs happen outside the main checkout.
- Keep the source of truth explicit: use the local `master` env when available, otherwise fall back to tracked templates.
- Allow smoke-only additions to `apps/api/.env` for CORS and bootstrap admin access.
- Re-state that copied or edited env files must never be committed.

## Non-Goals

- Do not introduce any new helper scripts or automation for env syncing.
- Do not allow subagents to invent arbitrary env files or fetch secrets from remote systems.
- Do not change the smoke checklist, evidence flow, or cleanup ownership.

## Recommended Approach

Add an explicit env precedence rule to the skill.

When the smoke run happens in the main `master` checkout, the subagent may use the existing local env files there directly. When the smoke run happens in a worktree, the subagent may copy the allowed local env files from the `master` checkout into the worktree so the run can use real secrets such as LLM API keys. The main agent always passes the absolute `master` checkout path in the handoff so the subagent does not guess. If the relevant local env file does not exist in `master`, the subagent falls back to the tracked `.env.example` template.

The allowed local env files for reuse are:

- `apps/api/.env`
- `apps/web/.env`
- `apps/web/.env.local`

For the API env, the subagent may make smoke-only local edits to ensure:

- `AUTH_DEV_BYPASS_LDAP=true`
- `API_CORS_ALLOWED_ORIGINS` includes the local web origin needed for the smoke run
- the dedicated smoke email is present in `AUTH_BOOTSTRAP_ADMIN_EMAILS`

Those copied or edited env files stay local and must never be committed.

## Skill Changes

### Subagent Mode

Replace the template-only env instruction with a precedence rule that explicitly allows:

- using the existing local env in the `master` checkout when the run is already there
- copying the local env from the `master` checkout into a worktree when the run happens in a worktree
- falling back to tracked templates only when the local `master` env file is unavailable

### Local Smoke Auth Contract

Expand the local contract so `apps/api/.env` can come from the `master` checkout or from `apps/api/.env.example` as a fallback, then explicitly allow smoke-only edits for CORS and bootstrap admin email setup.

### Secrets And Artifact Rules

Add a direct rule that copied env files and smoke-only env edits must remain local and uncommitted, and that env contents or secret-bearing command lines must not be printed into logs, reports, or chat.

### Subagent Prompt Template

Update the execution contract so the main agent hands off the same env precedence rule to the verification subagent, including the permitted smoke-only `apps/api/.env` updates.

## Expected Outcome

The smoke skill will support real end-to-end verification with valid local LLM credentials, work cleanly in both the `master` checkout and worktrees, and keep the repo safe by making the non-commit rule explicit for all copied or edited env files.
