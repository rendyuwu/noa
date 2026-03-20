# Change Receipts (Issues #26-#29) - Comprehensive Implementation Plan (Draft)

Last updated: 2026-03-19

This document is a brainstorming + implementation plan for:
- #26 RFC: production-grade change receipts
- #27 API: persist completed-phase receipt payload
- #28 Web: render screenshot-friendly completion receipt
- #29 Agent: standardize CHANGE completion narration

Issue links:
- https://github.com/rendyuwu/noa/issues/26
- https://github.com/rendyuwu/noa/issues/27
- https://github.com/rendyuwu/noa/issues/28
- https://github.com/rendyuwu/noa/issues/29

Related references:
- `docs/assistant/workflow-templates.md`
- `docs/assistant/whm-suspend-unsuspend.md`

## 1) Problem Statement

Current ToolRisk.CHANGE workflow completions are inconsistent and often not screenshot-friendly on mobile:
- completion text can be long, redundant, and sometimes includes raw JSON/evidence dumps
- web UI has multiple "completion-ish" surfaces (approval lifecycle, run summary, assistant message) that can compete
- deterministic UI rendering is hard without a stable terminal-phase payload

## 2) Goals / Non-Goals

Goals:
- A single, compact, mobile-screenshot-friendly receipt card for every terminal CHANGE outcome.
- Receipt renders deterministically from structured data (not from LLM-authored markdown).
- Human-only default: no raw JSON in the normal user flow.
- Minimal assistant completion narration (1-2 lines) that does not duplicate the receipt.
- Bind receipts to the correct run in threads with multiple actions.
- Start with workflow-backed CHANGE tools (WHM families), then generalize.

Scope (v1):
- Workflow-backed ToolRisk.CHANGE tools, starting with WHM families called out in #27:
  - `whm-account-lifecycle`
  - `whm-account-contact-email`
  - `whm-csf-batch-change`

Non-goals (for v1):
- A full audit log viewer UI (separate admin surface can come later).
- Streaming live-updating receipts while executing (run summary/todos remain for progress).
- Building new workflow families (we only standardize terminal receipts for existing ones).

## 3) Current System Notes (What Exists Today)

Backend structure already close to the desired contract:
- Workflow template contract and payload helpers exist in `apps/api/src/noa_api/core/workflows/types.py`:
  - `WorkflowReplyTemplate` -> `workflow_reply_template_payload(...)` (camelCase fields: `evidenceSummary`, `nextStep`, `assistantHint`)
  - `WorkflowEvidenceTemplate` -> `workflow_evidence_template_payload(...)` (`evidenceSections[]` with `{ key, title, items[] }`)
- Approval-time context is assembled in `apps/api/src/noa_api/core/workflows/registry.py` via `build_approval_context(...)`:
  - already emits `replyTemplate`, `evidenceSections` (preferred), and a `beforeState` fallback

Terminal-phase gap today:
- Persistent "terminal truth" is mostly `tool_runs.status/result/error` in `apps/api/src/noa_api/storage/postgres/models.py`.
- Workflow todo persistence is todo-only (`apps/api/src/noa_api/storage/postgres/workflow_todos.py`), not evidence-rich.

Web already has evidence-capable UI primitives:
- Approval cards render from `request_approval` args and can display evidence sections (`apps/web/components/assistant/request-approval-tool-ui.tsx`).
- A generic detail sheet can render structured sections (`apps/web/components/assistant/assistant-detail-sheet.tsx`).
- Workflow/run summary UI exists (`apps/web/components/assistant/workflow-todo-tool-ui.tsx`) but backend currently provides only `todos` on `update_workflow_todo`.

## 4) Target UX (What Users See)

For each terminal CHANGE outcome (SUCCESS/PARTIAL/NO-OP/FAILED/DENIED), the thread should show:
1) A short assistant completion message (1-2 lines) that points to the receipt card.
2) A Receipt Card UI immediately below (primary artifact).

Receipt Card properties:
- Always shows: title + status badge + one-paragraph outcome.
- Shows compact Change + Verification previews (small key/value rows).
- Optional "Details" opens the existing detail sheet for full evidence sections (still human-only).
- No raw JSON in the normal card or detail sheet.

Acceptance criteria (v1):
- Every terminal CHANGE workflow run produces exactly one receipt card in the thread.
- Receipt card fits in a single mobile screenshot in its default (collapsed) state.
- No raw JSON is shown in normal user flow (card + details).
- DENIED path still produces a receipt (even without a tool run).

## 5) Deterministic Receipt Data Contract

### 5.1 Reuse existing workflow payload shapes

