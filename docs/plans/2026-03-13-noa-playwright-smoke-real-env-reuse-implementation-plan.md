# NOA Playwright Smoke Real Env Reuse Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update `noa-playwright-smoke` so smoke verification can reuse real local env files from the `master` checkout, especially for valid LLM API keys, while allowing smoke-only API env adjustments that stay uncommitted.

**Architecture:** Keep the change inside `.agents/skills/noa-playwright-smoke/SKILL.md`. Strengthen the subagent contract, the local smoke auth contract, the secrets rules, and the subagent prompt so they all describe the same env precedence and non-commit behavior.

**Tech Stack:** Markdown skill docs.

---

### Task 1: Replace template-only env setup with an explicit precedence rule

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Rewrite the env materialization rule in `Subagent Mode`**

Change the current template-only wording so it explicitly says:

1. if the smoke run is in the `master` checkout, reuse the existing local env there
2. if the smoke run is in a worktree, copy only `apps/api/.env`, `apps/web/.env`, and `apps/web/.env.local` from the `master` checkout into the worktree
3. if the local env is unavailable in `master`, fall back to the tracked `.env.example` file

**Step 2: Keep the rule bounded**

Preserve the guardrail that the subagent must not invent ad hoc env files or pull secrets from anywhere else, and add a deterministic rule that the main agent always provides the absolute `master` checkout path in the handoff.

---

### Task 2: Expand the local smoke auth contract for smoke-only API env edits

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Update the `apps/api/.env` source rule**

State that `apps/api/.env` may come from the local `master` checkout when available, with `apps/api/.env.example` as the fallback.

**Step 2: Add the permitted smoke-only edits**

Explicitly allow the subagent to ensure:

- `AUTH_DEV_BYPASS_LDAP=true`
- `API_CORS_ALLOWED_ORIGINS` includes the local web origin for the smoke run
- the dedicated smoke email is present in `AUTH_BOOTSTRAP_ADMIN_EMAILS`

---

### Task 3: Strengthen the non-commit rule for copied and edited env files

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Add a direct env-specific prohibition**

In `Secrets And Artifact Rules`, add wording that copied env files and smoke-only env edits must remain local and must not be committed.

**Step 2: Mirror the same rule in the prompt template**

Update the `Required execution contract` bullets so the dispatched subagent receives the same instruction in plain language, including a ban on printing env contents or secret-bearing command lines into logs, reports, or chat.

---

### Task 4: Verify the updated instruction path is internally consistent

**Files:**
- Verify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Search for env setup wording**

Run:

```bash
rg -n "env|AUTH_BOOTSTRAP_ADMIN_EMAILS|API_CORS_ALLOWED_ORIGINS|commit" .agents/skills/noa-playwright-smoke/SKILL.md
```

Expected: the subagent contract, local smoke auth contract, secrets rules, and prompt template all describe the same env reuse behavior.

**Step 2: Re-run the baseline scenario mentally or with a review subagent**

Confirm that the updated wording now clearly allows:

- direct reuse of local env files in the `master` checkout
- copying local env files from `master` into a worktree
- smoke-only API env edits for CORS and bootstrap admin email setup
- keeping all copied or edited env files out of git commits

---

### Task 5: Review the diff only

**Files:**
- Verify: `.agents/skills/noa-playwright-smoke/SKILL.md`
- Verify: `docs/plans/2026-03-13-noa-playwright-smoke-real-env-reuse-design.md`
- Verify: `docs/plans/2026-03-13-noa-playwright-smoke-real-env-reuse-implementation-plan.md`

**Step 1: Inspect the diff**

Run:

```bash
git diff -- .agents/skills/noa-playwright-smoke/SKILL.md docs/plans/2026-03-13-noa-playwright-smoke-real-env-reuse-design.md docs/plans/2026-03-13-noa-playwright-smoke-real-env-reuse-implementation-plan.md
```

Expected: the change is limited to env reuse guidance, smoke-only API env edits, and explicit non-commit rules.
