# Workflow templates

NOA workflow UI is driven by canonical workflow todos stored in assistant thread state. To add a new operational tool family, register a workflow template on the API side and keep the web dock/detail UI unchanged.

## How registration works

1. Add a `workflow_family` value to each `ToolDefinition` in `apps/api/src/noa_api/core/tools/registry.py`.
2. Implement a template in `apps/api/src/noa_api/core/workflows/<family>.py` by subclassing `WorkflowTemplate` from `apps/api/src/noa_api/core/workflows/types.py`.
3. Register the template in `apps/api/src/noa_api/core/workflows/registry.py` with `register_workflow_template(...)`.

The registry is the orchestration boundary. Once a family is registered, the existing workflow dock, detail sheet, approval cards, and thread state transport continue to work without UI changes.

Family-specific operational reply behavior should also live here rather than in bespoke runner branches or tool-specific web UI logic.

## Template hooks

- `build_todos(...)`: required; returns canonical workflow steps for each lifecycle phase.
- `describe_activity(...)`: optional; customizes approval activity copy.
- `build_before_state(...)`: optional; maps preflight evidence into approval before-state rows.
- `build_reply_template(...)`: optional; returns structured proposal/completion/denial/no-op/partial/failure reply guidance for the family.
- `require_preflight(...)`: optional; blocks unsafe CHANGE requests until matching evidence exists.
- `fetch_postflight_result(...)`: optional; loads verification evidence after execution.
- `infer_waiting_on_user_workflow(...)`: optional; seeds a waiting workflow when the assistant asks the user for missing input without emitting a CHANGE tool call yet.

The reply template contract is structured data, not markdown-authored prose, so the same family-owned semantics can drive assistant replies now and richer receipts/detail views later.

## Family module shape

```python
from noa_api.core.workflows.types import (
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

## Current reference implementation

- Shared contract: `apps/api/src/noa_api/core/workflows/types.py`
- Registry: `apps/api/src/noa_api/core/workflows/registry.py`
- WHM templates: `apps/api/src/noa_api/core/workflows/whm.py`
