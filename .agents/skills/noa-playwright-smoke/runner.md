# NOA Playwright Smoke Runner (Subagent)

This runner is meant to be executed by a fresh subagent dispatched by `.agents/skills/noa-playwright-smoke/SKILL.md`.

Goal:

- Start local NOA servers
- Log in as a real user
- Execute the Change Checklist step-by-step
- Capture many checkpoint screenshots
- Generate a local screenshot gallery (`index.html`)
- LOOK at the screenshots and decide PASS/FAIL against the checklist
- Always cleanup

## Safety / Secrets (REQUIRED)

- Credentials MUST come from env vars `NOA_TEST_USER` and `NOA_TEST_PASSWORD`.
- DO NOT print them.
- DO NOT write them to disk.
- DO NOT interpolate them into tool call text (bash strings, fill_form values, logs).
- Preferred: ONE `playwright_browser_run_code` snippet that reads `process.env` inside the tool runtime.
- If `process.env` is NOT available in the Playwright tool runtime (e.g. `ReferenceError: process is not defined`), use the **auth-helper fallback** to avoid ever pasting credentials.
- DO NOT take screenshots of the login form after filling credentials (password must never appear in artifacts).
- Artifacts can contain cookies/tokens; keep them local and never commit or paste raw artifacts.

## Workflow

### 0) Preconditions

- You have a Change Checklist.
- `NOA_TEST_USER` and `NOA_TEST_PASSWORD` are set in the shell environment.
- Dependencies are installed (`uv sync` in `apps/api`, `npm install` in `apps/web`).
- If the API needs a DB, bring up Postgres + migrations first (see `AGENTS.md`).

### 1) Ensure env files exist (copy only if missing)

Do this via Bash. Never overwrite existing files.

```bash
if [ ! -f apps/web/.env.local ]; then
  cp apps/web/.env.example apps/web/.env.local
fi

if [ ! -f apps/api/.env ]; then
  cp apps/api/.env.example apps/api/.env
fi
```

### 2) Start servers (background) + capture `ARTIFACTS`

```bash
ARTIFACTS=".artifacts/noa-playwright-smoke/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ARTIFACTS" "$ARTIFACTS/shots" "$ARTIFACTS/video"

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

### 4) Playwright smoke + Change Checklist verification

Goal:

- Prove a real user can authenticate
- Then verify the Change Checklist items (step-by-step) with checkpoint screenshots
- Then LOOK at the screenshots and judge correctness against the checklist

Always start by validating `/login` loads and `#login-email` exists.

Then choose ONE auth strategy.

#### Strategy A: UI login (when `process.env` works)

Use a single `playwright_browser_run_code` snippet and read credentials from `process.env` inside the tool runtime.

```js
async (page) => {
  const email = process.env.NOA_TEST_USER;
  const password = process.env.NOA_TEST_PASSWORD;
  const artifactsFallback = "__ARTIFACTS__";
  const artifacts = (() => {
    try {
      const fs = require("fs");
      const p = fs
        .readFileSync(".artifacts/noa-playwright-smoke/LAST_ARTIFACTS", "utf8")
        .trim();
      return p || artifactsFallback;
    } catch {
      return artifactsFallback;
    }
  })();

  if (!email || !password) {
    throw new Error(
      "Missing NOA_TEST_USER/NOA_TEST_PASSWORD env vars (values must not be printed).",
    );
  }
  if (artifacts === artifactsFallback) {
    throw new Error(
      "Missing ARTIFACTS path. Run the bash start recipe first (writes .artifacts/noa-playwright-smoke/LAST_ARTIFACTS) or replace __ARTIFACTS__ manually (path is not a secret).",
    );
  }

  await page.setViewportSize({ width: 1280, height: 720 });

  const shot = async (name) => {
    await page.screenshot({
      path: `${artifacts}/shots/${name}.png`,
      fullPage: true,
    });
  };

  await page.goto("http://localhost:3000/login", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForSelector("#login-email", { timeout: 60_000 });
  await shot("00-login");

  // No screenshots after filling password
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();

  // Post-login landing (adjust if your checklist starts elsewhere)
  await page.waitForURL(/\/(assistant)(\?|$)/, { timeout: 60_000 });
  await page.waitForSelector('[data-testid="thread-viewport"]', {
    timeout: 60_000,
  });
  // Do NOT start video recording until after auth.
  // If you want screen recording, see the "Screen recording" section below.
  await shot("10-after-login");

  // Translate the Change Checklist into actions + checkpoints.
  // Example checkpoint: await shot("20-sidebar-expanded");
};
```

#### Strategy B: Auth-helper fallback (when `process.env` is NOT available)

If you see `ReferenceError: process is not defined`, do NOT paste credentials anywhere.

1) Start a local auth-helper that reads env vars in the shell and exchanges them for a token via the API.

