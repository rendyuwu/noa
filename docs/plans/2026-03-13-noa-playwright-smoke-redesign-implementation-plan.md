# NOA Playwright Smoke Skill Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign `noa-playwright-smoke` so the live skill is self-contained, the main agent builds the smoke checklist and owns cleanup, the subagent only executes and reports, and local smoke auth uses the backend development LDAP bypass instead of the old auth-helper fallback.

**Architecture:** Keep `./.agents/skills/noa-playwright-smoke/SKILL.md` as the only live instruction source. Remove the `runner.md` dependency, standardize the subagent contract inside `SKILL.md`, reuse the existing backend `AUTH_DEV_BYPASS_LDAP` path for smoke login, and preserve the gallery-based evidence bundle so the subagent can serve `index.html` on `0.0.0.0:9999` while the main agent reports `http://127.0.0.1:9999/index.html` to the user.

**Tech Stack:** Markdown skill docs, FastAPI auth settings/tests, Python utility scripts, Playwright MCP, `uv`, npm.

---

### Task 1: Rewrite the live skill contract and remove the runner dependency

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/SKILL.md`
- Delete: `.agents/skills/noa-playwright-smoke/runner.md`

**Step 1: Replace the dispatcher-only opening in `SKILL.md`**

Rewrite the file so it clearly defines two modes inside one document:

- main agent mode: inspect changes, build the smoke checklist, dispatch the subagent, report the evidence URL, perform cleanup
- subagent mode: prepare env, start services, run smoke, collect artifacts, generate/serve HTML evidence, report PASS/FAIL without changing code

The new `SKILL.md` should explicitly say that loading the skill is sufficient and that no secondary runner file is required.

**Step 2: Inline the required smoke checklist format**

Add a required checklist schema that includes:

```md
- id
- title
- why
- steps
- expected
- must_not_happen
```

Also add guidance that commit ranges are optional context, but the main agent must still convert them into a smoke checklist instead of delegating planning to the subagent.

**Step 3: Inline the subagent prompt contract**

Add a concrete subagent prompt template inside `SKILL.md` that requires the subagent to:

- reuse Postgres if it is already running
- materialize env files from `master`-tracked env templates
- start API/web servers
- run the smoke checklist through Playwright
- capture step log, screenshots, logs, and video
- generate HTML evidence and serve it on `0.0.0.0:9999`
- return a `127.0.0.1` URL for the main agent to share with the user
- avoid any code edits or commits

**Step 4: Remove `runner.md` and stale references**

Delete `.agents/skills/noa-playwright-smoke/runner.md`, then run:

```bash
rg -n "runner\.md|auth-helper|process is not defined" .agents/skills/noa-playwright-smoke docs/plans
```

Expected: the live skill directory no longer depends on `runner.md`, and any remaining references are only historical documents under `docs/plans/`.

**Step 5: Commit**

```bash
git add .agents/skills/noa-playwright-smoke/SKILL.md .agents/skills/noa-playwright-smoke/runner.md
git commit -m "chore: redesign NOA smoke skill workflow"
```

---

### Task 2: Document the smoke auth contract in the API env template

**Files:**
- Modify: `apps/api/.env.example`

**Step 1: Add smoke-mode comments and examples**

Document the preferred local smoke combination directly in `apps/api/.env.example`:

- `AUTH_DEV_BYPASS_LDAP=true`
- a dedicated smoke email added to `AUTH_BOOTSTRAP_ADMIN_EMAILS`
- a note that this is for local smoke verification only and should stay disabled outside development/test

Keep the existing LDAP documentation intact; this is an additive clarification, not a replacement.

**Step 2: Verify the env example remains safe**

Check that the file still avoids real secrets and still reads as a template rather than a runnable production config.

**Step 3: Commit**

```bash
git add apps/api/.env.example
git commit -m "docs: describe NOA smoke auth env contract"
```

---

### Task 3: Add regression coverage for the smoke-login contract

**Files:**
- Modify: `apps/api/tests/test_auth_login.py`
- Modify if needed: `apps/api/src/noa_api/core/auth/auth_service.py`
- Modify if needed: `apps/api/src/noa_api/core/config.py`

**Step 1: Add a regression test for smoke login behavior**

Extend `apps/api/tests/test_auth_login.py` with a test that verifies this exact contract:

- development settings
- `auth_dev_bypass_ldap=True`
- smoke email included in `bootstrap_admin_emails`
- authenticating the smoke user succeeds without reaching a real LDAP server
- the created user is active on first login
- the returned token and role set are valid for the smoke session

Use the existing in-memory repo helpers and `LDAPService`/fake JWT service patterns already present in the file.

**Step 2: Run the focused auth tests**

Run:

```bash
uv run pytest -q apps/api/tests/test_auth_login.py -k "bypass or bootstrap or smoke"
```

Expected: the new test passes along with the existing dev-bypass coverage.

**Step 3: Only implement code changes if the new test exposes a gap**

If the test fails, make the smallest change necessary in:

- `apps/api/src/noa_api/core/auth/auth_service.py`, or
- `apps/api/src/noa_api/core/config.py`

Do not add a second auth-helper path. Keep the fix aligned with the existing development LDAP bypass model.

**Step 4: Re-run the focused auth tests**

Run the same `pytest` command again and confirm PASS.

**Step 5: Commit**

```bash
git add apps/api/tests/test_auth_login.py apps/api/src/noa_api/core/auth/auth_service.py apps/api/src/noa_api/core/config.py
git commit -m "test: pin smoke login contract for dev LDAP bypass"
```

---

### Task 4: Make the evidence gallery surface the full smoke bundle

**Files:**
- Modify: `.agents/skills/noa-playwright-smoke/scripts/build_gallery.py`

**Step 1: Extend gallery links for step-by-step evidence**

Update `build_gallery.py` so the generated HTML links any available step log file, preferring:

- `steps.md`
- `steps.txt`
- optional summary files such as `report.md` if they exist

Keep the current screenshot and video behavior intact.

**Step 2: Rename or tighten labels only if it improves clarity**

If helpful, change the visible title from a screenshot-only framing to an evidence/report framing, for example `NOA Smoke Evidence`.

**Step 3: Validate the script manually with a temp artifacts directory**

Create a disposable temp folder containing:

- `shots/00-login.png` (can be a placeholder image)
- `video/demo.webm` (can be an empty placeholder if the script only lists links)
- `steps.md`
- `console-errors.txt`

Then run:

```bash
python3 .agents/skills/noa-playwright-smoke/scripts/build_gallery.py <temp-artifacts-dir>
```

Expected: `index.html` is created and contains links to the step log alongside screenshots/video/logs.

**Step 4: Commit**

```bash
git add .agents/skills/noa-playwright-smoke/scripts/build_gallery.py
git commit -m "feat: expand NOA smoke evidence gallery"
```

---

### Task 5: Verify the live skill instructions before using them for implementation work

**Files:**
- Modify if needed: `.agents/skills/noa-playwright-smoke/SKILL.md`

**Step 1: Read the final `SKILL.md` top-to-bottom**

Confirm that it clearly answers all of these without consulting any other file:

- when the skill should trigger
- how the main agent derives the smoke checklist
- what the subagent receives
- how smoke auth bypasses LDAP
- how artifacts are generated and served
- what the main agent must clean up
- what URL the main agent should tell the user

**Step 2: Run a final reference check**

Run:

```bash
rg -n "runner\.md|Change Checklist|0\.0\.0\.0:9999|127\.0\.0\.1:9999|AUTH_DEV_BYPASS_LDAP" .agents/skills/noa-playwright-smoke apps/api/.env.example
```

Expected:

- `runner.md` is gone from the live workflow
- the checklist contract is present
- both server bind and user-facing URL are documented
- the smoke auth contract is discoverable

**Step 3: Commit**

```bash
git add .agents/skills/noa-playwright-smoke/SKILL.md apps/api/.env.example .agents/skills/noa-playwright-smoke/scripts/build_gallery.py apps/api/tests/test_auth_login.py
git commit -m "chore: finalize NOA smoke skill redesign"
```