From `apps/api/src/noa_api/core/workflows/types.py`:
- `replyTemplate` payload:
  - `title: string`
  - `outcome: "info"|"changed"|"no_op"|"partial"|"failed"|"denied"`
  - `summary: string`
  - `evidenceSummary: string[]`
  - `nextStep?: string | null`
  - `assistantHint?: string | null`
- `evidenceSections` payload:
  - `[{ key: string, title: string, items: [{ label: string, value: string }] }]`

Section keys to treat as canonical (already documented in `docs/assistant/workflow-templates.md`):
- `before_state`, `requested_change`, `after_state`, `verification`, `outcomes`, `failure`

### 5.2 Receipt envelope (binding + versioning)

To reliably bind a receipt to a specific action/run and to allow schema evolution, wrap the above in a small envelope.

Proposed `ChangeReceiptV1` (human-only payload):
```json
{
  "schemaVersion": 1,
  "receiptId": "<uuid-or-stable-id>",
  "threadId": "<uuid>",
  "actionRequestId": "<uuid>",
  "toolRunId": "<uuid-or-null>",
  "toolName": "whm_suspend_account",
  "workflowFamily": "whm-account-lifecycle",
  "terminalPhase": "completed|failed|denied",
  "generatedAt": "2026-03-19T12:34:56Z",
  "replyTemplate": { "title": "...", "outcome": "changed", "summary": "...", "evidenceSummary": [] },
  "evidenceSections": [{ "key": "requested_change", "title": "Requested change", "items": [{ "label": "Action", "value": "Suspend" }] }]
}
```

Binding invariants:
- `actionRequestId` always present.
- `toolRunId` required for `completed|failed`, null for `denied`.
- Receipts are immutable once persisted (web renders from persisted payload only).

### 5.3 Status mapping (UI badge)

Map `replyTemplate.outcome` to receipt badge:
- `changed` -> `SUCCESS`
- `partial` -> `PARTIAL`
- `no_op` -> `NO-OP`
- `failed` -> `FAILED`
- `denied` -> `DENIED`
- `info` -> (should not occur at terminal; treat as `NO-OP` or `SUCCESS` by policy)

## 6) Backend Plan (apps/api)

### 6.1 Generate terminal receipts from workflow templates

Implementation goal: build the same structured payload at terminal phases that we already have at approval-time.

Where the data comes from:
- `replyTemplate`: `build_workflow_reply_template(...)`
- `evidenceSections`: `build_workflow_evidence_template(...)` -> `workflow_evidence_template_payload(...)`
- Preflight evidence: `collect_recent_preflight_evidence(...)` (already in `apps/api/src/noa_api/core/workflows/types.py`)
- Postflight evidence (when available): `WorkflowTemplate.fetch_postflight_result(...)`

Receipt creation should happen deterministically in the approval executor paths, not in LLM narration.

### 6.2 Persist receipt payload (#27)

Recommended persistence model: dedicated table (queryable + immutable + decoupled from raw tool output).

Proposed DB change:
- New table `action_receipts` keyed by `action_requests.id`.
  - `action_request_id` UUID PK / FK
  - `tool_run_id` UUID nullable
  - `schema_version` int
  - `terminal_phase` text
  - `payload` JSONB
  - `created_at` timestamptz

Alternative (lower migration footprint, higher coupling):
- Store receipt under `tool_runs.result.receipt` and/or `action_requests.args.receipt`.
Tradeoff: mixes "raw tool output" with "human receipt payload", and makes future querying/backfill harder.

Idempotency:
- DB uniqueness should prevent duplicates (one receipt per action request / tool run).
- Terminal handlers should treat receipt creation as create-once.

### 6.3 Emit a receipt card into the thread (#26/#28)

We need a deterministic artifact in the thread that the web can render as a card.

Recommended transport:
- Emit an internal tool-call message part at terminal time:
  - `toolName: "workflow_receipt"`
  - `args: ChangeReceiptV1`
This makes the receipt appear in the thread without relying on assistant markdown.

Alternative transport:
- Extend `update_workflow_todo` tool-call payload to include `replyTemplate + evidenceSections` and render a receipt from that.
Tradeoff: conflates progress/todos with terminal receipts and can be confusing in multi-run threads.

### 6.4 Terminal phase coverage (completed / failed / denied)

Terminal events to handle:
- `completed`: after tool execution result persisted + postflight evidence fetched.
- `failed`: after tool run marked failed; include failure evidence section.
- `denied`: no tool run; still emit receipt (and minimal assistant message) because the agent does not rerun on deny.

Likely touch points (for later implementation):
- `apps/api/src/noa_api/api/assistant/assistant_action_operations.py`
- `apps/api/src/noa_api/core/workflows/registry.py`

## 7) Web Plan (apps/web)

### 7.1 Receipt Card UI (#28)