```bash
(
ARTIFACTS="$(cat ".artifacts/noa-playwright-smoke/LAST_ARTIFACTS")"

python3 - <<'PY' >"$ARTIFACTS/auth-helper.log" 2>&1 &
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

API_LOGIN_URL = "http://127.0.0.1:8000/auth/login"
HOST = "127.0.0.1"
PORT = 4555

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/token":
            self._send_json(404, {"detail": "not found"})
            return

        email = os.environ.get("NOA_TEST_USER")
        password = os.environ.get("NOA_TEST_PASSWORD")
        if not email or not password:
            self._send_json(
                500,
                {"detail": "Missing NOA_TEST_USER/NOA_TEST_PASSWORD env vars (values must not be printed)."},
            )
            return

        data = json.dumps({"email": email, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            API_LOGIN_URL,
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                status = resp.getcode()
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read()
        except Exception:
            self._send_json(502, {"detail": "Auth helper failed to reach API"})
            return

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"detail": "API login response was not valid JSON"}
            status = 502

        self._send_json(status, payload if isinstance(payload, dict) else {"detail": "invalid payload"})

HTTPServer((HOST, PORT), Handler).serve_forever()
PY

printf "%s\n" "$!" >"$ARTIFACTS/auth-helper.pid"
)
```

2) In Playwright, fetch the token from the helper (no secrets), inject it into storage, then run the same checklist flows + screenshots.

```js
async (page) => {
  const artifactsFallback = "__ARTIFACTS__";
  const artifacts = (() => {
    try {
      const fs = require("fs");
      const p = fs
        .readFileSync(".artifacts/noa-playwright-smoke/LAST_ARTIFACTS", "utf8")
        .trim();
      return p || artifactsFallback;
    } catch {
      return artifactsFallback;
    }
  })();

  if (artifacts === artifactsFallback) {
    throw new Error(
      "Missing ARTIFACTS path. Run the bash start recipe first (writes .artifacts/noa-playwright-smoke/LAST_ARTIFACTS) or replace __ARTIFACTS__ manually (path is not a secret).",
    );
  }

  const tokenResponse = await page.request.get("http://127.0.0.1:4555/token");
  const payload = await tokenResponse.json();
  const ok = typeof tokenResponse.ok === "function" ? tokenResponse.ok() : tokenResponse.ok;
  const status = typeof tokenResponse.status === "function" ? tokenResponse.status() : tokenResponse.status;
  if (!ok) {
    throw new Error(`Auth helper returned ${status}`);
  }

  const token = payload?.access_token;
  const user = payload?.user;
  if (typeof token !== "string" || !token) {
    throw new Error("Auth helper response missing access_token");
  }

  await page.addInitScript(
    ({ token, user }) => {
      try {
        window.sessionStorage.setItem("noa.jwt", token);
      } catch {}
      try {
        window.localStorage.setItem("noa.user", JSON.stringify(user ?? null));
      } catch {}
    },
    { token, user },
  );

  await page.setViewportSize({ width: 1280, height: 720 });
  const shot = async (name) => {
    await page.screenshot({
      path: `${artifacts}/shots/${name}.png`,
      fullPage: true,
    });
  };

  await page.goto("http://localhost:3000/assistant", { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="thread-viewport"]', { timeout: 60_000 });
  await shot("10-after-login");

  // Translate the Change Checklist into actions + checkpoints.
};
```

### 5) Capture artifacts (REQUIRED)

Always save:

- Screenshots (per checkpoint) under `$ARTIFACTS/shots/`
- Console errors (level `error`) to `$ARTIFACTS/console-errors.txt`
- Network requests list (no static) to `$ARTIFACTS/network-requests.txt`

Use these Playwright MCP tools:

```text
playwright_browser_console_messages({ level: "error", filename: ".../console-errors.txt" })
playwright_browser_network_requests({ includeStatic: false, filename: ".../network-requests.txt" })
```

If a flow fails after leaving the login page, additionally capture a full-page screenshot to `$ARTIFACTS/failure.png` (do not screenshot a filled login form).

### 6) Screen recording (REQUIRED)

Playwright supports screen recording via `recordVideo`, but it is configured at BrowserContext creation time.

To avoid recording the login form (which may contain credential inputs), record ONLY the post-login verification flows:

1) Authenticate first (UI login or auth-helper)
2) Read the post-login auth state from the page (JWT from `sessionStorage`, user JSON from `localStorage`)
3) Create a NEW context with `recordVideo` enabled
4) Inject the auth state into the new context via `addInitScript`
5) Run the Change Checklist flows in the recorded page

Pattern (inside `playwright_browser_run_code`):

