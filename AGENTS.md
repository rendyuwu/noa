# AGENTS.md (NOA)

Terse like caveman. Technical substance exact. Only fluff die.
Drop: articles, filler (just/really/basically), pleasantries, hedging.
Fragments OK. Short synonyms. Code unchanged.
Pattern: [thing] [action] [reason]. [next step].
ACTIVE EVERY RESPONSE. Never revert after many turns. No filler drift.
Code/commits/PRs: normal prose. Off switch: "stop caveman" / "normal mode".

**State RULE, never number.** `.omc/` gitignored (`.gitignore:47`, 0 files tracked), so plan own
numbering (`A9`, `A17`) resolve to NOTHING for next reader, next session. Never broken link —
broken link leave trail; bare `A17` leave reader unable discover what lost. Applies every committed
surface: code comments, commit messages, `docs/`. Write rule inline — spend extra clause. Brief
subagent: translate plan numbering BEFORE it become vocabulary they write into permanent files.
Nothing catch this — bad import fail `tsc`, bad plan-number NOTHING catch. Precision and durability
feel like one virtue from inside session, are not: reference that not resolve is not evidence, only
READS as one.
`DECISIONS.md` = why. Never re-litigate decided items without new evidence. Big and
section-numbered, and those numbers get cited from outside it — `grep -n '^#' DECISIONS.md` for the
map, then Read ONLY that section line range. Never whole-file: one read of all of it costs more
context than every section anyone needed, and the map costs a fraction of one section.

## Repo

Monorepo, 3 deployables, 1 shared `core/`, 1 Alembic:

- `apps/api` — FastAPI + FastMCP. Two router sets: `/mcp` + `/admin`.
- `apps/admin-web` — Next.js + BIGSU admin panel. Never frameable.
- `apps/web-embed` — Next.js approval card iframe + large-result tables. **No BIGSU, no Tailwind** —
  hand-written CSS; eslint refuse `@gio/*` and any `apps/admin-web` import. Dev port 3001.
- `core/` — config, auth, remote_exec, secrets, integrations. Shared by all.

`apps/admin-web` and `apps/web-embed` = independent packages. Own lockfile, own CI, own deploy.
No shared source, no shared deps, no aliases, no symlinks between them.

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

## Remotes and branches

2 remotes, not interchangeable:

- `origin` = GitHub `rendyuwu/noa`, **PUBLIC**. Working/PR surface, not CI surface, so **never add
  or edit `.github/workflows/*` unless asked** — CI task = requirements GitLab pipeline must
  satisfy, not Actions files to author here. `main` tracks it.
- `gitlab` = `git@gitlab.biznetgio.pt:simondayce/noa.git`, PRIVATE, owner-maintained. CI surface,
  branches `master` and `staging`, nothing else. Runner must sit INSIDE Biznet Gio network because
  `bigsu.biznetgio.pt` resolve internal-only, so public runner cannot install `@gio/*` at all.
  Detail: `docs/admin-web.md` section "Registry access and CI".

**Never push `master` or `staging` to `origin`. EVER.** They carry `k8s/*/secret-api.yaml` FILLED —
Fernet key, JWT secret, DB and LDAP bind passwords — and `origin` public. NAME remote every push of
those 2: no bare `git push`, never rely on default. Leak here not recoverable — deleting branch
after not unpublish what already cached or indexed.

`master` and `staging` = `main` + CI/deploy overlay (`.gitlab-ci.yml`, `k8s/{staging,production}/`,
`scripts/ci/`, `artifacts.env`, and 2 tests that read them). Never authoring branch, ever: every job
filters `only: [staging, master]` and owner Argo watch those 2 refs, so 3rd build nothing, sync
nothing.

Main-track work reach them by **`git cherry-pick -x`**, never by merging `main` forward. PR #1
landed via GitHub REBASE strategy, so `main` hold rewritten SHAs whose originals still sit on those
2 branches, and `git merge main` would replay patches already present there.

The 2 branches not byte-identical, BY DESIGN: each one `update_manifest` render only ITS OWN env
dir, so `master` carry `k8s/production/deployment-*.yaml` at `master-<sha>` while `staging` carry
`k8s/staging/*` at `staging-<sha>`. All else must match. Those deploy commits land on branch that
TRIGGERED them and carry `[ci skip]`, so `git fetch gitlab` and fast-forward BOTH before committing
again, or next push is non-fast-forward.

## Hard boundaries — break these and design dies