Implement a dedicated tool UI for `workflow_receipt` that renders the compact receipt card.

Recommended architecture:
- New tool UI component (tool-to-UI binding):
  - `apps/web/components/assistant/workflow-receipt-tool-ui.tsx` (new)
- Presentational card component:
  - `apps/web/components/assistant/change-receipt-card.tsx` (new)
- Reuse detail sheet for "Details":
  - `apps/web/components/assistant/assistant-detail-sheet.tsx`

Receipt layout rules (mobile-first, screenshot-safe):
- Header: 1-line title + status badge; optional status rail.
- Outcome: always visible, 2-line clamp.
- Compact previews:
  - "Change": up to 2 key/value rows
  - "Verification": up to 2 key/value rows
- Footer actions: `Details` + `Copy receipt`.
- Expanded content (if any) should prefer the detail sheet over a tall in-thread card.

Concrete UI spec (v1):
- Container: full width of chat column; rounded corners; subtle border/shadow; optional 4px status rail.
- Header (max 1 row):
  - Left: small "RECEIPT" label.
  - Title: 1-line truncation.
  - Right: status badge: `SUCCESS | PARTIAL | NO-OP | FAILED | DENIED`.
- Outcome (always visible; max 2 lines):
  - One-sentence outcome; for FAILED/DENIED append a short reason if available.
- Evidence preview (always visible; max 4 rows total):
  - Two blocks: "Change" (up to 2 items) and "Verification" (up to 2 items).
  - If more evidence exists: show a "+N more" hint that Details contains the rest.
- Footer (max 1 row):
  - Actions: `Details` (opens the detail sheet) + `Copy receipt` (plain text; no JSON).
- Accessibility:
  - Status must be conveyed by text, not color only.
  - "Details" uses proper dialog semantics; toggles use `aria-expanded` when applicable.

### 7.2 Deterministic rendering + run binding

Rendering must depend only on the receipt payload:
- stable section ordering comes from API
- stable React keys derive from `receiptId`
- guard multi-run threads by using `actionRequestId/toolRunId` for identity

### 7.3 Human-only guarantee

UI should assume evidence items are strings and:
- truncate long values
- avoid rendering nested objects
- never render raw JSON blobs in normal surfaces

## 8) Agent Narration Plan (#29)

Objective: assistant completion messages should not compete with the receipt card.

Behavior (terminal outcomes):
- 1-2 lines max.
- no tables
- no JSON
- explicit pointer: Receipt below.
- PARTIAL/FAILED: include the next safe step in one sentence when available.

Implementation levers:
- System prompt guidance (see `docs/assistant/system-prompt.md` and `apps/api/src/noa_api/core/prompts/noa-system-prompt.md`).
- Backend deny-path message (because the agent does not rerun on deny).

## 9) Testing / Verification

API:
- Unit tests for workflow template payload builders at terminal phases (completed/failed/denied).
- Integration tests that:
  - exactly one receipt is persisted per action
  - exactly one `workflow_receipt` tool-call part is emitted at terminal
  - deny path emits receipt even without tool run

Web:
- Component tests for receipt card rendering:
  - status badge mapping
  - truncation and max-row rules
  - "Details" shows full evidence sections
  - "Copy receipt" output stays human-readable (no JSON)

Manual (end-to-end):
- WHM happy-path, denied, forced-failure; confirm:
  - one receipt card per terminal action
  - assistant completion text is short and non-duplicative
  - the card fits in one mobile screenshot

## 10) Rollout / Sequencing

Suggested sequence to minimize risk:
1) Finalize receipt spec and deterministic contract (this document + #26).
2) Backend persistence + terminal emission (DB migration + #27).
3) Web receipt UI (new tool UI + #28).
4) Agent narration standardization (prompt + backend deny-path copy + #29).
5) Optional backfill for historical terminal actions.

## 11) Open Questions / Decisions

- Persistence location: new `action_receipts` table vs embedding inside `tool_runs.result`.
- Transport: separate `workflow_receipt` tool-call message vs extending `update_workflow_todo`.
- Evidence section typing on the web: keep ignoring `key`, or extend UI types to retain it for deterministic filtering.
- Backfill: do we want old threads to show receipts, or only new actions?
- Scope expansion: after WHM families, should non-workflow CHANGE tools also emit receipts (same spec)?

## 12) Issue-to-Deliverable Mapping

- #26: final receipt UX + data contract + truncation/ordering rules; confirm no JSON in normal flow.
- #27: API builds + persists terminal receipt payload and binds it to actionRequest/toolRun.
- #28: Web renders deterministic receipt card and integrates with detail sheet.
- #29: Agent completion narration is short, consistent, and always defers to the receipt card.
