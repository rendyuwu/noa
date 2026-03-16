# WHM Tool Hardening Enforcement Implementation Plan

**Status:** Done on `feat/system-prompt-adjustment`. This file is the execution tracker for the WHM production-hardening pass so future sessions can see what landed and what low-priority follow-up, if any, remains without rereading the whole branch history.

**Goal:** Finish the high-value production hardening around WHM tool safety by enforcing strict argument contracts, preflight-backed CHANGE gating, canonical identity matching, and stable result-shape validation.

**Architecture:** Keep enforcement centralized where possible. Tool arg validation belongs in `apps/api/src/noa_api/core/tools/argument_validation.py` plus registry schemas in `apps/api/src/noa_api/core/tools/registry.py`. Preflight-backed CHANGE safety belongs in `apps/api/src/noa_api/core/agent/runner.py` and the approval execution path in `apps/api/src/noa_api/api/assistant/assistant_action_operations.py`. WHM-specific semantic rules live in the WHM tool/integration layer, and result-shape enforcement stays centralized in `ActionToolRunService.complete_tool_run(...)`.

**Tech Stack:** FastAPI, Python 3.11, async SQLAlchemy, pytest, JSON-schema-like internal validators, WHM helper tools

---

## Commits completed in this hardening pass

- `55519ad` `refactor(api): productionize system prompt handling`
- `3ba6884` `fix(api): reject invalid tool calls before execution`
- `f180a62` `fix(api): enforce preflight-backed tool validation`
- `02ddce6` `fix(api): enforce tool result contracts centrally`
- `9933345` `fix(api): harden WHM preflight evidence and identifiers`
- `ffbe492` `fix(api): bind WHM preflight evidence to server identity`

---

## Done

### 1) System prompt and tool contract foundation

**Status:** Done

**What landed:**
- File-based system prompt loading, precedence handling, and prompt fingerprint logging
- Stronger tool descriptions and argument schemas in the central registry
- Centralized runtime argument validation before tool execution/proposal

**Key files:**
- `apps/api/src/noa_api/core/prompts/loader.py`
- `apps/api/src/noa_api/core/agent/runner.py`
- `apps/api/src/noa_api/core/tools/registry.py`
- `apps/api/src/noa_api/core/tools/argument_validation.py`

### 2) Semantic identifier validation

**Status:** Done

**What landed:**
- Strict validation for `server_ref`, WHM usernames, generic CSF targets, and IPv4-only TTL targets
- Stricter CSF hostname parsing so malformed garbage no longer passes as a hostname
- Early invalid-target rejection in CSF preflight reads

**Key files:**
- `apps/api/src/noa_api/core/tools/argument_validation.py`
- `apps/api/src/noa_api/core/tools/registry.py`
- `apps/api/src/noa_api/whm/integrations/csf.py`
- `apps/api/src/noa_api/whm/tools/preflight_tools.py`

### 3) Proposal-time and approval-time preflight enforcement

**Status:** Done

**What landed:**
- CHANGE proposals are blocked unless matching WHM preflight evidence exists
- Approved CHANGE execution revalidates preflight before mutation starts
- Missing/mismatched preflight now persists a failed tool run and tool-result message instead of slipping through or throwing late

**Key files:**
- `apps/api/src/noa_api/core/agent/runner.py`
- `apps/api/src/noa_api/api/assistant/assistant_action_operations.py`
- `apps/api/src/noa_api/api/assistant/assistant_tool_result_operations.py`

### 4) Preflight evidence binding to result content

**Status:** Done

**What landed:**
- Account preflight evidence is matched against `result.account.user`, not just preflight call args
- CSF preflight evidence is matched against `result.target`, not just preflight call args

**Key files:**
- `apps/api/src/noa_api/core/agent/runner.py`
- `apps/api/tests/test_agent_runner.py`
- `apps/api/tests/test_assistant_service.py`

### 5) Canonical server identity binding

**Status:** Done

