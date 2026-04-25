# Workflow templates

NOA workflow UI is driven by canonical workflow todos stored in assistant thread state. CHANGE tools use a reason-first approval flow: they require matching preflight evidence and a clear recorded user reason before an approval request is created. To add a new operational tool family, register a workflow template on the API side and keep the web dock/detail UI unchanged.

## How registration works

1. Add a `workflow_family` value to each `ToolDefinition` in `apps/api/src/noa_api/core/tools/definitions/` (e.g. `whm.py` or `proxmox.py`).
2. Implement a template in `apps/api/src/noa_api/core/workflows/<family>/` by subclassing `WorkflowTemplate` from `apps/api/src/noa_api/core/workflows/types.py`.
3. Register the template in `apps/api/src/noa_api/core/workflows/registry.py` with `register_workflow_template(...)`.

The registry is the orchestration boundary. Once a family is registered, the existing workflow dock, detail sheet, approval cards, and thread state transport continue to work without UI changes.

Family-specific operational reply behavior should also live here rather than in bespoke runner branches or tool-specific web UI logic.

## Template hooks

- `build_todos(...)`: required; returns canonical workflow steps for each lifecycle phase.
- `describe_activity(...)`: optional; customizes approval activity copy.
- `build_before_state(...)`: optional; maps preflight evidence into approval before-state rows.
- `build_evidence_template(...)`: optional; returns structured evidence sections (`before_state`, `requested_change`, `after_state`, `verification`, `outcomes`, `failure`, `preflight_results`) for approval/completion receipts.
- `build_reply_template(...)`: optional; returns structured reply guidance for workflow phases (`waiting_on_user`, `waiting_on_approval`, `executing`, `completed`, `denied`, `failed`). Use it to ask for a missing or unclear reason, such as `Ticket #1661262`, another osTicket/reference number, or a brief description.
- `require_preflight(...)`: optional; blocks unsafe CHANGE requests until matching evidence exists. CHANGE tools should not create `action_requests` until this preflight gate passes and a clear user reason has been captured.
- `fetch_postflight_result(...)`: optional; loads verification evidence after execution.
- `infer_waiting_on_user_workflow(...)`: optional; seeds a waiting workflow when the assistant asks the user for missing input without emitting a CHANGE tool call yet. Use this when the reason is missing or ambiguous so the workflow stays in `waiting_on_user` while asking for an osTicket/reference number or a brief description.

The reply/evidence contracts are structured data, not markdown-authored prose, so the same family-owned semantics can drive assistant replies now and richer receipts/detail views later.

Workflow replies are phase-owned milestone narration, not a transcript of every internal LLM round.
When a workflow family returns reply semantics for `waiting_on_user`, `waiting_on_approval`, or terminal outcomes, those replies are the canonical user-visible milestones for that phase. Intermediate model narration that does not change conversation state should not be persisted alongside them.

Current CHANGE workflow families use a backend-owned `waiting_on_approval` handoff. The model gathers required inputs, runs family preflight, and calls the underlying CHANGE tool; after that validated CHANGE call succeeds, the backend workflow template owns the approval narration and approval card payload. Different families may keep different raw preflight schemas, but they should map those results into the shared approval-handoff contract exposed to the workflow UI.

Workflow families may also provide a structured approval narration presentation contract that the backend renders centrally to markdown for the approval handoff. That presentation layer is descriptive only and does not replace the canonical structured approval payload consumed by the workflow UI and persisted in thread state.

When a workflow needs a change reason, the prompt should mention a ticket reference like `Ticket #1661262` (or another osTicket/reference number) or ask for a brief human-readable description. If the reason is missing or ambiguous, keep the workflow in `waiting_on_user` instead of creating an approval request.

`build_before_state(...)` is now a compatibility shim. New workflow families should primarily implement `build_evidence_template(...)`; the registry projects `beforeState` from the `before_state` evidence section when present, and only falls back to `build_before_state(...)` for legacy templates.

