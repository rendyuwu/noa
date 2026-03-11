---
name: noa-playwright-smoke
description: Use when implementing or changing NOA (apps/web or apps/api), before claiming a change is done/fixed, or when asked to quickly verify behavior end-to-end in a real browser.
---

# NOA Playwright Smoke

## Overview

This skill runs a fast, end-to-end smoke check against local NOA dev servers:

- API: http://localhost:8000 (health: /health)
- Web: http://localhost:3000 (/login -> /assistant)

If you touched user-facing behavior (routes, auth, UI, tools, persistence), treat this as REQUIRED before saying "done".

## Safety / Secrets (REQUIRED)

- Credentials MUST come from env vars `NOA_TEST_USER` and `NOA_TEST_PASSWORD`.
- DO NOT print them.
- DO NOT write them to disk.
- DO NOT interpolate them into tool call text (bash strings, fill_form values, logs).
- Prefer ONE `playwright_browser_run_code` snippet that reads `process.env` inside the tool runtime.
- DO NOT write/print/interpolate any other credentials (API keys, access tokens, session cookies).
- Artifacts (screenshots, server logs, network capture) can include cookies/tokens and other sensitive data; keep them local and never commit or share raw artifacts.

## Workflow

### Run recipe (copy/paste)

1) Ensure env files exist (copy only if missing):

```bash
if [ ! -f apps/web/.env.local ]; then
  cp apps/web/.env.example apps/web/.env.local
fi

if [ ! -f apps/api/.env ]; then
  cp apps/api/.env.example apps/api/.env
fi
```

2) Start servers (background) + capture `ARTIFACTS`:

```bash
ARTIFACTS=".artifacts/noa-playwright-smoke/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ARTIFACTS"

# WARNING: `.artifacts/noa-playwright-smoke/LAST_ARTIFACTS` is a convenience pointer.
# Concurrent runs can overwrite it. When in doubt, prefer the printed `ARTIFACTS=...`.
printf "%s\n" "$ARTIFACTS" >".artifacts/noa-playwright-smoke/LAST_ARTIFACTS"

( cd apps/api && exec uv run uvicorn noa_api.main:app --port 8000 ) \
  >"$ARTIFACTS/api.log" 2>&1 &
API_PID=$!

( cd apps/web && exec npm run dev -- --port 3000 ) \
  >"$ARTIFACTS/web.log" 2>&1 &
WEB_PID=$!

printf "%s\n" "$API_PID" >"$ARTIFACTS/api.pid"
printf "%s\n" "$WEB_PID" >"$ARTIFACTS/web.pid"

echo "ARTIFACTS=$ARTIFACTS"
```

3) Wait for readiness (step 3), then run Playwright `playwright_browser_run_code` (step 4). Never paste creds; read `process.env` inside the tool runtime.

4) Cleanup using pidfiles (works in a fresh shell):

```bash
(
ARTIFACTS="$(cat ".artifacts/noa-playwright-smoke/LAST_ARTIFACTS")"

if [ -z "${ARTIFACTS:-}" ] || [ ! -d "$ARTIFACTS" ]; then
  echo "Refusing to cleanup: ARTIFACTS dir not found: $ARTIFACTS" >&2
  exit 1
fi

if [ ! -f "$ARTIFACTS/api.pid" ] || [ ! -f "$ARTIFACTS/web.pid" ]; then
  echo "Refusing to cleanup: pidfiles missing under: $ARTIFACTS" >&2
  exit 1
fi

API_PID="$(cat "$ARTIFACTS/api.pid")"
WEB_PID="$(cat "$ARTIFACTS/web.pid")"

case "$API_PID" in (''|*[!0-9]*) echo "Refusing to cleanup: invalid API_PID: $API_PID" >&2; exit 1;; esac
case "$WEB_PID" in (''|*[!0-9]*) echo "Refusing to cleanup: invalid WEB_PID: $WEB_PID" >&2; exit 1;; esac

API_CMD="$(ps -p "$API_PID" -o args= 2>/dev/null || true)"
WEB_CMD="$(ps -p "$WEB_PID" -o args= 2>/dev/null || true)"

if [ -n "$API_CMD" ]; then
  case "$API_CMD" in
    *uvicorn*noa_api.main:app*) : ;;
    *)
      echo "Refusing to cleanup: API pid $API_PID does not look like NOA uvicorn (cmd: $API_CMD)" >&2
      exit 1
      ;;
  esac
fi

if [ -n "$WEB_CMD" ]; then
  case "$WEB_CMD" in
    *npm*run*dev*|*next*dev*|*node*next*dev*) : ;;
    *)
      echo "Refusing to cleanup: WEB pid $WEB_PID does not look like npm/next dev (cmd: $WEB_CMD)" >&2
      exit 1
      ;;
  esac
fi

# Try graceful termination (children first)
pkill -TERM -P "$WEB_PID" 2>/dev/null || true
pkill -TERM -P "$API_PID" 2>/dev/null || true
kill -TERM "$WEB_PID" "$API_PID" 2>/dev/null || true

# Escalate if needed
for i in $(seq 1 10); do
  if ! kill -0 "$WEB_PID" 2>/dev/null && ! kill -0 "$API_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

pkill -KILL -P "$WEB_PID" 2>/dev/null || true
pkill -KILL -P "$API_PID" 2>/dev/null || true
kill -KILL "$WEB_PID" "$API_PID" 2>/dev/null || true
)
```

