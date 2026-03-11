# NOA Playwright Smoke Skill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a repo-exclusive skill (`noa-playwright-smoke`) that starts the NOA API+web dev servers, runs a Playwright-MCP smoke test (login -> /assistant viewport), then shuts servers down and reports pass/fail before the assistant reports results to the user.

**Architecture:** A `.agents/skills/noa-playwright-smoke/SKILL.md` workflow that (1) ensures env files exist without overwriting, (2) starts servers in the background and waits for readiness, (3) drives the browser via Playwright MCP using stable selectors, (4) captures artifacts, and (5) always cleans up processes.

**Tech Stack:** Next.js (apps/web), FastAPI (apps/api), `uv` (Python), npm (Node), Playwright MCP tools.

---

### Task 1: Create a dedicated worktree for skill work

**Files:** none

**Step 1: Create worktree**

Run:

```bash
git worktree add ../noa-skill-noa-playwright-smoke -b chore/noa-playwright-smoke-skill
```

Expected: new directory `../noa-skill-noa-playwright-smoke` exists and `git status` is clean inside it.

**Step 2: Use the worktree for all following tasks**

Open a new session rooted at `../noa-skill-noa-playwright-smoke`.

---

### Task 2: Scaffold the skill directory

**Files:**
- Create: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Create folder**

Run:

```bash
mkdir -p .agents/skills/noa-playwright-smoke
```

Expected: directory exists.

**Step 2: Create `.agents/skills/noa-playwright-smoke/SKILL.md`**

Create file with the following content:

```markdown
---
name: noa-playwright-smoke
description: Run NOA end-to-end smoke tests using the Playwright MCP before reporting results. Use this skill whenever you implement or change any NOA feature (apps/web or apps/api), touch auth/login, routing, assistant UI, API proxying, or anything user-facing, and you need to verify the app still works. Also use it when the user asks to test/verify/smoke-check/regression-test NOA.
---

# NOA Playwright Smoke

## Purpose

Validate that a fresh NOA stack can start locally and a real user can log in and reach the assistant UI.

This skill is intentionally narrow and repo-specific.

## Preconditions

- Postgres is already running.
- Default dev ports:
  - Web: http://localhost:3000
  - API: http://localhost:8000
- Shell env vars exist:
  - NOA_TEST_USER
  - NOA_TEST_PASSWORD

If the credentials are missing, stop and tell the user to export them.

## Safety rules (credentials)

- Never write credentials to disk.
- Never include credentials in the final user-facing report.
- If you read credentials via a bash command, do not paste the values back into chat.

## Workflow

### 1) Ensure env files exist (non-destructive)

- If `apps/web/.env.local` does not exist, copy from `apps/web/.env.example`.
- If `apps/api/.env` does not exist, copy from `apps/api/.env.example`.
- Do NOT overwrite existing env files.

### 2) Start services (bash)

- Start the API server in the background and record its PID.
- Start the web dev server in the background and record its PID.
- Wait for readiness:
  - API: GET http://localhost:8000/health returns {"status":"ok"}
  - Web: http://localhost:3000/login responds and renders

Notes:
- Prefer no-reload servers for stability.
- If startup fails due to missing deps, run:
  - `uv sync` in `apps/api`
  - `npm install` in `apps/web`

### 3) Run Playwright MCP smoke test

Use stable selectors from the repo:

- Navigate to http://localhost:3000/login
- Fill `#login-email` with the value of NOA_TEST_USER
- Fill `#login-password` with the value of NOA_TEST_PASSWORD
- Click the button named "Sign in"
- Wait until URL contains `/assistant`
- Assert the assistant viewport exists: `[data-testid="thread-viewport"]`

If login fails:
- Capture a screenshot.
- Capture browser console errors.
- Include a short, actionable failure reason in the report.

### 4) Capture artifacts

- Screenshot (at least on failure)
- Console errors
- Network request list (exclude static assets if possible)

Save artifacts to a temp run directory and include the paths in the final report.

### 5) Always cleanup

Stop the web and API processes you started (graceful, then force if needed). Cleanup MUST run even if the test fails.

## Report format

Start with PASS/FAIL, then:

- API health: pass/fail
- Web /login loads: pass/fail
- Login succeeds: pass/fail
- /assistant renders viewport: pass/fail

