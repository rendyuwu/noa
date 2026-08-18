# AGENTS.md (NOA)

Terse like caveman. Technical substance exact. Only fluff die.
Drop: articles, filler (just/really/basically), pleasantries, hedging.
Fragments OK. Short synonyms. Code unchanged.
Pattern: [thing] [action] [reason]. [next step].
ACTIVE EVERY RESPONSE. ⊥ revert after many turns. ⊥ filler drift.
Code/commits/PRs: normal prose. Off switch: "stop caveman" / "normal mode".

`SPEC.md` = working artifact. Read before edit. `FORMAT.md` = how to parse it.
Cite by § — `§V.15`, `§T.32`, `C8`. Zero ambiguity.
`DECISIONS.md` = why. ⊥ re-litigate decided items without new evidence.
`docs/AS-BUILT.md` = cold archive. **⊥ read whole, ever** — 170 KB, single lines reach 15 KB.
Slice by header only; protocol = "AS-BUILT read protocol" below, ∧ it BINDS.

## Repo

Monorepo, 3 deployables, 1 shared `core/`, 1 Alembic (C12):

- `apps/api` — FastAPI + FastMCP. Two router sets: `/mcp` + `/admin`.
- `apps/admin-web` — Next.js + BIGSU admin panel. ⊥ frameable.
- `apps/web-embed` — Next.js approval card iframe + large-result tables. **⊥ BIGSU, ⊥ Tailwind** —
  hand-written CSS; eslint refuses `@gio/*` ∧ any `apps/admin-web` import (T40). Dev port 3001.
- `core/` — config, auth, remote_exec, secrets, integrations. Shared by all.

`apps/admin-web` ∧ `apps/web-embed` = independent packages. Own lockfile, own CI, own deploy.
⊥ shared source, ⊥ shared deps, ⊥ aliases, ⊥ symlinks between them.

## Build / lint / test

Postgres (dev), from root:

```bash
docker compose up -d postgres
```

API — need Python `>=3.11,<3.13` + `uv`:

```bash
cp .env.example .env
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn noa_api.main:app --reload --port 8000
```

Python lint/format/test from root:

```bash
uv run ruff check .
uv run ruff format .
uv run pytest -q
uv run pytest -q apps/api/tests/test_health.py::test_health_ok   # single test
uv run pytest -q -k rbac
```

Web apps, each in own dir: `pnpm install`, `pnpm dev`, `pnpm build`, `pnpm lint`,
`pnpm typecheck`, `pnpm test`.

## Hard boundaries — break these ∧ design dies

- **Reason** = one field, operator-typed in approval card. CHANGE tool schemas ⊥ carry reason param
  of any name. ⊥ `reason`, ⊥ `proposed_reason`. LLM ⊥ author, ⊥ relay, ⊥ see. Reason born at
  DECISION time, ⊥ call time — **approve ∧ deny both require a non-blank one**, 409
  `change_reason_required` either way, ∧ the DB CHECK `ck_action_requests_decided_reason` holds it
  vs any writer. EXPIRED carries ⊥ reason ∵ nobody gave one. (C8, V15, V43, T37)
- **Approve/deny** ⊥ travel through LLM. Only path = cookie POST from NOA-origin document +
  server-minted CSRF token — HMAC, bound to the session **∧** the request id, key derived from
  `AUTH_JWT_SECRET`, `core/approvals/csrf.py`. "May this run?" read from `action_requests.status`
  in DB — ⊥ from LLM claim, ⊥ from tool arg. One writer of a terminal status
  (`core/approvals/decisions.py`), ∧ it is ⊥ reachable from the MCP path: T33's repository writes
  PENDING ∧ nothing else. (C18, V22, V23, V28, V39, T37)
- **One workflow, one tool.** Preflight, checks, execution → internal functions. Only final
  workflow exposed. Evidence born in-process, ⊥ cross tool boundary. (C9, V17)
- **Never-implement list** (C22) = management policy, ⊥ technical. ⊥ port, ⊥ expose, ⊥ re-add.
  Re-add = owner decision, ⊥ agent call.
- **T59 = CLEARED 2026-08-08 (was a blocking gate).** Iframe render path VERIFIED live vs pinned
  LibreChat `45cc53c4` — frame on NOA origin, cookie rides in, in-frame POST authenticated
  (R29). Embed work (T32, T41–T46, T56) unblocked; active branch = iframe UI resource + link-out
  text beside it (V24, V25). Approve/deny in-frame ! be JS `fetch` — no `allow-forms` (V80).
  Re-verify on every LibreChat bump: `spikes/librechat-embed-render-gate/` (C21).
