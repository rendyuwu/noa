# Claude UI + /api Proxy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the whole app match assistant-ui’s Claude clone look (Option 1), while routing all browser traffic through a Next.js `/api/*` proxy so the browser never calls the backend directly.

**Architecture:** Keep the existing assistant-ui Assistant Transport runtime + remote thread list. Replace our hand-styled UI with a Claude-clone component tree (primitives + Tailwind classes) and add a catch-all Next.js route handler at `/api/[...path]` that forwards to the FastAPI backend (including streaming).

**Tech Stack:** Next.js App Router, React 19, `@assistant-ui/react` primitives/runtime, Tailwind CSS, Next.js Route Handlers proxying to FastAPI.

---

### Task 0: Create an isolated worktree (recommended)

**Files:** none

**Step 1:** Create a worktree
- Run: `git worktree add ../noa-claude-ui -b feat/claude-ui-proxy`
- Expected: new directory `../noa-claude-ui` with a clean checkout

### Task 1: Add Tailwind + UI deps

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`

**Step 1:** Add dependencies (npm)
- Add dev deps: `tailwindcss postcss autoprefixer`
- Add deps for icons/markdown used by Claude-like UI: `@radix-ui/react-icons lucide-react remark-gfm @assistant-ui/react-markdown`

**Step 2:** Install
- Run (in `apps/web`): `npm install`
- Expected: install succeeds, lockfile updated

### Task 2: Configure Tailwind in the Next app

**Files:**
- Create: `apps/web/tailwind.config.ts`
- Create: `apps/web/postcss.config.mjs`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/app/layout.tsx`

**Step 1:** Add Tailwind config
- `apps/web/tailwind.config.ts` should include content globs for `apps/web/app` + `apps/web/components`

**Step 2:** Add PostCSS config
- `apps/web/postcss.config.mjs` with `tailwindcss` + `autoprefixer`

**Step 3:** Replace global CSS baseline
- `apps/web/app/globals.css`:
  - `@tailwind base; @tailwind components; @tailwind utilities;`
  - add Claude tokens (bg `#F5F5F0`, primary `#ae5630`, muted `#6b6a68`, etc.)
  - set global serif typography and warm background

**Step 4:** Ensure full-height layout + base classes
- `apps/web/app/layout.tsx`: set `<html>`/`<body>` to full height and apply base background/text classes

### Task 3: Add `/api/*` backend proxy (streaming-safe)

**Files:**
- Create: `apps/web/app/api/[...path]/route.ts`
- Modify: `apps/web/.env.example` (doc/config)

**Step 1:** Create a catch-all route handler
Implement a proxy that:
- reads backend base URL from server env (recommend `NOA_API_URL`)
- forwards method, headers (including `Authorization`), querystring, and body
- returns `new Response(upstream.body, { status, headers })` to preserve streaming for `/assistant`

Skeleton:

```ts
// apps/web/app/api/[...path]/route.ts
export const dynamic = "force-dynamic";

const API_BASE =
  process.env.NOA_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ?? // temporary fallback for existing envs
  "http://localhost:8000";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function cleanRequestHeaders(headers: Headers) {
  const next = new Headers(headers);
  next.delete("host");
  for (const h of HOP_BY_HOP) next.delete(h);
  return next;
}

function cleanResponseHeaders(headers: Headers) {
  const next = new Headers(headers);
  for (const h of HOP_BY_HOP) next.delete(h);
  return next;
}

async function handler(req: Request, path: string[]) {
  const url = new URL(req.url);
  const target = new URL(`${API_BASE}/${path.join("/")}`);
  target.search = url.search;

  const init: RequestInit = {
    method: req.method,
    headers: cleanRequestHeaders(req.headers),
    body: req.method === "GET" || req.method === "HEAD" ? undefined : req.body,
    // If Node complains about streaming request bodies, switch to:
    // body: await req.arrayBuffer()
    redirect: "manual",
  };

  const upstream = await fetch(target, init);
  return new Response(upstream.body, {
    status: upstream.status,
    headers: cleanResponseHeaders(upstream.headers),
  });
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ path: string[] }> },
) {
  return handler(req, (await ctx.params).path);
}
export async function POST(
  req: Request,
  ctx: { params: Promise<{ path: string[] }> },
) {
  return handler(req, (await ctx.params).path);
}
export async function PUT(
  req: Request,
  ctx: { params: Promise<{ path: string[] }> },
) {
  return handler(req, (await ctx.params).path);
}
export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ path: string[] }> },
) {
  return handler(req, (await ctx.params).path);
}
export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ path: string[] }> },
) {
  return handler(req, (await ctx.params).path);
}
```

