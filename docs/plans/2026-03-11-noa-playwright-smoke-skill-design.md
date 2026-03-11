# NOA Playwright Smoke Skill (Design)

**Goal:** Add a repo-specific Claude skill that automatically runs a fast end-to-end smoke test for NOA (via the Playwright MCP) before reporting results to the user. This is meant to be used after *any* new feature or implementation change that could affect the running app.

## When To Use

Trigger this skill whenever the user:

- asks to “test”, “verify”, “smoke test”, “regression test”, or “check the UI” for NOA
- asks for confidence before reporting/merging/shipping a change in this repo
- mentions Playwright, browser automation, login flow, assistant UI, or a broken route

## Constraints

- **Repo exclusive:** Assumes NOA monorepo layout (`apps/api`, `apps/web`).
- **Playwright MCP only:** Use the Playwright MCP for browser driving (not a standalone Playwright test suite).
- **Non-destructive env:** Create env files only if missing; never overwrite existing `.env` / `.env.local`.
- **Start + stop servers:** Start API + web dev servers, then kill them at the end (even on failure).
- **No secret leakage:** Credentials must never be written to disk or included in the final user report.

## Runtime Assumptions

- Postgres is already running.
- Default local ports:
  - Web: `http://localhost:3000`
  - API: `http://localhost:8000`
- Credentials are provided via shell environment variables:
  - `NOA_TEST_USER`
  - `NOA_TEST_PASSWORD`

If creds are missing, the skill should fail fast with a clear instruction to export them.

## Setup Behavior (Non-Destructive)

- If `apps/web/.env.local` is missing, create it from `apps/web/.env.example`.
- If `apps/api/.env` is missing, create it from `apps/api/.env.example`.

Note: copying `apps/api/.env.example` may not yield a working LDAP config; do not overwrite an existing `apps/api/.env`.

## Smoke Test Definition

### 1) Start services (bash)

- Start API in the background (PID tracked).
- Start web dev server in the background (PID tracked).
- Wait until API health responds (`GET /health`) and web serves `/login`.

### 2) Login + assistant landing (Playwright MCP)

Navigate and assert:

- Visit `http://localhost:3000/login`
- Fill:
  - email: `#login-email`
  - password: `#login-password`
- Click submit button with accessible name "Sign in"
- Assert we reach `/assistant`
- Assert the assistant thread viewport exists: `[data-testid="thread-viewport"]`

Optional follow-on (not required for v1): assert composer UI exists (`aria-label="Message input"` and `aria-label="Send message"`) without sending a message.

## Artifacts To Capture

- Screenshot on failure (and optionally on success).
- Browser console errors.
- Network request list (exclude static assets if possible).

Store artifacts in a temp run directory and include paths in the final report.

## Failure Modes + Guidance

- Missing creds: instruct user to set `NOA_TEST_USER` / `NOA_TEST_PASSWORD`.
- Login 403 “User pending approval”: explain that the user must be activated / bootstrapped (e.g. `AUTH_BOOTSTRAP_ADMIN_EMAILS`) and re-run.
- API can’t start due to missing dependencies: run `uv sync` in `apps/api`.
- Web can’t start due to missing dependencies: run `npm install` in `apps/web`.

## Output Format (Human Report)

- `PASS` or `FAIL` at the top.
- A short checklist:
  - API health
  - Web /login loads
  - Login succeeds
  - /assistant renders viewport
- If failed: include the first actionable error + artifact paths.
- Never include the credential values.