- **An ESCAPE HATCH out of the frame ships the plain ADDRESS beside the link** — ⊥ a link alone.
  `target="_blank"` needs `allow-popups`, absent at 1 of the 2 render sites ⇒ the click does
  NOTHING, silently; ∧ where popups ARE granted the opened tab INHERITS the sandbox ⇒ `allow-forms`
  ⊥ in it either, so a `<form>` login in that tab is inert too. "Top-level" ⊥ mean "unsandboxed"
  (MEASURED, T43). Assert on the page COUNT ∨ a hit counter, ⊥ on an exception, ∧ at BOTH pinned
  sandbox strings — the permissive one is the negative control. (V94, V25, R32, R13)
- **Python `<3.13`** (C1) ∧ **`fastmcp==3.4.5`** (C23) ∧ **exact-pinned `next`/`react`** (C2).
  Bump = deliberate + re-verify, ⊥ drive-by.
- Ambiguous identifier → structured candidates result. ⊥ guess. (C10, V18)
- Raw exceptions ⊥ reach LLM. Sanitize: `RuntimeError` → `tool_execution_failed`,
  `TimeoutError` → `timeout`. (V19)
- Zero firewall backends available → error `no_firewall_backend`. ⊥ success-with-empty-gather.
  Silent no-op on approved CHANGE = ⊥ acceptable. Guard sits at the MECHANISM: ∀ firewall op goes
  through `firewall_gate.run_on_usable_backends` — ⊥ a per-tool check, ⊥ a hand-rolled
  `asyncio.gather` (AST-guarded). Binaries present ∧ `sudo -n` denied → `ssh_sudo_required`,
  ⊥ `no_firewall_backend`. (V57, V55, T68)
- **Partial answer ⊥ a whole one.** A source that ⊥ answer gets NAMED beside the verdict; zero
  answers → `unknown`, ⊥ the benign value. Silence ≠ evidence of absence. (V86)
- A READ that CAPS rows ships its own bound — total count + truncation flag, ordered
  reproducibly before the cut. (V85)
- **A background pass that WRITES carries a LIMIT ∧ reports what the LIMIT hid.** Cap IN the
  statement, ⊥ a slice after loading; total from the SAME statement as the page; `truncated` +
  `remaining` beside `count`, ∵ "how many did this pass resolve" reads as "how many were
  there". Batch ÷ interval = the drain rate ∧ it gets STATED. ⊥ `FOR UPDATE ... SKIP LOCKED`
  on rows a live worker also writes — a repair loop ⊥ outrank the worker. (V92, V85)

## Code style

- Python: type hints ∀ new code. `ruff` clean. `.py` ≤ 900 lines.
- TypeScript: typed, ⊥ `any`. `.ts` ≤ 300 lines, `.tsx` ≤ 450 lines.
- Reusable functions over duplication. Shared code → `core/`. (V66)
- Tests for new functionality. ≥1 check per touched invariant where practical. (V67)
- Test compare ⊥ eat clock-stamped bytes (`Expires`, `Date`, `iat`) — drop them from equality,
  assert by property, ∧ keep a case proving the compare still separates. (V87, B4)
- Test of a CONCURRENCY control ! prove the 2 parties OVERLAPPED. `asyncio.gather` of 2 callers
  ⊥ a race — it passes with the lock deleted. Hold the window open, assert ORDER (⊥ the win
  count), ∧ ship a negative control showing the forbidden order is reachable. (V89, B5)
- Test SETUP gate ⊥ be the thing under test. Readiness wait pointed at the subject turns the
  subject's failure into a TIMEOUT, ∧ a timeout names nothing. Gate sits one layer BELOW — socket
  under route, process under socket. (V90)
- Conventional commits. ⊥ secrets in git — `.env*` ignored except `.env.example`. (V68)
- Env vars for lists = JSON arrays: `AUTH_BOOTSTRAP_ADMIN_EMAILS=["a@b.com"]`.
- Browser ⊥ call FastAPI direct. Same-origin proxy route per web app.
- ∀ error response carries `request_id` in body + `x-request-id` header. Shared handler, ⊥ per-route.
  (V73)

## Reference repo