Then:

- If FAIL: first actionable error + artifact paths
- If PASS: artifact paths (optional)

Do not include credential values.
```

---

### Task 3: Add a minimal “run recipe” snippet for reliable start/stop

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Add concrete bash command templates**

Add a short section that shows a recommended pattern:

- Create a temp run dir
- Start API and web with `nohup ... &` and store PIDs
- Poll endpoints until ready
- Cleanup with `kill $(cat pidfile)` and verify ports are free

Keep it tool-agnostic but compatible with this repo’s tooling (`uv` and `npm`).

**Step 2: Verify the commands are non-destructive**

Confirm the snippet never overwrites `.env` / `.env.local`.

---

### Task 4: Create initial eval prompts for the skill (qualitative)

**Files:**
- Create: `evals/evals.json`

**Step 1: Write 2-3 realistic prompts**

Create `evals/evals.json`:

```json
{
  "skill_name": "noa-playwright-smoke",
  "evals": [
    {
      "id": 0,
      "prompt": "I just changed the NOA login page. Before you report back, run a Playwright smoke test that starts the stack, logs in using NOA_TEST_USER/NOA_TEST_PASSWORD, verifies we reach /assistant, then shuts everything down.",
      "expected_output": "Runs Playwright MCP login smoke test with start/stop and reports pass/fail with artifacts.",
      "files": []
    },
    {
      "id": 1,
      "prompt": "I updated the assistant UI thread layout. Please run the NOA Playwright smoke test before telling me it’s good.",
      "expected_output": "Starts services, logs in, verifies thread viewport exists, collects artifacts, cleans up.",
      "files": []
    }
  ]
}
```

---

### Task 5: Run evals (with-skill vs baseline) and generate a review viewer

**Files:**
- Create: `noa-playwright-smoke-workspace/iteration-1/...`

**Step 1: Create workspace folder next to the skill**

Run:

```bash
mkdir -p noa-playwright-smoke-workspace/iteration-1
```

**Step 2: For each eval prompt, run two independent runs**

- With-skill: run a subagent that has access to `.agents/skills/noa-playwright-smoke`
- Baseline: run the same prompt without the skill

Save each run’s outputs under:

- `noa-playwright-smoke-workspace/iteration-1/eval-<name>/with_skill/outputs/`
- `noa-playwright-smoke-workspace/iteration-1/eval-<name>/without_skill/outputs/`

**Step 3: While runs execute, draft simple assertions**

Add assertions that are objectively checkable (where possible), e.g.:

- “Report contains PASS/FAIL header”
- “Report mentions /assistant and thread-viewport selector”
- “Report does not contain NOA_TEST_PASSWORD value” (manual check)

**Step 4: Generate the review viewer**

Run:

```bash
python /home/ubuntu/.agents/skills/skill-creator/eval-viewer/generate_review.py \
  noa-playwright-smoke-workspace/iteration-1 \
  --skill-name "noa-playwright-smoke" \
  --static noa-playwright-smoke-workspace/iteration-1/review.html
```

Expected: `noa-playwright-smoke-workspace/iteration-1/review.html` exists for human review.

---

### Task 6: Iterate on SKILL.md based on review feedback

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Incorporate feedback**

Improve stability and reduce surprise:

- Make cleanup bulletproof.
- Make readiness checks explicit.
- Make failure guidance actionable.

**Step 2: Re-run evals into iteration-2**

Repeat Task 5 into `noa-playwright-smoke-workspace/iteration-2` and regenerate the viewer with `--previous-workspace`.

---

### Task 7: (Optional) Optimize the description for better triggering

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`

Use the skill-creator description optimization loop *after* the skill behavior is stable.

---

### Task 8: Commit

**Files:**
- Add/Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`
- Add: `evals/evals.json`
- Add: `docs/plans/2026-03-11-noa-playwright-smoke-skill-design.md`
- Add: `docs/plans/2026-03-11-noa-playwright-smoke-skill.md`

Run:

```bash
git add .agents/skills/noa-playwright-smoke/SKILL.md evals/evals.json docs/plans/2026-03-11-noa-playwright-smoke-skill-design.md docs/plans/2026-03-11-noa-playwright-smoke-skill.md
git commit -m "chore: add NOA Playwright smoke testing skill"
```