**What landed:**
- WHM preflight success payloads now include `server_id`
- Proposal-time and approval-time matching prefer canonical `server_id` over raw `server_ref` text
- Matching falls back to raw `server_ref` only when canonical resolution is unavailable

**Key files:**
- `apps/api/src/noa_api/whm/tools/preflight_tools.py`
- `apps/api/src/noa_api/core/agent/runner.py`
- `apps/api/src/noa_api/api/assistant/assistant_action_operations.py`

### 6) Central result-shape enforcement expansion

**Status:** Partially done

**What landed:**
- Tool results are centrally validated in `ActionToolRunService.complete_tool_run(...)`
- WHM read/preflight schemas are now tighter than the original broad `object` contracts
- Preflight success now requires `server_id`; list/search account payloads require minimally shaped account items; server listing requires the safe server object shape

**Key files:**
- `apps/api/src/noa_api/storage/postgres/action_tool_runs.py`
- `apps/api/src/noa_api/core/tools/registry.py`
- `apps/api/tests/test_tool_result_validation.py`

---

## Finalized in this session

### A) Tighten the remaining broad nested result objects

**Status:** Done

**What landed:**
- WHM result objects now default to closed shapes unless a schema explicitly opts into extra fields
- WHM server-resolution errors now use typed `choices` entries with `id`, `name`, and `base_url`
- Account read/preflight payloads now return a normalized safe subset (`user`, `domain`, `email`, `contactemail`, `suspended`) instead of broad upstream pass-through objects
- CSF batch success payloads now require `ok: true` at the top level and keep per-item result variants closed

**Key files:**
- `apps/api/src/noa_api/core/tools/registry.py`
- `apps/api/src/noa_api/whm/tools/read_tools.py`
- `apps/api/src/noa_api/whm/tools/preflight_tools.py`
- `apps/api/src/noa_api/whm/tools/result_shapes.py`
- `apps/api/tests/test_tool_result_validation.py`

### B) Harden WHM admin create/update validation

**Status:** Done

**What landed:**
- Admin create/update request models now reject invalid WHM server names, invalid WHM API usernames, and malformed/non-canonical WHM base URLs at the request boundary
- Valid WHM base URLs are normalized to a stable canonical origin shape to support stricter runtime identity matching
- Request-validation error responses now sanitize exception context so 422 responses remain JSON-serializable when custom validators fail

**Key files:**
- `apps/api/src/noa_api/api/routes/whm_admin.py`
- `apps/api/src/noa_api/api/error_handling.py`
- `apps/api/tests/test_whm_admin_routes.py`

### C) Final audit / cleanup pass

**Status:** Done

**Audit summary:**
- The remaining high-value WHM hardening items from this plan are closed
- Runtime tool/result enforcement is now backed by stricter centralized contracts plus admin-boundary validation for stored WHM server metadata
- No additional high-priority hardening gaps were identified during this pass; any future follow-up should be treated as incremental tightening rather than required production-safety work

- Full API verification completed after the changes landed

---

## Next

No remaining high-priority implementation slice is required for this hardening pass.

If follow-up work is needed later, treat it as optional incremental tightening and start by reviewing the finalized schemas in `apps/api/src/noa_api/core/tools/registry.py` and the normalized account result helpers in `apps/api/src/noa_api/whm/tools/result_shapes.py`.

---

## Verification snapshot

- Latest full API pass in this session after the schema and admin-validation hardening work: `cd apps/api && uv run pytest -q`
- Result: `306 passed`

---

## Resume guidance

If resuming later, start here:

1. Read this file first
2. Check `git log --oneline --decorate -10`
3. Inspect `apps/api/src/noa_api/core/tools/registry.py` and `apps/api/src/noa_api/whm/tools/result_shapes.py` for the finalized WHM result contracts
4. Add only incremental follow-up tightening if a new payload gap is discovered
5. Update this tracker only if new hardening work is intentionally reopened