```js
// After login succeeded on `page`:
const token = await page.evaluate(() => window.sessionStorage.getItem("noa.jwt"));
const userRaw = await page.evaluate(() => window.localStorage.getItem("noa.user"));
if (!token) {
  throw new Error("Expected noa.jwt in sessionStorage after login");
}

const browser = page.context().browser?.();
if (!browser) {
  throw new Error(
    "Video recording required, but unable to create a new BrowserContext in this runtime",
  );
}

const fs = require("fs");
fs.mkdirSync(`${artifacts}/video`, { recursive: true });

const recordContext = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  recordVideo: {
    dir: `${artifacts}/video`,
    size: { width: 1280, height: 720 },
  },
});

await recordContext.addInitScript(
  ({ token, userRaw }) => {
    try {
      window.sessionStorage.setItem("noa.jwt", token);
    } catch {}
    try {
      if (typeof userRaw === "string" && userRaw) {
        window.localStorage.setItem("noa.user", userRaw);
      }
    } catch {}
  },
  { token, userRaw },
);

const recordedPage = await recordContext.newPage();
const recordedVideo = recordedPage.video();

// Now run all checklist flows on `recordedPage` and keep taking screenshots:
await recordedPage.goto("http://localhost:3000/assistant", { waitUntil: "domcontentloaded" });
await recordedPage.waitForSelector('[data-testid="thread-viewport"]', { timeout: 60_000 });

// ...checkpoint screenshots + actions...

await recordedPage.close();
await recordContext.close();

// Video file is written under $ARTIFACTS/video/ (usually .webm)
if (recordedVideo) {
  await recordedVideo.path();
}
```

If video capture is not possible in this runtime, the run is FAIL.

- checkpoint screenshots
- `$ARTIFACTS/index.html`
- a note in the report explaining why video recording was unavailable

Also FAIL the run if no video file is present under `$ARTIFACTS/video/` after the run completes.

Quick check:

```bash
if ! ls "$ARTIFACTS"/video/*.{webm,mp4} >/dev/null 2>&1; then
  echo "Missing video under $ARTIFACTS/video (expected .webm/.mp4)" >&2
fi
```

### 7) Generate screenshot gallery (REQUIRED)

Create a local HTML gallery so the main agent/user can quickly review many screenshots:

```bash
python3 .agents/skills/noa-playwright-smoke/scripts/build_gallery.py "$ARTIFACTS"
```

Expected output:

- `$ARTIFACTS/index.html` (opens locally, shows thumbnails + full-size links)

If a video was recorded, the gallery also links it from `$ARTIFACTS/video/`.

### 8) Visual review: LOOK at the screenshots (REQUIRED)

For each checklist item:

1) Identify which checkpoint screenshots are the evidence for that item.
2) Open the screenshots (prefer the Read tool so you can actually see the images).
3) Compare against "Expected result" and "Must-not-happen".
4) Mark PASS only if the visuals match.

If you cannot view images in-tool, do NOT claim the UI is correct; instead, report the gallery path and ask the user/main agent to review.

### 9) Always cleanup (REQUIRED)

Cleanup using pidfiles (works in a fresh shell):

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

If you started the auth-helper, terminate it too:

```bash
(
ARTIFACTS="$(cat ".artifacts/noa-playwright-smoke/LAST_ARTIFACTS")"
if [ -f "$ARTIFACTS/auth-helper.pid" ]; then
  HELPER_PID="$(cat "$ARTIFACTS/auth-helper.pid")"
  case "$HELPER_PID" in (''|*[!0-9]*) exit 0;; esac
  kill -TERM "$HELPER_PID" 2>/dev/null || true
  sleep 1
  kill -KILL "$HELPER_PID" 2>/dev/null || true
fi
)
```

### 10) Final report (REQUIRED)

Return a concise report (no big logs, no screenshots embedded):

- PASS or FAIL
- Baseline smoke:
  - API `/health` ok
  - `/login` loaded
  - login succeeded
- Change Checklist results:
  - each item: PASS/FAIL with checkpoint screenshot filenames as evidence
- Artifacts:
  - `ARTIFACTS=...`
  - `$ARTIFACTS/index.html`
  - `$ARTIFACTS/video/` (screen recording, REQUIRED)
  - `$ARTIFACTS/console-errors.txt`
  - `$ARTIFACTS/network-requests.txt`
  - `$ARTIFACTS/api.log`
  - `$ARTIFACTS/web.log`
- Cleanup complete

If FAIL: include the first actionable fix and explicitly say which checklist item(s) failed.

## Common Failure Modes

- Ports already in use (8000/3000): stop conflicting processes and re-run.
- Missing Postgres/migrations: bring up DB and migrate (see `AGENTS.md`).
- Missing env vars: set `NOA_TEST_USER` + `NOA_TEST_PASSWORD` (do not paste values into chat/tool calls).
- `process is not defined` inside `playwright_browser_run_code`: use the auth-helper fallback.