### 0) Preconditions

- `NOA_TEST_USER` and `NOA_TEST_PASSWORD` are available to the Playwright MCP tool runtime (`process.env`).
- Dependencies are installed (`uv sync` in `apps/api`, `npm install` in `apps/web`).
- If the API needs a DB, bring up Postgres + migrations first (see `AGENTS.md`).

### 1) Ensure env files exist (copy only if missing)

Do this via Bash. Never overwrite existing files.

- Web: `apps/web/.env.local` (copy from `apps/web/.env.example` if missing)
- API: `apps/api/.env` (copy from `apps/api/.env.example` if missing)

Use step 1 from the Run recipe.

### 2) Start API + web dev servers (background, record PIDs)

Start both servers via Bash in the background, capture their PIDs, and write logs into an artifacts directory.

Use step 2 from the Run recipe.

Notes:

- Printing PIDs/paths is OK; never print credentials.
- If either server fails to start, inspect `$ARTIFACTS/api.log` and `$ARTIFACTS/web.log`.

### 3) Wait for readiness

API readiness requirement:

- GET `http://localhost:8000/health` returns JSON `{"status":"ok"}`.

Example Bash poll:

```bash
for i in $(seq 1 60); do
  body="$(curl -fsS http://localhost:8000/health 2>/dev/null || true)"
  compact="$(printf "%s" "$body" | tr -d '\n' | tr -d '\r' | tr -d ' ')"
  if [ "$compact" = "{\"status\":\"ok\"}" ]; then
    echo "API ready"
    break
  fi
  sleep 1
done
```

Web readiness requirement:

- `http://localhost:3000/login` loads and `#login-email` exists.

Prefer to validate web readiness in Playwright by navigating to `/login` and waiting for `#login-email`.

### 4) Playwright MCP smoke test (no secrets in tool call text)

Goal:

- `/login` -> fill `#login-email` + `#login-password`
- click "Sign in"
- wait for `/assistant`
- assert `[data-testid="thread-viewport"]` exists

Use `playwright_browser_run_code` and read credentials from `process.env` inside the tool runtime.

```js
async (page) => {
  const email = process.env.NOA_TEST_USER;
  const password = process.env.NOA_TEST_PASSWORD;

  if (!email || !password) {
    throw new Error(
      "Missing NOA_TEST_USER/NOA_TEST_PASSWORD env vars (values must not be printed).",
    );
  }

  await page.goto("http://localhost:3000/login", {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector("#login-email", { timeout: 60_000 });
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();

  await page.waitForURL(/\/assistant(\?|$)/, { timeout: 60_000 });
  await page.waitForSelector('[data-testid="thread-viewport"]', {
    timeout: 60_000,
  });
};
```

### 5) On failure: capture artifacts (REQUIRED)

If any step fails, capture:

- Screenshot (full page) to `$ARTIFACTS/failure.png`
- Console errors (level `error`) to `$ARTIFACTS/console-errors.txt`
- Network requests list (no static) to `$ARTIFACTS/network-requests.txt`

Use these Playwright MCP tools (filenames should be inside the artifacts directory you created):

```text
playwright_browser_take_screenshot({ fullPage: true, filename: ".../failure.png" })
playwright_browser_console_messages({ level: "error", filename: ".../console-errors.txt" })
playwright_browser_network_requests({ includeStatic: false, filename: ".../network-requests.txt" })
```

Also keep server logs:

- `$ARTIFACTS/api.log`
- `$ARTIFACTS/web.log`

### 6) Always cleanup (REQUIRED)

Do not rely on shell variables persisting. Load `ARTIFACTS` from `.artifacts/noa-playwright-smoke/LAST_ARTIFACTS` (written during startup) and read PIDs from the pid files. Note: concurrent runs can overwrite `LAST_ARTIFACTS`; when in doubt, prefer the printed `ARTIFACTS=...` path.

Use step 4 from the Run recipe.

### 7) Final report (REQUIRED)

Return:

- PASS or FAIL
- Checklist:
  - env files present
  - servers started
  - API `/health` ok
  - `/login` loaded
  - login succeeded
  - `/assistant` loaded
  - `[data-testid="thread-viewport"]` found
  - cleanup complete
- Artifact paths (no secrets)

## Common Failure Modes

- Ports already in use (8000/3000): stop conflicting processes and re-run.
- Missing Postgres/migrations: bring up DB and migrate (see `AGENTS.md`).
- Missing env vars: set `NOA_TEST_USER` + `NOA_TEST_PASSWORD` (do not paste values into chat/tool calls).