- **Reason** = one field, operator-typed in approval card. CHANGE tool schemas never carry reason
  param of any name. No `reason`, no `proposed_reason`. LLM never author, never relay, never see.
  Reason born at DECISION time, not call time — **approve and deny both require non-blank one**,
  409 `change_reason_required` either way, and DB CHECK `ck_action_requests_decided_reason` hold it
  vs any writer. EXPIRED carry no reason because nobody gave one.
- **Approve/deny** never travel through LLM. Only path = cookie POST from NOA-origin document +
  server-minted CSRF token — HMAC, bound to session **and** request id, key derived from
  `AUTH_JWT_SECRET`, `core/approvals/csrf.py`. "May this run?" read from `action_requests.status`
  in DB — never from LLM claim, never from tool arg. One writer of terminal status
  (`core/approvals/decisions.py`), and not reachable from MCP path: gate repository writes PENDING
  and nothing else.
- **One workflow, one tool.** Preflight, checks, execution: internal functions. Only final
  workflow exposed. Evidence born in-process, never cross tool boundary.
- **Never-implement list** = management policy, not technical. Never port, never expose, never
  re-add. Re-add = owner decision, not agent call.
- **Iframe render path CLEARED 2026-08-08** (was blocking gate). VERIFIED live vs pinned
  LibreChat `45cc53c4` — frame on NOA origin, cookie rides in, in-frame POST authenticated.
  Embed work unblocked; active branch = iframe UI resource + link-out text beside it.
  Approve/deny in-frame must be JS `fetch` — no `allow-forms`.
  Re-verify every LibreChat bump: `spikes/librechat-embed-render-gate/`.
- **ESCAPE HATCH out of frame ships plain ADDRESS beside link** — never link alone.
  `target="_blank"` need `allow-popups`, absent at 1 of 2 render sites, so click do NOTHING,
  silently; and where popups ARE granted opened tab INHERITS sandbox, so `allow-forms` absent there
  too and `<form>` login in that tab inert. "Top-level" not mean "unsandboxed" (MEASURED). Assert on
  page COUNT or hit counter, never on exception, and at BOTH pinned sandbox strings — permissive one
  is negative control.
- **Python `<3.13`** and **`fastmcp==3.4.5`** and **exact-pinned `next`/`react`**.
  Bump = deliberate + re-verify, never drive-by.
- Ambiguous identifier: structured candidates result. Never guess.
- Raw exceptions never reach LLM. Sanitize: `RuntimeError` to `tool_execution_failed`,
  `TimeoutError` to `timeout`.
- Zero firewall backends available: error `no_firewall_backend`. No success-with-empty-gather.
  Silent no-op on approved CHANGE = not acceptable. Guard sits at MECHANISM: every firewall op
  goes through `firewall_gate.run_on_usable_backends` — never per-tool check, never hand-rolled
  `asyncio.gather` (AST-guarded). Binaries present and `sudo -n` denied: `ssh_sudo_required`,
  never `no_firewall_backend`.
- **Partial answer not whole one.** Source that cannot answer get NAMED beside verdict;
  zero answers: `unknown`, never benign value. Silence not evidence of absence.
- READ that CAPS rows ship own bound — total count + truncation flag, ordered reproducibly before
  cut.
- **Background pass that WRITES carry LIMIT and report what LIMIT hid.** Cap IN statement, never
  slice after loading; total from SAME statement as page; `truncated` + `remaining` beside `count`,
  because "how many did this pass resolve" read as "how many were there". Batch divided by interval
  = drain rate and it get STATED. Never `FOR UPDATE ... SKIP LOCKED` on rows live worker also
  writes — repair loop never outrank worker.

## Code style

- Python: type hints on all new code. `ruff` clean. `.py` at most 900 lines.
- TypeScript: typed, no `any`. `.ts` at most 300 lines, `.tsx` at most 450 lines.
- Reusable functions over duplication. Shared code go to `core/`.
- Tests for new functionality. At least 1 check per touched invariant where practical.
- Test compare never eat clock-stamped bytes (`Expires`, `Date`, `iat`) — drop them from equality,
  assert by property, keep case proving compare still separates.
- Test of CONCURRENCY control must prove 2 parties OVERLAPPED. `asyncio.gather` of 2 callers not
  race — pass with lock deleted. Hold window open, assert ORDER (not win count), ship negative
  control showing forbidden order reachable.
- Test SETUP gate must not be thing under test. Readiness wait pointed at subject turn subject
  failure into TIMEOUT, and timeout name nothing. Gate sits one layer BELOW — socket under route,
  process under socket.