**Step 2:** Update `apps/web/.env.example`
- Replace `NEXT_PUBLIC_API_URL=...` with:
  - `NOA_API_URL=http://localhost:8000`
- Doc note: browser calls `/api/...`; only Next server needs `NOA_API_URL`.

### Task 4: Switch the web app to use `/api` (no direct backend calls from browser)

**Files:**
- Modify: `apps/web/components/lib/fetch-helper.ts`
- Modify: `apps/web/app/login/page.tsx` (and any other direct `getApiUrl()` usage)

**Step 1:** Update `getApiUrl()` to return `"/api"`
- So `fetchWithAuth("/threads")` becomes request to `/api/threads`
- So assistant transport `api: `${getApiUrl()}/assistant`` becomes `/api/assistant`

**Step 2:** Verify login uses proxy
- Login POST should go to `/api/auth/login` (not `http://localhost:8000/auth/login`)

### Task 5: Port Claude clone UI for `/assistant` (Edit/Reload disabled)

**Files:**
- Create: `apps/web/components/claude/claude-shell.tsx`
- Create: `apps/web/components/claude/claude-thread.tsx`
- Create: `apps/web/components/claude/claude-thread-list.tsx`
- Create: `apps/web/components/claude/request-approval-tool-ui.tsx`
- Modify: `apps/web/app/(app)/assistant/page.tsx`
- (Optional) Modify: `apps/web/components/lib/thread-shell.tsx` (leave as legacy)

**Step 1:** Implement Claude thread UI using primitives + Claude classes
- Start from assistant-ui’s `claude.tsx` structure (Thread root, viewport, composer)
- Use `MessagePrimitive.Parts` with Claude-like wrappers
- Add action bars:
  - Copy: functional (`ActionBarPrimitive.Copy`)
  - Feedback: functional only if we add a feedback adapter; otherwise render disabled
  - Edit/Reload: render disabled buttons (do NOT call `ActionBarPrimitive.Edit/Reload`)

**Step 2:** Implement thread list sidebar in Claude visual language
- Style `ThreadListPrimitive.*` to look like Claude’s left rail
- Keep Archive/Delete actions; optionally move to a “…” menu later

**Step 3:** Implement `request_approval` tool UI in Claude style
- Keep the existing `approve-action` / `deny-action` commands
- Render a warm bordered card with args preview + Approve/Deny buttons

**Step 4:** Wire `/assistant` page to use the new shell
- Keep `useRequireAuth()` gate
- Remove the old header row; put Admin/Logout into sidebar footer to match Claude feel

### Task 6: Reskin `/login` to Claude styling

**Files:**
- Modify: `apps/web/app/login/page.tsx`

**Steps:**
- Keep logic identical
- Replace `.panel/.button` styling with Claude Tailwind styling (warm background, rounded card, subtle shadow, serif)

### Task 7: Reskin `/admin` to Claude styling

**Files:**
- Modify: `apps/web/app/(admin)/admin/page.tsx`

**Steps:**
- Keep functionality identical
- Apply Claude surfaces/typography/buttons; keep layout readable and “enterprise” clean

### Task 8: Add docs for disabled controls + proxy

**Files:**
- Create: `docs/plans/2026-03-09-claude-ui-design.md`
- Create: `docs/plans/2026-03-09-claude-ui-proxy-implementation-plan.md`
- Modify: `README.md`
- Modify: `docs/STATUS.md` (optional but recommended)

**Doc requirements:**
- Explicitly list “present but disabled”: Edit, Reload, attachments button, model selector button, tools menu button (if included)
- Explain why (Assistant Transport backend doesn’t support edit/re-run yet; attachments not passed to LLM yet)
- Proxy usage:
  - browser → Next `/api/*` → FastAPI
  - env var `NOA_API_URL`
  - how to verify in devtools network panel

### Task 9: Verification

**Commands:**
- Web: `cd apps/web && npm run build`
- (Optional) API regression: `cd apps/api && uv run pytest`

**Manual smoke:**
- Login works via `/api/auth/login`
- `/assistant` loads, thread list works, send message works, streaming still works via `/api/assistant`
- Tool approval card renders + Approve/Deny works
- Browser network panel shows requests only to same-origin `/api/...` (no direct `http://localhost:8000/...`)