## Family module shape

```python
from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowEvidenceSection,
    WorkflowEvidenceTemplate,
    WorkflowReplyTemplate,
    WorkflowTemplate,
    WorkflowTemplateContext,
)


class ProxmoxVmPowerTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext):
        return [
            {"content": "Preflight VM state.", "status": "completed", "priority": "high"},
            {"content": "Request approval.", "status": "waiting_on_approval", "priority": "high"},
        ]

    def build_reply_template(self, context: WorkflowTemplateContext):
        return WorkflowReplyTemplate(
            title="VM power approval requested",
            outcome="info",
            summary="This will power off the VM after approval.",
            evidence_summary=["Preflight found the VM running."],
            next_step="Approve the request to continue, or deny it to leave the VM unchanged.",
        )

    def build_evidence_template(self, context: WorkflowTemplateContext):
        return WorkflowEvidenceTemplate(
            sections=[
                WorkflowEvidenceSection(
                    key="before_state",
                    title="Before state",
                    items=[WorkflowEvidenceItem(label="State", value="running")],
                ),
                WorkflowEvidenceSection(
                    key="requested_change",
                    title="Requested change",
                    items=[WorkflowEvidenceItem(label="Action", value="power off")],
                ),
            ]
        )


WORKFLOW_TEMPLATES = {
    "proxmox-vm-power": ProxmoxVmPowerTemplate(),
}
```

Then register it:

```python
from noa_api.core.workflows.proxmox import WORKFLOW_TEMPLATES as PROXMOX_WORKFLOW_TEMPLATES

for family, template in PROXMOX_WORKFLOW_TEMPLATES.items():
    register_workflow_template(family=family, template=template)
```

## Current registered workflow families

| Family | Template class | Package |
|--------|---------------|---------|
| `whm-account-lifecycle` | `WHMAccountLifecycleTemplate` | `core/workflows/whm/account_lifecycle.py` |
| `whm-account-contact-email` | `WHMAccountContactEmailTemplate` | `core/workflows/whm/contact_email.py` |
| `whm-account-primary-domain` | `WHMAccountPrimaryDomainTemplate` | `core/workflows/whm/primary_domain.py` |
| `whm-firewall-batch-change` | `WHMFirewallBatchTemplate` | `core/workflows/whm/firewall.py` |
| `proxmox-vm-nic-connectivity` | `ProxmoxVMNicConnectivityTemplate` | `core/workflows/proxmox/nic_connectivity.py` |
| `proxmox-vm-cloudinit-password-reset` | `ProxmoxVMCloudinitPasswordResetTemplate` | `core/workflows/proxmox/cloudinit_password_reset.py` |
| `proxmox-pool-membership-move` | `ProxmoxPoolMembershipMoveTemplate` | `core/workflows/proxmox/pool_membership_move.py` |

## Current reference implementation

- Shared contract: `apps/api/src/noa_api/core/workflows/types.py`
- Registry: `apps/api/src/noa_api/core/workflows/registry.py`
- WHM templates: `apps/api/src/noa_api/core/workflows/whm/` (package with per-family modules)
- Proxmox templates: `apps/api/src/noa_api/core/workflows/proxmox/` (package with per-family modules)
- Shared approval helpers: `apps/api/src/noa_api/core/workflows/approval.py`
- Preflight validation: `apps/api/src/noa_api/core/workflows/preflight_validation.py`
- Web entry points:
  - Approval card: `apps/web/components/assistant/request-approval-tool-ui.tsx`
  - Workflow todo card: `apps/web/components/assistant/workflow-todo-tool-ui.tsx`
  - Workflow receipt card: `apps/web/components/assistant/workflow-receipt-tool-ui.tsx`
  - Receipt renderer: `apps/web/components/assistant/workflow-receipt-renderer.tsx`
  - Detail sections: `apps/web/components/assistant/detail-sections.tsx`
