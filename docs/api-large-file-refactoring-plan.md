# API Large-File Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split 10 oversized files in `apps/api` (14,349 lines total) into focused, single-responsibility modules without changing any runtime behavior.

**Architecture:** Pure structural refactoring — extract functions/classes into new modules, update imports, add re-exports for backward compatibility. No logic changes, no new features, no test rewrites. Every task ends with `uv run pytest -q` confirming 667 tests still pass.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, Pydantic v2, pytest

**Baseline:** 667 tests passing (`uv run pytest -q` from `apps/api/`)

---

## Conventions

- **All file paths** are relative to `apps/api/src/noa_api/` unless prefixed with `apps/api/` or `tests/`.
- **"Move"** means: cut from source, paste into target, update imports in source to re-export from target.
- **Re-exports:** When moving symbols out of a module that tests import from, add `from new_module import X` in the old module so existing test imports don't break. Remove re-exports in a later cleanup pass.
- **Test command:** `uv run pytest -q` from `apps/api/` (expects 667 passed).
- **Lint command:** `uv run ruff check src tests` from `apps/api/`.

---

## Phase 1: Extract Shared Infrastructure

These extractions unblock all later phases by creating shared modules that eliminate cross-file duplication and fix backward dependency directions.

---

### Task 1: Extract shared approval presentation helpers

**Files:**
- Create: `core/workflows/approval.py`
- Modify: `core/workflows/whm.py`
- Modify: `core/workflows/proxmox.py`

The approval presentation helpers (`_approval_detail_rows`, `_approval_paragraph_block`, `_approval_bullet_list_block`, `_approval_key_value_block`, `_approval_key_value_block_from_details`, `_approval_reason_detail`, `_approval_presentation`, `_approval_presentation_from_reply_data`) are duplicated nearly identically between `whm.py` and `proxmox.py`. Extract the WHM versions into a shared module, then update both files to import from it.

- [ ] **Step 1: Create `core/workflows/approval.py` with shared helpers**

Extract these functions from `core/workflows/whm.py` (lines 988–1067) into the new file. The Proxmox versions (lines 1674–1745) are structurally identical — the WHM versions are the canonical source.

```python
# core/workflows/approval.py
from __future__ import annotations

from noa_api.core.workflows.types import (
    WorkflowApprovalPresentation,
    WorkflowApprovalPresentationBlock,
)


def approval_detail_rows(*rows: tuple[str, str | None]) -> list[dict[str, str]]:
    """Build a list of label/value detail rows, skipping None values."""
    return [
        {"label": label, "value": value}
        for label, value in rows
        if value is not None
    ]


def approval_key_value_block_from_details(
    details: list[dict[str, str]],
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        type="key_value",
        items=[
            {"label": item["label"], "value": item["value"]}
            for item in details
        ],
    )


def approval_reason_detail(reason: str | None) -> str:
    if reason is None:
        return "(not yet provided)"
    return reason


def approval_paragraph_block(
    text: str | None,
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(type="paragraph", text=text or "")


def approval_bullet_list_block(
    items: list[str],
    *,
    title: str | None = None,
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        type="bullet_list", items=items, title=title
    )


def approval_key_value_block(
    items: list[dict[str, str]],
    *,
    title: str | None = None,
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        type="key_value", items=items, title=title
    )


def approval_presentation(
    *,
    summary: str | None = None,
    blocks: list[WorkflowApprovalPresentationBlock],
) -> WorkflowApprovalPresentation:
    return WorkflowApprovalPresentation(summary=summary, blocks=blocks)


def approval_presentation_from_reply_data(
    *,
    summary: str | None,
    details: list[dict[str, str]],
    evidence_summary: list[str],
    reason: str | None,
) -> WorkflowApprovalPresentation:
    blocks: list[WorkflowApprovalPresentationBlock] = []
    if summary:
        blocks.append(approval_paragraph_block(summary))
    if details:
        blocks.append(approval_key_value_block_from_details(details))
    if evidence_summary:
        blocks.append(approval_bullet_list_block(evidence_summary))
    return approval_presentation(summary=summary, blocks=blocks)
```

- [ ] **Step 2: Update `core/workflows/whm.py` to import from shared module**

Replace the local `_approval_*` functions (lines 988–1067) with imports from the shared module. Keep underscore-prefixed aliases for backward compatibility within the file:

```python
# At top of whm.py, add:
from noa_api.core.workflows.approval import (
    approval_detail_rows as _approval_detail_rows,
    approval_key_value_block_from_details as _approval_key_value_block_from_details,
    approval_reason_detail as _approval_reason_detail,
    approval_paragraph_block as _approval_paragraph_block,
    approval_bullet_list_block as _approval_bullet_list_block,
    approval_key_value_block as _approval_key_value_block,
    approval_presentation as _approval_presentation,
    approval_presentation_from_reply_data as _approval_presentation_from_reply_data,
)
```

Then delete the local function definitions (lines 988–1067). Also delete `_join_with_and` and `_approval_sentence_summary` only if they are not used elsewhere in the file (check first — they may be used by reply template builders).

- [ ] **Step 3: Update `core/workflows/proxmox.py` to import from shared module**

Replace the local `_approval_*` functions (lines 1674–1745) with the same imports. Keep underscore-prefixed aliases:

```python
# At top of proxmox.py, add:
from noa_api.core.workflows.approval import (
    approval_detail_rows as _approval_detail_rows,
    approval_key_value_block_from_details as _approval_key_value_block_from_details,
    approval_reason_detail as _approval_reason_detail,
    approval_paragraph_block as _approval_paragraph_block,
    approval_bullet_list_block as _approval_bullet_list_block,
    approval_key_value_block as _approval_key_value_block,
    approval_presentation as _approval_presentation,
    approval_presentation_from_reply_data as _approval_presentation_from_reply_data,
)
```

Then delete the local function definitions (lines 1674–1745). Proxmox also has `_approval_table_block` (line 1708) which is Proxmox-specific — leave it in `proxmox.py`.

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 5: Run lint**

