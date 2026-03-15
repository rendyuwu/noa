# Backend Telemetry Next-Step Docs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refresh the audit wording so backend telemetry reconsideration is clearly the main remaining next step after the current structured log/event field set stabilizes.

**Scope:** Docs-only. Touch `docs/reports/2026-03-14-error-handling-and-logging-audit.md` plus this implementation-plan doc and its paired design doc only. Do not edit related handoff docs.

---

### Task 1: Reprioritize the active audit recommendation

**Files:**
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`

**Steps:**

1. Update the recommendation sentence near the completed-status bullets so it explicitly says revisiting backend telemetry is now the main next step.
2. Update the three-item `Active next steps` list so item 1 is the telemetry revisit after field stabilization.
3. Keep items 2 and 3 as later deeper helper/service logging work and shared/helper-level `error_code` catalog work, not more route-slice work.

### Task 2: Review the docs-only diff

**Files:**
- Review: `docs/plans/2026-03-15-backend-telemetry-next-step-docs-design.md`
- Review: `docs/plans/2026-03-15-backend-telemetry-next-step-docs-implementation-plan.md`
- Review: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`

**Steps:**

1. Run `git diff -- docs/plans/2026-03-15-backend-telemetry-next-step-docs-design.md docs/plans/2026-03-15-backend-telemetry-next-step-docs-implementation-plan.md docs/reports/2026-03-14-error-handling-and-logging-audit.md`.
2. Confirm the diff is limited to the two new planning docs plus the targeted audit wording refresh.
3. Commit with `docs: reprioritize backend telemetry follow-up`.