- **Assertion nobody watched FAIL not evidence.** Break guarded thing, SEE red, restore, see green,
  record failure line. Green-against-a-break WORSE than no check — it eat attention missing one
  would draw. Mutate PRODUCTION value, never test body: deleting assertion not redden suite, so that
  measure nothing. Hand-kept SET (dict of branches, list of tools) is claim only where something
  reads it against code — bind it or SAY it unbound. Lane physically cannot stage hazard: say so and
  hold each instrument separately; stated gap beat spec that reads as coverage.
- Conventional commits. No secrets in git — `.env*` ignored except `.env.example`.
- Env vars for lists = JSON arrays: `AUTH_BOOTSTRAP_ADMIN_EMAILS=["a@b.com"]`.
- Browser never call FastAPI direct. Same-origin proxy route per web app.
- Every error response carry `request_id` in body + `x-request-id` header. Shared handler, never
  per-route.

## Reference repo

`noa-old` = patterns source, not fork base. Port branch = **`MCP`**, not `staging`, not working
tree. `core/secrets/yopass.py`, `core/secrets/password.py`, `docs/integrations/yopass.md` exist ONLY
on `MCP`/`main`. Read `git show MCP:<path>`.
Copy mature integration layers (WHM/Proxmox/PMG, `remote_exec`, `secrets`) — never rewrite. Banner
stripping and `sudo -n` hardened there. **Host-key pinning and TOFU refresh were not** — upstream
`known_hosts=None` = asyncssh off switch, port inherited dead pin, fixed here.
Upstream provenance not evidence control works: ported security control must land with test vs real
mechanism before any doc call it hardened.
**Same rule caught REFRESH half** — upstream WHM validate captured and OVERWROTE pin EVERY run, so
pin worth nothing and any admin pressing Validate silently re-trusts whatever answers. NOA pin ONCE:
no pin, capture, store ONLY if probe after it passes; stored pin not equal to presented gives
`ssh_host_key_mismatch`, never re-pin. Rotation = 2 deliberate acts (clear, then validate). Test =
real `asyncssh` on loopback + `auth_attempts == []`
(`apps/api/tests/test_server_host_key_validation.py`). Upstream PMG had tighter shape and its WHM
did not — port tighter one, not first one read.

## Docs map

- `README.md` — overview, setup, architecture summary.
- `ARCHITECTURE.md` — topology, module boundaries, protocol pins.
- `DECISIONS.md` — decision notes + measurements. Why, not what.
- `docs/integrations/` — per-system reference (WHM, Proxmox, PMG, yopass). Update in same change as
  feature.

# Ponytail, lazy senior dev mode

You are lazy senior developer. Lazy mean efficient, not careless. Best code = code never written.

Before writing any code, stop at first rung that holds:

1. Need build at all? (YAGNI)
2. Already exist in this codebase? Reuse helper, util, or pattern already here, not re-write.
3. Standard library already do this? Use it.
4. Native platform feature cover it? Use it.
5. Already-installed dependency solve it? Use it.
6. Can be one line? Make one line.
7. Only then: write minimum code that works.

Ladder run after you understand problem, not instead of it: read task and code it touches, trace
real flow end to end, then climb.

Bug fix = root cause, not symptom: report names symptom. Grep every caller of function you touch and
fix shared function once — one guard there is smaller diff than one per caller, and patching only
path ticket names leave sibling caller still broken.

Rules:

- No abstractions not explicitly requested.
- No new dependency if avoidable.
- No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins, but only once you understand problem. Smallest change in wrong place
  not lazy, it second bug.
- Question complex requests: "Do you actually need X, or does Y cover it?"
- Pick edge-case-correct option when two stdlib approaches same size, lazy mean less code, not
  flimsier algorithm.
- Mark deliberate simplifications that cut real corner with known ceiling (global lock, O(n²) scan,
  naive heuristic) with `ponytail:` comment naming ceiling and upgrade path.

Not lazy about: understanding problem (read fully and trace real flow before picking rung, small
diff you not understand is just laziness dressed up as efficiency), input validation at trust
boundaries, error handling that prevent data loss, security, accessibility, calibration real hardware
needs (platform never spec ideal, clock drifts, sensor reads off), anything explicitly requested.
Lazy code without its check unfinished: non-trivial logic leave ONE runnable check behind, smallest
thing that fails if logic breaks (assert-based demo/self-check or one small test file; no frameworks,
no fixtures). Trivial one-liners need no test.

(Yes, this file also apply to agents working on ponytail repo itself. Especially them.)