Run: `uv run ruff check src tests`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/noa_api/core/workflows/approval.py src/noa_api/core/workflows/whm.py src/noa_api/core/workflows/proxmox.py
git commit -m "refactor: extract shared workflow approval helpers into core/workflows/approval.py"
```

---

### Task 2: Extract shared route telemetry helpers

**Files:**
- Create: `api/route_telemetry.py`
- Modify: `api/routes/admin.py`
- Modify: `api/routes/threads.py`

Both `admin.py` and `threads.py` define near-identical `_status_family`, `_safe_trace`, `_safe_metric`, and outcome-recording helpers. Extract into a shared module.

- [ ] **Step 1: Create `api/route_telemetry.py`**

```python
# api/route_telemetry.py
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from noa_api.core.telemetry import TelemetryEvent, get_telemetry_recorder

logger = logging.getLogger(__name__)


def status_family(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "2xx"
    if 400 <= status_code < 500:
        return "4xx"
    return "5xx"


def safe_trace(request: Request, event: TelemetryEvent) -> None:
    try:
        recorder = get_telemetry_recorder(request)
        recorder.record(event)
    except Exception:
        logger.debug("telemetry trace failed", exc_info=True)


def safe_metric(
    request: Request,
    *,
    name: str,
    value: float = 1.0,
    tags: dict[str, str] | None = None,
) -> None:
    try:
        recorder = get_telemetry_recorder(request)
        recorder.metric(name=name, value=value, tags=tags or {})
    except Exception:
        logger.debug("telemetry metric failed", exc_info=True)


def record_route_outcome(
    request: Request,
    *,
    metric_name: str,
    action: str,
    status_code: int,
    error_code: str | None = None,
    extra_tags: dict[str, str] | None = None,
) -> None:
    tags: dict[str, str] = {
        "action": action,
        "status_family": status_family(status_code),
    }
    if error_code is not None:
        tags["error_code"] = error_code
    if extra_tags:
        tags.update(extra_tags)
    safe_metric(request, name=metric_name, tags=tags)
```

- [ ] **Step 2: Update `api/routes/admin.py` to use shared telemetry**

Replace the local `_status_family`, `_safe_trace`, `_safe_metric`, and `_record_admin_outcome` (lines 136–198) with imports:

```python
from noa_api.api.route_telemetry import (
    safe_trace as _safe_trace,
    safe_metric as _safe_metric,
    record_route_outcome,
    status_family as _status_family,
)
```

Update `_record_admin_outcome` calls to use `record_route_outcome` with `metric_name=ADMIN_OUTCOMES_TOTAL`. If the local `_record_admin_outcome` has admin-specific logic beyond what `record_route_outcome` provides, keep a thin wrapper that delegates.

- [ ] **Step 3: Update `api/routes/threads.py` to use shared telemetry**

Same pattern — replace local `_status_family`, `_safe_trace`, `_safe_metric`, `_record_thread_outcome` (lines 330–391) with imports from `api/route_telemetry.py`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 5: Commit**

```bash
git add src/noa_api/api/route_telemetry.py src/noa_api/api/routes/admin.py src/noa_api/api/routes/threads.py
git commit -m "refactor: extract shared route telemetry helpers into api/route_telemetry.py"
```

---

### Task 3: Extract shared preflight validation (fix backward dependency)

**Files:**
- Create: `core/workflows/preflight_validation.py`
- Modify: `core/agent/runner.py`
- Modify: `api/assistant/assistant_action_operations.py`

Currently `assistant_action_operations.py` imports private helpers `_require_matching_preflight` and `_resolve_requested_server_id` from `runner.py`. This is a dependency inversion. Extract these into a shared module.

- [ ] **Step 1: Create `core/workflows/preflight_validation.py`**

Move `_require_matching_preflight` (runner.py line 1380) and `_resolve_requested_server_id` (runner.py line 1606) into this new module. Note: `_require_matching_preflight` is a thin wrapper around `require_matching_preflight` from `core/workflows/registry.py`. `_resolve_requested_server_id` depends on `SQLWHMServerRepository` and `resolve_whm_server_ref`.

```python
# core/workflows/preflight_validation.py
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.registry import require_matching_preflight
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.server_ref import resolve_whm_server_ref


def validate_matching_preflight(
    *,
    tool_name: str,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    return require_matching_preflight(
        tool_name=tool_name,
        args=args,
        working_messages=working_messages,
        requested_server_id=requested_server_id,
    )


async def resolve_requested_server_id(
    *,
    tool_name: str,
    args: dict[str, object],
    session: AsyncSession,
) -> str | None:
    server_choice = args.get("server") or args.get("server_name")
    if not isinstance(server_choice, str) or not server_choice.strip():
        return None
    repo = SQLWHMServerRepository(session)
    ref = await resolve_whm_server_ref(
        server_choice=server_choice.strip(),
        repository=repo,
    )
    if ref is None:
        return None
    return str(ref.server.id)
```

- [ ] **Step 2: Update `core/agent/runner.py`**

Replace the local `_require_matching_preflight` (line 1380) and `_resolve_requested_server_id` (line 1606) with imports from the new module. Keep the old names as re-exports for any other importers:

```python
# In runner.py, replace local definitions with:
from noa_api.core.workflows.preflight_validation import (
    validate_matching_preflight as _require_matching_preflight,
    resolve_requested_server_id as _resolve_requested_server_id,
)
```

Delete the local function bodies. Remove the now-unused imports of `SQLWHMServerRepository` and `resolve_whm_server_ref` from runner.py (only if nothing else in runner.py uses them — check first).

- [ ] **Step 3: Update `api/assistant/assistant_action_operations.py`**

Change the import from runner to the new shared module:

```python
# Replace:
from noa_api.core.agent.runner import (
    _require_matching_preflight,
    _resolve_requested_server_id,
)
# With:
from noa_api.core.workflows.preflight_validation import (
    validate_matching_preflight as _require_matching_preflight,
    resolve_requested_server_id as _resolve_requested_server_id,
)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 5: Run lint**

Run: `uv run ruff check src tests`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/noa_api/core/workflows/preflight_validation.py src/noa_api/core/agent/runner.py src/noa_api/api/assistant/assistant_action_operations.py
git commit -m "refactor: extract shared preflight validation, fix backward dependency from action_operations to runner"
```

---

## Phase 2: Split Workflow Modules

Split the two largest files in the codebase. Each workflow family becomes its own module within a package.

---

### Task 4: Split `core/workflows/whm.py` into a package

**Files:**
- Create: `core/workflows/whm/` package (10 files)
- Modify: `core/workflows/whm.py` → becomes `core/workflows/whm/__init__.py`
- Modify: `core/workflows/registry.py` (import path update)

**Strategy:** Create the package directory, move functions into focused modules, then convert the original `whm.py` into `whm/__init__.py` that re-exports everything for backward compatibility. This means NO test imports need to change.

- [ ] **Step 1: Create package directory**

```bash
mkdir -p apps/api/src/noa_api/core/workflows/whm_pkg
```

(We use a temp name `whm_pkg` to avoid conflicting with the existing `whm.py` file during the transition.)

- [ ] **Step 2: Create `whm_pkg/common.py` — result parsers and state extractors**

Move these functions from `whm.py`:
- `_format_argument_value` (line 912)
- `_extract_before_state` (line 926)
- `_clean_items` (line 1944)
- `_result_ok`, `_result_status`, `_result_message`, `_result_error_code` (lines 1998–2020)
- `_result_items` (line 2398)
- `_account_subject`, `_action_label` (lines 2036–2048)
- `_account_state`, `_account_email`, `_account_domain` (lines 2331–2363)
- `_domain_inventory`, `_requested_domain_location`, `_domain_owner`, `_dns_zone_exists` (lines 2366–2396)
- `_targets_with_status` (line 2023)
- `_default_step_statuses` (line 955)
- `_render_domain_list` (line 2086)
- `_join_with_and`, `_approval_sentence_summary` (lines 1070–1088)

Include necessary imports from `core/workflows/types`.

- [ ] **Step 3: Create `whm_pkg/matching.py` — preflight matching and validation**

Move these functions:
- `_server_identity_matches` (line 777)
- `_matching_account_preflight` (line 2264)
- `_matching_primary_domain_preflight` (line 2289)
- `_matching_firewall_preflight_entries` (line 2517)
- `_require_account_preflight` (line 661)
- `_require_primary_domain_preflight` (line 715)
- `_require_firewall_preflight` (line 2780)
- `_postflight_account` (line 2317)
- `_postflight_firewall_entries` (line 2543)
- `_account_preflight_candidates` (line 790)
- `_primary_domain_preflight_candidates` (line 801)

- [ ] **Step 4: Create `whm_pkg/inference.py` — user-text intent parsing**

Move these functions:
- `_latest_user_text` (line 812)
- `_infer_whm_account_lifecycle_tool_name` (line 828)
- `_select_account_preflight_candidate` (line 837)
- `_select_primary_domain_preflight_candidate` (line 858)
- `_extract_email` (line 896)
- `_extract_domain` (line 901)

- [ ] **Step 5: Create `whm_pkg/todo_helpers.py` — shared todo step content builders**

Move these functions:
- `_preflight_step_content` (line 2050)
- `_reason_step_content` (line 2070)
- `_postflight_step_content` (line 2203)
- `_conclusion_step_content` (line 2226)
- `_primary_domain_preflight_step_content` (line 2091)
- `_primary_domain_postflight_step_content` (line 2118)
- `_contact_email_postflight_step_content` (line 2150)
- `_contact_email_conclusion_step_content` (line 2171)
- `_account_before_state_items`, `_account_after_state_items` (lines 1948–1995)
- `_firewall_preflight_step_content` (line 2457)
- `_firewall_postflight_step_content` (line 2484)

- [ ] **Step 6: Create `whm_pkg/account_lifecycle.py`**

Move:
- `WHMAccountLifecycleTemplate` class (line 121)
- `_build_account_lifecycle_reply_template` wrapper (line 67)
- `_build_account_lifecycle_reply_template_impl` (line 1092)
- `_build_account_lifecycle_evidence_template` (line 1518)

Import shared helpers from `common.py`, `matching.py`, `todo_helpers.py`.

- [ ] **Step 7: Create `whm_pkg/contact_email.py`**

Move:
- `WHMAccountContactEmailTemplate` class (line 259)
- `_build_contact_email_reply_template` wrapper (line 73)
- `_build_contact_email_reply_template_impl` (line 1226)
- `_build_contact_email_evidence_template` (line 1638)

- [ ] **Step 8: Create `whm_pkg/primary_domain.py`**

Move:
- `WHMAccountPrimaryDomainTemplate` class (line 376)
- `_build_primary_domain_reply_template` wrapper (line 79)
- `_build_primary_domain_reply_template_impl` (line 1361)
- `_build_primary_domain_evidence_template` (line 1758)

- [ ] **Step 9: Create `whm_pkg/firewall.py`**

Move:
- `WHMFirewallBatchTemplate` class (line 535)
- All firewall-specific helpers (lines 2412–2778):
  - `_firewall_subject`, `_firewall_action_phrase`, `_firewall_missing_reason_text`, `_firewall_activity_phrase`
  - `_firewall_entries_summary`, `_firewall_available_tool_names`, `_firewall_entry_status_summary`
  - `_last_non_empty_line`, `_firewall_csf_receipt_value`, `_format_firewall_timestamp`
  - `_firewall_imunify_entry_value`, `_firewall_imunify_metadata_value`, `_firewall_imunify_receipt_value`
  - `_firewall_entry_receipt_items`, `_firewall_entries_items`, `_firewall_expected_state`
- `_build_firewall_reply_template` (line 2841)
- `_build_firewall_evidence_template` (line 2996)

- [ ] **Step 10: Create `whm_pkg/base.py` — base template classes**

Move:
- `_WHMTemplate` class (line 33)
- `_WHMAccountTemplate` class (line 85)

These depend on `matching.py` for `_require_account_preflight` and on `common.py` for `_extract_before_state`.

- [ ] **Step 11: Create `whm_pkg/__init__.py` with re-exports**

```python
# core/workflows/whm_pkg/__init__.py
from noa_api.core.workflows.whm_pkg.account_lifecycle import (
    WHMAccountLifecycleTemplate,
)
from noa_api.core.workflows.whm_pkg.contact_email import (
    WHMAccountContactEmailTemplate,
)
from noa_api.core.workflows.whm_pkg.primary_domain import (
    WHMAccountPrimaryDomainTemplate,
)
from noa_api.core.workflows.whm_pkg.firewall import WHMFirewallBatchTemplate

WORKFLOW_TEMPLATES: dict[str, "WorkflowTemplate"] = {
    "whm-account-lifecycle": WHMAccountLifecycleTemplate(),
    "whm-account-contact-email": WHMAccountContactEmailTemplate(),
    "whm-account-primary-domain": WHMAccountPrimaryDomainTemplate(),
    "whm-firewall-batch-change": WHMFirewallBatchTemplate(),
}

# Re-export everything tests might import
from noa_api.core.workflows.whm_pkg.base import _WHMTemplate, _WHMAccountTemplate
from noa_api.core.workflows.whm_pkg.common import *  # noqa: F401,F403
from noa_api.core.workflows.whm_pkg.matching import *  # noqa: F401,F403
from noa_api.core.workflows.whm_pkg.inference import *  # noqa: F401,F403
```

- [ ] **Step 12: Swap file to package**

```bash
cd apps/api/src/noa_api/core/workflows
mv whm.py whm_old.py
mv whm_pkg whm
```

- [ ] **Step 13: Update `core/workflows/registry.py`**

The import `from noa_api.core.workflows.whm import WORKFLOW_TEMPLATES as WHM_WORKFLOW_TEMPLATES` should still work because `whm/__init__.py` exports `WORKFLOW_TEMPLATES`.

Verify no import path changes are needed.

- [ ] **Step 14: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 15: Run lint**

Run: `uv run ruff check src tests`
Expected: No errors (may need `# noqa` annotations on star imports)

- [ ] **Step 16: Remove old file and commit**

```bash
cd apps/api/src/noa_api/core/workflows
rm whm_old.py
git add -A .
git commit -m "refactor: split core/workflows/whm.py (3150 lines) into whm/ package with per-family modules"
```

---

### Task 5: Split `core/workflows/proxmox.py` into a package

**Files:**
- Create: `core/workflows/proxmox/` package (9 files)
- Modify: `core/workflows/proxmox.py` → becomes `core/workflows/proxmox/__init__.py`
- Modify: `core/workflows/registry.py` (verify import still works)

Same strategy as Task 4.

- [ ] **Step 1: Create package directory**

```bash
mkdir -p apps/api/src/noa_api/core/workflows/proxmox_pkg
```

- [ ] **Step 2: Create `proxmox_pkg/common.py`**

Move shared helpers:
- `_normalized_int` (line 383)
- `_normalized_int_list` (line 1756)
- `_action_label`, `_approval_action_label`, `_desired_link_state`, `_action_verb`, `_action_completed_label`, `_action_outcome_adjective` (lines 389–423)
- `_subject`, `_title_subject` (lines 425–438)
- `_workflow_result_failed` (line 1670)
- `_vmids_text`, `_pool_value` (lines 1749–1770)
- `_upstream_error` (line 1626)

- [ ] **Step 3: Create `proxmox_pkg/matching.py`**

Move:
- `_server_identity_matches`, `_server_identity_matches_any` (lines 441–469)
- `_matching_preflight` (line 472)
- `_require_vm_nic_preflight` (line 524)
- `_matching_cloudinit_preflight` (line 2009)
- `_require_cloudinit_preflight` (line 2045)
- `_matching_pool_move_preflight` (line 2500)
- `_require_pool_move_preflight` (line 2551)

- [ ] **Step 4: Create `proxmox_pkg/postflight.py`**

Move:
- `_resolve_proxmox_client` (line 1481)
- `_cloudinit_postflight_result` (line 1510)
- `_pool_postflight_result` (line 1529)
- `_wait_for_cloudinit_verification` (line 1575)

- [ ] **Step 5: Create `proxmox_pkg/nic_connectivity.py`**

Move:
- `ProxmoxVMNicConnectivityTemplate` class (line 32)
- NIC-specific helpers: `_link_state`, `_preflight_content`, `_verification_content`, `_postflight_verified`, `_reason_step_content`, `_before_state_items`, `_after_state_items`, `_verification_items`, `_evidence_summary`, `_verification_summary_sentence`, `_final_link_state`, `_verification_confirmed` (lines 591–798)

- [ ] **Step 6: Create `proxmox_pkg/cloudinit_password_reset.py`**

Move:
- `ProxmoxVMCloudinitPasswordResetTemplate` class (line 800)
- Cloud-init helpers: `_cloudinit_subject`, `_cloudinit_confirms_password_reset`, `_cloudinit_approval_summary`, `_cloudinit_completion_summary`, `_cloudinit_preflight_content`, `_cloudinit_verification_content`, `_cloudinit_before_state_items`, `_cloudinit_after_state_items`, `_cloudinit_verification_items`, `_cloudinit_evidence_summary` (lines 1474–2007)

- [ ] **Step 7: Create `proxmox_pkg/pool_membership_move.py`**

Move:
- `ProxmoxPoolMembershipMoveTemplate` class (line 1147)
- Pool helpers: `_pool_move_subject`, `_pool_result_vmids`, `_pool_members_from_result`, `_pool_table`, `_pool_move_preflight_content`, `_pool_move_verification_content`, `_pool_move_before_state_items`, `_pool_move_after_state_items`, `_pool_move_verification_items`, `_pool_move_evidence_summary`, `_pool_move_requested_change_*`, `_pool_move_approval_*`, `_pool_move_completion_summary`, `_pool_move_verified`, `_pool_move_verification_summary_lines`, `_pool_move_postflight_state`, `_pool_move_postflight_summary_line`, `_pool_name` (lines 1147–1471, 1652–1670, 2100–2620)

- [ ] **Step 8: Create `proxmox_pkg/__init__.py` with re-exports**

```python
from noa_api.core.workflows.proxmox_pkg.nic_connectivity import (
    ProxmoxVMNicConnectivityTemplate,
)
from noa_api.core.workflows.proxmox_pkg.cloudinit_password_reset import (
    ProxmoxVMCloudinitPasswordResetTemplate,
)
from noa_api.core.workflows.proxmox_pkg.pool_membership_move import (
    ProxmoxPoolMembershipMoveTemplate,
)
from noa_api.core.workflows.types import WorkflowTemplate

WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {
    "proxmox-vm-nic-connectivity": ProxmoxVMNicConnectivityTemplate(),
    "proxmox-vm-cloudinit-password-reset": ProxmoxVMCloudinitPasswordResetTemplate(),
    "proxmox-pool-membership-move": ProxmoxPoolMembershipMoveTemplate(),
}
```

- [ ] **Step 9: Swap file to package**

```bash
cd apps/api/src/noa_api/core/workflows
mv proxmox.py proxmox_old.py
mv proxmox_pkg proxmox
```

- [ ] **Step 10: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 11: Run lint and commit**

```bash
uv run ruff check src tests
cd apps/api/src/noa_api/core/workflows && rm proxmox_old.py
git add -A .
git commit -m "refactor: split core/workflows/proxmox.py (2640 lines) into proxmox/ package with per-family modules"
```

---

## Phase 3: Split Agent Core

Split the agent runner and fix the duplicated execution paths.

---

### Task 6: Extract LLM client from `core/agent/runner.py`

**Files:**
- Create: `core/agent/llm_client.py`
- Modify: `core/agent/runner.py`

- [ ] **Step 1: Create `core/agent/llm_client.py`**

Move these from `runner.py`:
- `LLMToolCall` dataclass (line 49)
- `LLMTurnResponse` dataclass (line 55)
- `LLMClientProtocol` (line 61)
- `OpenAICompatibleLLMClient` class (line 273)
- `create_default_llm_client` function (line 1226)
- `_split_text_deltas` helper (line 2152)

Include necessary imports (`dataclasses`, `typing`, `Protocol`, `openai`, `Settings`).

- [ ] **Step 2: Update `runner.py` to import from `llm_client.py`**

```python
from noa_api.core.agent.llm_client import (
    LLMClientProtocol,
    LLMToolCall,
    LLMTurnResponse,
    OpenAICompatibleLLMClient,
    create_default_llm_client,
    _split_text_deltas,
)
```

Delete the local definitions. Keep re-exports in runner.py for backward compatibility (tests import `LLMToolCall`, `OpenAICompatibleLLMClient`, `create_default_llm_client` from `runner`).

- [ ] **Step 3: Create `core/agent/__init__.py`** (if it doesn't exist)

```python
# core/agent/__init__.py
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 5: Commit**

```bash
git add src/noa_api/core/agent/
git commit -m "refactor: extract LLM client types and OpenAI adapter from runner.py into llm_client.py"
```

---

### Task 7: Extract message codec from `core/agent/runner.py`

**Files:**
- Create: `core/agent/message_codec.py`
- Modify: `core/agent/runner.py`

- [ ] **Step 1: Create `core/agent/message_codec.py`**

Move these functions:
- `_as_object_dict` (line 120)
- `_assistant_message_parts` (line 126)
- `_append_assistant_text_to_working_messages` (line 139)
- `_append_assistant_text_to_output_messages` (line 158)
- `_should_persist_assistant_text_this_round` (line 173)
- `_should_suppress_provisional_assistant_text_this_round` (line 185)
- `_message_visible_text` (line 201)
- `_render_workflow_milestone_text` (line 216)
- `_finalize_turn_messages` (line 227)
- `_prompt_replay_parts` (line 261)
- `_to_openai_chat_messages` (line 1238)
- `_safe_json_object` (line 1328)
- `_extract_reasoning_summary` (line 1341)

Also move `AgentMessage` and `AgentRunnerResult` dataclasses (lines 72–80) here since they are the message types.

- [ ] **Step 2: Update `runner.py` to import from `message_codec.py`**

Add imports and delete local definitions. Keep re-exports for `AgentMessage`, `AgentRunnerResult`, `_to_openai_chat_messages`, `_build_approval_context` (tests import these from `runner`).

- [ ] **Step 3: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 4: Commit**

```bash
git add src/noa_api/core/agent/
git commit -m "refactor: extract message codec and type definitions from runner.py into message_codec.py"
```

---

### Task 8: Extract tool schema and guidance helpers from `core/agent/runner.py`

**Files:**
- Create: `core/agent/tool_schemas.py`
- Create: `core/agent/guidance.py`
- Modify: `core/agent/runner.py`

- [ ] **Step 1: Create `core/agent/tool_schemas.py`**

Move:
- `_to_openai_tool_schema` (line 1635)
- `_llm_tool_description` (line 1646)
- `_tool_risk_note` (line 1652)
- `_build_approval_context` (line 1622)

- [ ] **Step 2: Create `core/agent/guidance.py`**

Move:
- `_tool_error_messages` (line 1658)
- `_assistant_guidance_for_change_validation_error` (line 1692)
- `_internal_tool_guidance` (line 1712)
- `_should_stop_after_internal_tool_guidance` (line 1726)
- `_post_tool_followup_guidance` (line 1865)
- `_preflight_retry_guidance` (line 1883)
- `_preflight_user_retry_reply` (line 1900)
- `_extract_firewall_preflight_raw_outputs` (line 1922)
- `_render_firewall_preflight_raw_output` (line 1955)
- `_append_firewall_preflight_raw_output` (line 1962)

- [ ] **Step 3: Update `runner.py` imports**

Import from new modules. Keep re-exports for `_to_openai_tool_schema` and `_build_approval_context` (tests import these from `runner`).

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 5: Commit**

```bash
git add src/noa_api/core/agent/
git commit -m "refactor: extract tool schema builders and guidance helpers from runner.py"
```

---

### Task 9: Extract fallback reply helpers from `core/agent/runner.py`

**Files:**
- Create: `core/agent/fallbacks.py`
- Modify: `core/agent/runner.py`

- [ ] **Step 1: Create `core/agent/fallbacks.py`**

Move:
- `_latest_tool_result_part` (line 1730)
- `_tool_call_args_for_id` (line 1746)
- `_canonical_tool_args` (line 1767)
- `_working_messages_after_part` (line 1771)
- `_has_fresh_matching_preflight_after_failed_tool_result` (line 1798)
- `_latest_matching_failed_tool_result_part` (line 1831)
- `_fallback_assistant_reply_from_recent_tool_result` (line 1973)
- `_assistant_reply_from_tool_result_part` (line 1986)
- `_generic_read_success_fallback` (line 2087)
- `_generic_read_result_count` (line 2123)
- `_infer_waiting_on_user_workflow_from_messages` (line 2137)

- [ ] **Step 2: Update `runner.py` imports**

- [ ] **Step 3: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 4: Commit**

```bash
git add src/noa_api/core/agent/
git commit -m "refactor: extract fallback reply and retry helpers from runner.py into fallbacks.py"
```

---

### Task 10: Extract change-reason validation from `core/agent/runner.py`

**Files:**
- Create: `core/agent/change_validation.py`
- Modify: `core/agent/runner.py`

- [ ] **Step 1: Create `core/agent/change_validation.py`**

Move:
- `_normalized_text` (line 1395)
- `_reason_provenance_tokens` (line 1402)
- `_reason_tokens_are_explicit_in_latest_user_turn` (line 1431)
- `_is_reason_provenance_error` (line 1447)
- `_latest_user_message_text` (line 1456)
- `_validate_change_reason_provenance` (line 1478)
- `_canonicalize_reason_follow_up_args` (line 1521)
- `_matches_reason_follow_up_workflow_action` (line 1562)
- `_tool_args_without_reason` (line 1589)
- `_message_has_text` (line 1593)

- [ ] **Step 2: Update `runner.py` imports**

- [ ] **Step 3: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 4: Commit**

```bash
git add src/noa_api/core/agent/
git commit -m "refactor: extract change-reason validation from runner.py into change_validation.py"
```

---

## Phase 4: Split Tool Infrastructure

---

### Task 11: Split `core/tools/registry.py` into organized modules

**Files:**
- Create: `core/tools/types.py`
- Create: `core/tools/schema_builders.py`
- Create: `core/tools/schemas/` package (3 files)
- Create: `core/tools/definitions/` package (4 files)
- Modify: `core/tools/registry.py` (becomes thin re-export hub)

- [ ] **Step 1: Create `core/tools/types.py`**

Move:
- `ToolExecutor` type alias (line 58)
- `ToolParametersSchema` type alias (line 59)
- `ToolResultSchema` type alias (line 60)
- `ToolDefinition` dataclass (line 64)

- [ ] **Step 2: Create `core/tools/schema_builders.py`**

Move all schema builder functions (lines 75–265):
- `_object_schema`, `_string_param`, `_integer_param`, `_string_array_param`, `_integer_array_param`
- `_result_object_schema`, `_result_array_schema`, `_result_string_schema`, `_result_boolean_schema`
- `_result_any_of`, `_result_null_schema`, `_result_nullable_schema`, `_result_json_value_schema`
- `_result_integer_schema`, `_result_json_object_schema`, `_result_json_array_schema`
- `_result_upstream_response_schema`, `_result_vm_data_schema`, `_result_pool_response_schema`

- [ ] **Step 3: Create `core/tools/schemas/__init__.py`, `common.py`, `whm.py`, `proxmox.py`**

Split the schema constants (lines 267–979) by domain:
- `common.py`: shared schemas (server choice, todo, generic success/error)
- `whm.py`: WHM-specific result schemas (account, firewall, domain, preflight)
- `proxmox.py`: Proxmox-specific result schemas (VM, pool, cloud-init, NIC)

- [ ] **Step 4: Create `core/tools/definitions/__init__.py`, `common.py`, `whm.py`, `proxmox.py`**

Split `_MVP_TOOLS` (lines 980–1670) by domain:
- `common.py`: `get_current_time`, `get_current_date`, `update_workflow_todo` definitions
- `whm.py`: all WHM tool definitions (read, preflight, account change, firewall)
- `proxmox.py`: all Proxmox tool definitions (read, NIC, cloud-init, pool)
- `__init__.py`: merge all tuples into `ALL_TOOLS`

- [ ] **Step 5: Update `core/tools/registry.py` to be a thin hub**

```python
from noa_api.core.tools.types import ToolDefinition, ToolExecutor
from noa_api.core.tools.definitions import ALL_TOOLS

_MVP_TOOLS = ALL_TOOLS
_MVP_TOOL_INDEX: dict[str, ToolDefinition] = {t.name: t for t in _MVP_TOOLS}

def get_tool_registry() -> tuple[ToolDefinition, ...]:
    return _MVP_TOOLS

def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return _MVP_TOOL_INDEX.get(tool_name)

def get_tool_names() -> tuple[str, ...]:
    return tuple(t.name for t in _MVP_TOOLS)
```

Keep re-exports of `ToolDefinition` for backward compatibility.

- [ ] **Step 6: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 7: Commit**

```bash
git add src/noa_api/core/tools/
git commit -m "refactor: split core/tools/registry.py (1683 lines) into types, schemas, and per-domain definitions"
```

---

### Task 12: Split `whm/tools/firewall_tools.py` into a package

**Files:**
- Create: `whm/tools/firewall/` package (5 files)
- Modify: `whm/tools/firewall_tools.py` → becomes `whm/tools/firewall/__init__.py`

- [ ] **Step 1: Create `whm/tools/firewall/csf_backend.py`**

Move CSF-specific operations:
- `_csf_preflight` (line 81)
- `_csf_unblock` (line 107)
- `_csf_allowlist_add_ttl` (line 119)
- `_csf_allowlist_remove` (line 132)
- `_csf_denylist_add_ttl` (line 144)

- [ ] **Step 2: Create `whm/tools/firewall/imunify_backend.py`**

Move Imunify-specific operations:
- `_imunify_preflight` (line 162)
- `_imunify_blacklist_remove` (line 187)
- `_imunify_whitelist_add_ttl` (line 201)
- `_imunify_whitelist_remove` (line 228)
- `_imunify_blacklist_add_ttl` (line 241)

- [ ] **Step 3: Create `whm/tools/firewall/common.py`**

Move:
- `_LFD_AUTH_LINE_RE` constant (line 33)
- `_extract_lfd_auth_line` (line 39)
- `_resolution_error` (line 59)
- `_no_firewall_tools_error` (line 68)
- `_compute_combined_verdict` (line 273)

- [ ] **Step 4: Create `whm/tools/firewall/__init__.py` with public tool functions**

Keep the public tool handlers here (they orchestrate the backends):
- `whm_preflight_firewall_entries` (line 294)
- `whm_firewall_unblock` (line 382)
- `whm_firewall_allowlist_add_ttl` (line 564)
- `whm_firewall_allowlist_remove` (line 748)
- `whm_firewall_denylist_add_ttl` (line 891)

Import from `csf_backend`, `imunify_backend`, `common`.

- [ ] **Step 5: Update imports in `core/tools/registry.py`**

The registry imports `from noa_api.whm.tools.firewall_tools import ...`. Update to `from noa_api.whm.tools.firewall import ...`.

Also update `core/workflows/whm.py` (or `whm/__init__.py`) which imports `whm_preflight_firewall_entries`.

- [ ] **Step 6: Swap file to package**

```bash
cd apps/api/src/noa_api/whm/tools
mv firewall_tools.py firewall_tools_old.py
# (firewall/ directory already created in steps 1-4)
# Verify __init__.py re-exports all public names
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 8: Clean up and commit**

```bash
rm apps/api/src/noa_api/whm/tools/firewall_tools_old.py
git add -A .
git commit -m "refactor: split whm/tools/firewall_tools.py (1072 lines) into firewall/ package with CSF/Imunify backends"
```

---

## Phase 5: Split Route Modules

---

### Task 13: Extract AssistantService from `api/routes/assistant.py`

**Files:**
- Create: `api/assistant/schemas.py`
- Create: `api/assistant/service.py`
- Create: `api/assistant/run_lifecycle.py`
- Create: `api/assistant/dependencies.py`
- Modify: `api/routes/assistant.py`

- [ ] **Step 1: Create `api/assistant/schemas.py`**

Move Pydantic models (lines 100–158):
- `AssistantThreadStateMessage`
- `AssistantWorkflowTodo`
- `AssistantPendingApproval`
- `AssistantActionRequest`
- `AssistantThreadStateResponse`
- `AssistantRunAckResponse`

- [ ] **Step 2: Create `api/assistant/service.py`**

Move `AssistantService` class (lines 207–533) and its serialization helpers:
- `_serialize_pending_approval` (line 161)
- `_action_request_lifecycle_status` (line 171)
- `_serialize_action_request` (line 189)

- [ ] **Step 3: Create `api/assistant/run_lifecycle.py`**

Move run lifecycle helpers:
- `_coerce_run_id` (line 578)
- `_extract_waiting_action_request_id` (line 587)
- `_canonical_active_run_id` (line 603)
- `_should_resume_existing_run` (line 613)
- `_coordinator_task_done` (line 621)
- `_coordinator_task` (line 633)
- `_coordinator_sequence` (line 648)
- `_snapshot_is_terminal` (line 658)
- `_terminal_live_event` (line 667)
- `_terminal_failure_reason` (line 689)
- `_state_has_current_error_message` (line 705)
- `_wait_for_tracked_run_completion` (line 728)
- `_persist_terminal_run_state` (line 754)
- `_execute_detached_run_job` (line 809)
- `_run_detached_assistant_turn` (line 868)

- [ ] **Step 4: Create `api/assistant/dependencies.py`**

Move:
- `_build_assistant_service` (line 535)
- `_build_authorization_service` (line 556)
- `get_assistant_service` (line 560)
- `get_assistant_run_coordinator` (line 574)

- [ ] **Step 5: Update `api/routes/assistant.py`**

Keep only:
- `router` definition
- `_http_exception_error_code` helper
- `_require_active_user` guard
- `get_thread_state` route handler
- `assistant_transport` route handler
- `get_assistant_run_live` route handler

Import everything else from the new modules. Add re-exports for backward compatibility:

```python
# Re-exports for test compatibility
from noa_api.api.assistant.service import AssistantService  # noqa: F401
from noa_api.api.assistant.dependencies import (  # noqa: F401
    get_assistant_service,
    get_assistant_run_coordinator,
)
from noa_api.api.assistant.schemas import (  # noqa: F401
    AssistantThreadStateResponse,
    AssistantRunAckResponse,
)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 7: Commit**

```bash
git add src/noa_api/api/assistant/ src/noa_api/api/routes/assistant.py
git commit -m "refactor: extract AssistantService, schemas, run lifecycle, and dependencies from routes/assistant.py"
```

---

### Task 14: Split `api/routes/admin.py` by domain

**Files:**
- Create: `api/admin/schemas.py`
- Create: `api/admin/user_routes.py`
- Create: `api/admin/role_routes.py`
- Modify: `api/routes/admin.py`

- [ ] **Step 1: Create `api/admin/__init__.py`**

```python
# api/admin/__init__.py
```

- [ ] **Step 2: Create `api/admin/schemas.py`**

Move all Pydantic models (lines 48–134):
- `AdminUserResponse`, `AdminUsersResponse`, `UpdateUserRequest`, `UpdateUserResponse`
- `DeleteUserResponse`, `AdminToolsResponse`, `SetUserToolsRequest`
- `DirectGrantsMigrationResponse`, `AdminRolesResponse`, `CreateRoleRequest`
- `AdminRoleResponse`, `DeleteRoleResponse`, `SetRoleToolsRequest`
- `RoleToolsResponse`, `SetUserRolesRequest`
- `_to_user_response` helper (line 93)

- [ ] **Step 3: Create `api/admin/user_routes.py`**

Move user-related routes:
- `list_users` (line 232)
- `update_user_active` (line 254)
- `delete_user` (line 335)
- `set_user_roles` (line 755)
- `list_tools` (line 415)
- `set_user_tools` (line 437, disabled endpoint)
- `migrate_direct_grants` (line 469)

Create a sub-router: `user_router = APIRouter()`.

- [ ] **Step 4: Create `api/admin/role_routes.py`**

Move role-related routes:
- `list_roles` (line 495)
- `create_role` (line 517)
- `delete_role` (line 566)
- `get_role_tools` (line 627)
- `set_role_tools` (line 676)

Create a sub-router: `role_router = APIRouter()`.

- [ ] **Step 5: Update `api/routes/admin.py`**

Keep only the router composition:

```python
from fastapi import APIRouter
from noa_api.api.admin.user_routes import user_router
from noa_api.api.admin.role_routes import role_router

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(user_router)
router.include_router(role_router)
```

Keep `_require_admin` guard either in admin.py or in a shared `api/admin/guards.py`.

- [ ] **Step 6: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 7: Commit**

```bash
git add src/noa_api/api/admin/ src/noa_api/api/routes/admin.py
git commit -m "refactor: split api/routes/admin.py (866 lines) into admin/ package with user and role sub-routers"
```

---

### Task 15: Split `api/routes/threads.py` into focused modules

**Files:**
- Create: `api/threads/schemas.py`
- Create: `api/threads/repository.py`
- Create: `api/threads/service.py`
- Create: `api/threads/title_generation.py`
- Modify: `api/routes/threads.py`

- [ ] **Step 1: Create `api/threads/__init__.py`**

```python
# api/threads/__init__.py
```

- [ ] **Step 2: Create `api/threads/schemas.py`**

Move Pydantic models (lines 32–89):
- `ThreadResponse`, `ThreadListResponse`, `CreateThreadRequest`
- `UpdateThreadRequest`, `GenerateTitleRequest`, `GenerateTitleResponse`
- `_to_thread_response` helper (line 317)

- [ ] **Step 3: Create `api/threads/repository.py`**

Move `SQLThreadRepository` class (lines 125–245).

- [ ] **Step 4: Create `api/threads/service.py`**

Move `ThreadService` class (lines 247–314).

- [ ] **Step 5: Create `api/threads/title_generation.py`**

Move:
- `_extract_text_chunks` (line 92)
- `_message_text_chunks` (line 114)

These are used by `generate_thread_title` route handler.

- [ ] **Step 6: Update `api/routes/threads.py`**

Keep only:
- `router` definition
- Route handlers (list, create, get, patch, archive, unarchive, delete, generate_title)
- `_raise_thread_not_found` helper
- `get_thread_service` dependency
- `_require_active_user` guard

Import schemas, repository, service, title helpers from new modules. Add re-export for `get_thread_service` (tests import it from `routes.threads`).

- [ ] **Step 7: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 8: Commit**

```bash
git add src/noa_api/api/threads/ src/noa_api/api/routes/threads.py
git commit -m "refactor: split api/routes/threads.py (761 lines) into threads/ package with repository, service, and schemas"
```

---

## Phase 6: Split Authorization

---

### Task 16: Split `core/auth/authorization.py` into focused modules

**Files:**
- Create: `core/auth/authorization_types.py`
- Create: `core/auth/authorization_errors.py`
- Create: `core/auth/authorization_repository.py`
- Create: `core/auth/authorization_service.py`
- Modify: `core/auth/authorization.py` (becomes re-export hub)

- [ ] **Step 1: Create `core/auth/authorization_errors.py`**

Move all exception classes (lines 25–70):
- `UnknownToolError`, `LastActiveAdminError`, `SelfDeactivateAdminError`
- `SelfDeleteAdminError`, `InvalidRoleNameError`, `ReservedRoleError`
- `InternalRoleError`, `RoleNotFoundError`, `UnknownRoleError`
- `SelfRemoveAdminRoleError`

- [ ] **Step 2: Create `core/auth/authorization_types.py`**

Move:
- `AuthorizationUser` dataclass (line 74)
- `DirectGrantsMigrationSummary` TypedDict (line 141)
- `AuthorizationRepositoryProtocol` (line 86)

- [ ] **Step 3: Create `core/auth/authorization_repository.py`**

Move `SQLAuthorizationRepository` class (lines 150–366).

- [ ] **Step 4: Create `core/auth/authorization_service.py`**

Move `AuthorizationService` class (lines 368–741) and `get_authorization_service` dependency (line 744).

- [ ] **Step 5: Update `core/auth/authorization.py` as re-export hub**

```python
# core/auth/authorization.py — backward-compatible re-exports
from noa_api.core.auth.authorization_errors import (  # noqa: F401
    UnknownToolError,
    LastActiveAdminError,
    SelfDeactivateAdminError,
    SelfDeleteAdminError,
    InvalidRoleNameError,
    ReservedRoleError,
    InternalRoleError,
    RoleNotFoundError,
    UnknownRoleError,
    SelfRemoveAdminRoleError,
)
from noa_api.core.auth.authorization_types import (  # noqa: F401
    AuthorizationUser,
    DirectGrantsMigrationSummary,
    AuthorizationRepositoryProtocol,
)
from noa_api.core.auth.authorization_repository import (  # noqa: F401
    SQLAuthorizationRepository,
)
from noa_api.core.auth.authorization_service import (  # noqa: F401
    AuthorizationService,
    get_authorization_service,
)
```

This means NO existing imports anywhere in the codebase need to change.

- [ ] **Step 6: Run tests**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 7: Commit**

```bash
git add src/noa_api/core/auth/
git commit -m "refactor: split core/auth/authorization.py (753 lines) into errors, types, repository, and service modules"
```

---

## Final Verification

### Task 17: Full verification pass

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -q`
Expected: 667 passed

- [ ] **Step 2: Run linter**

Run: `uv run ruff check src tests`
Expected: No errors

- [ ] **Step 3: Run formatter check**

Run: `uv run ruff format --check src tests`
Expected: No changes needed

- [ ] **Step 4: Verify no behavior changes**

Run: `uv run pytest -q --tb=short`
Expected: All 667 tests pass with no warnings about import deprecations

- [ ] **Step 5: Final commit (if any cleanup needed)**

```bash
git add -A .
git commit -m "refactor: final cleanup after large-file refactoring"
```

---

## Summary of Expected Outcomes

| Metric | Before | After |
|--------|--------|-------|
| Largest source file | 3,150 lines (`whm.py`) | ~400 lines (largest workflow family) |
| Files > 1,000 lines | 7 | 0 |
| Files > 500 lines | 14 | ~4 (route handlers with many endpoints) |
| `runner.py` | 2,157 lines | ~500 lines (orchestrator only) |
| `registry.py` | 1,683 lines | ~30 lines (thin hub) |
| Total source files | 102 | ~145 |
| Tests passing | 667 | 667 (no changes) |