`noa-old` = patterns source, ⊥ fork base. Port branch = **`MCP`**, ⊥ `staging`, ⊥ working tree.
`core/secrets/yopass.py`, `core/secrets/password.py`, `docs/integrations/yopass.md` exist ONLY on
`MCP`/`main`. Read `git show MCP:<path>`.
Copy mature integration layers (WHM/Proxmox/PMG, `remote_exec`, `secrets`) — ⊥ rewrite. Banner
stripping ∧ `sudo -n` hardened there. **Host-key pinning ∧ TOFU refresh were ⊥** — upstream
`known_hosts=None` = asyncssh's off switch, port inherited a dead pin, fixed here (B2, V82).
Upstream provenance ⊥ evidence a control works: ported security control ! land with a test vs the
real mechanism before any §V ∨ doc calls it hardened. (C13, V69, V82, V84)
**Same rule caught the REFRESH half (T54)** — upstream WHM validate captured ∧ OVERWROTE the pin
EVERY run ⇒ pin worth nothing, any admin pressing Validate silently re-trusts whatever answers.
NOA pins ONCE: ⊥ pin → capture, store ONLY if the probe after it passes; stored pin ≠ presented
→ `ssh_host_key_mismatch`, ⊥ re-pin. Rotation = 2 deliberate acts (clear, then validate). Test =
real `asyncssh` on loopback + `auth_attempts == []`
(`apps/api/tests/test_server_host_key_validation.py`). Upstream's PMG had the tighter shape ∧ its
WHM ⊥ — port the tighter one, ⊥ the first one read.

## Docs map

- `README.md` — overview, setup, architecture summary.
- `SPEC.md` — goal, constraints, interfaces, invariants, tasks, bugs. Canonical.
- `DECISIONS.md` — decision notes + measurements. Why, ⊥ what.
- `FORMAT.md` — SPEC.md format + caveman encoding rules.
- `docs/AS-BUILT.md` — cold archive, ⊥ loaded per session: as-built deviation detail for done §T
  rows, §B full narratives, consumed/superseded §R. Sub-clause addresses (`T38(g)`) resolve HERE.
  A done task's as-built goes here, ⊥ back into `SPEC.md`. Read protocol below = HARD RULE.
- `docs/integrations/` — per-system reference (WHM, Proxmox, PMG, yopass). Update in same change as
  the feature.

## AS-BUILT read protocol — HARD RULE

`docs/AS-BUILT.md` = 170 KB / ~45k tokens in 263 lines. ONE body line reaches 15 KB (~4k tokens).
263 lines LIES about the size — line count is no license to open it. A whole-file read burns a
third of a fresh context ∧ buys near-zero ∵ ~95% of it is unrelated to any one task.

- **⊥ `Read` it without BOTH `offset` ∧ `limit`.** ⊥ `cat`, ⊥ `head`, ⊥ full-file `Read`,
  ⊥ "skim it to get oriented", ⊥ "load it once, it will be cached". No free read exists.
- **⊥ grep it in content mode.** `Grep` with `output_mode: "content"` returns the ENTIRE matched
  body line — 4k tokens for a 1-word hit. Use `output_mode: "files_with_matches"` ∨ `"count"`, ∨ a
  WINDOWED shell grep: `grep -n -o -m 5 '.\{0,120\}<term>.\{0,120\}' docs/AS-BUILT.md`.
- Layout is fixed ∧ machine-sliceable: `## T<nn>` heading, blank, ONE body line, blank. 4 lines
  per section.
- Retrieval = 2 steps, always:
  1. `grep -n '^## T38$' docs/AS-BUILT.md` → line N. (Header grep is safe — headers are short.)
  2. `Read` `offset: N`, `limit: 3`. ⊥ widen `limit` "to see neighbours" — neighbours cost 4k each.
- Enter ONLY when a cite resolves HERE (`T38(g)`, `T32(b)`) ∧ `SPEC.md`'s own line ⊥ answer the
  question in hand. SPEC.md carries intent + cites ∧ that usually suffices. Default = stay out.
- Need ≥3 sections, ∨ the target section is unknown → delegate to a subagent: it greps, reads its
  slices, ∧ returns THE ANSWER. Archive prose ⊥ get pasted back into main context — pasting it is
  the whole-file read wearing a different hat.
- APPEND/EDIT same way: header-grep for the anchor, `Read` that slice (satisfies `Edit`'s read
  gate), then `Edit` on the anchor line. ⊥ `Write` the file — `Write` wants a full prior `Read`,
  which is the banned act.
- Violating this is ⊥ recoverable mid-session: context spent ⊥ come back. When unsure whether a
  slice is enough, take the slice ∧ grep again — 2 cheap reads beat 1 catastrophic one.
