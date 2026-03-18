from __future__ import annotations

from typing import Any, cast

import pytest

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.registry import (
    build_approval_context,
    build_workflow_evidence_template,
    build_workflow_reply_template,
    build_workflow_todos,
    describe_workflow_activity,
    fetch_postflight_result,
    infer_waiting_on_user_workflow_from_messages,
    list_registered_workflow_families,
    register_workflow_template,
    require_matching_preflight,
)
from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowEvidenceSection,
    WorkflowEvidenceTemplate,
    WorkflowInference,
    WorkflowReplyTemplate,
    WorkflowTemplate,
    WorkflowTemplateContext,
)
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem


class _CustomWorkflowTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        return [
            {
                "content": f"Registered step for {context.tool_name}.",
                "status": "waiting_on_approval",
                "priority": "high",
            }
        ]

    def build_reply_template(
        self, context: WorkflowTemplateContext
    ) -> WorkflowReplyTemplate | None:
        return WorkflowReplyTemplate(
            title=f"Reply for {context.tool_name}",
            outcome="info",
            summary=f"Workflow phase is {context.phase}.",
            evidence_summary=["Custom evidence"],
            next_step="Wait for user approval.",
        )

    def build_evidence_template(
        self, context: WorkflowTemplateContext
    ) -> WorkflowEvidenceTemplate | None:
        return WorkflowEvidenceTemplate(
            sections=[
                WorkflowEvidenceSection(
                    key="before_state",
                    title="Before state",
                    items=[
                        WorkflowEvidenceItem(label="Before", value="ready"),
                    ],
                ),
                WorkflowEvidenceSection(
                    key="requested_change",
                    title="Requested change",
                    items=[
                        WorkflowEvidenceItem(
                            label="Tool",
                            value=context.tool_name,
                        ),
                    ],
                ),
            ]
        )

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        _ = tool_name, args
        return "Run custom workflow"

    def build_before_state(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        preflight_results: list[dict[str, object]],
    ) -> list[dict[str, str]] | None:
        _ = tool_name, args, preflight_results
        return [{"label": "Before", "value": "ready"}]

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name, args, working_messages, requested_server_id
        return SanitizedToolError(
            error="Custom preflight missing",
            error_code="custom_preflight_required",
        )

    async def fetch_postflight_result(
        self, *, tool_name: str, args: dict[str, object], session
    ):
        _ = tool_name, args, session
        return {"ok": True, "source": "custom-template"}

    def infer_waiting_on_user_workflow(
        self,
        *,
        assistant_text: str,
        working_messages: list[dict[str, object]],
    ) -> WorkflowInference | None:
        _ = working_messages
        if "reason" not in assistant_text.lower():
            return None
        return WorkflowInference(tool_name="custom_change", args={"resource": "alpha"})


@pytest.mark.asyncio
async def test_workflow_registry_supports_registered_templates() -> None:
    family = "test-custom-workflow-family"
    register_workflow_template(family=family, template=_CustomWorkflowTemplate())

    assert family in list_registered_workflow_families()

    todos = build_workflow_todos(
        tool_name="custom_change",
        workflow_family=family,
        args={"resource": "alpha"},
        phase="waiting_on_approval",
        preflight_evidence=[],
    )
    assert todos == [
        {
            "content": "Registered step for custom_change.",
            "status": "waiting_on_approval",
            "priority": "high",
        }
    ]

    approval_context = build_approval_context(
        tool_name="custom_change",
        workflow_family=family,
        args={"resource": "alpha"},
        working_messages=[],
    )
    assert approval_context["activity"] == "Run custom workflow"
    assert approval_context["beforeState"] == [{"label": "Before", "value": "ready"}]
    assert approval_context["evidenceSections"] == [
        {
            "key": "before_state",
            "title": "Before state",
            "items": [{"label": "Before", "value": "ready"}],
        },
        {
            "key": "requested_change",
            "title": "Requested change",
            "items": [{"label": "Tool", "value": "custom_change"}],
        },
    ]
    assert approval_context["argumentSummary"] == [
        {"label": "Resource", "value": "alpha"}
    ]
    assert approval_context["replyTemplate"] == {
        "title": "Reply for custom_change",
        "outcome": "info",
        "summary": "Workflow phase is waiting_on_approval.",
        "evidenceSummary": ["Custom evidence"],
        "nextStep": "Wait for user approval.",
        "assistantHint": None,
    }

    reply_template = build_workflow_reply_template(
        tool_name="custom_change",
        workflow_family=family,
        args={"resource": "alpha"},
        phase="completed",
        preflight_evidence=[],
        result={"ok": True},
    )
    assert reply_template == WorkflowReplyTemplate(
        title="Reply for custom_change",
        outcome="info",
        summary="Workflow phase is completed.",
        evidence_summary=["Custom evidence"],
        next_step="Wait for user approval.",
    )

    evidence_template = build_workflow_evidence_template(
        tool_name="custom_change",
        workflow_family=family,
        args={"resource": "alpha"},
        phase="completed",
        preflight_evidence=[],
        result={"ok": True},
    )
    assert evidence_template is not None
    assert evidence_template.sections[0].key == "before_state"

    preflight_error = require_matching_preflight(
        tool_name="custom_change",
        workflow_family=family,
        args={"resource": "alpha"},
        working_messages=[],
    )
    assert preflight_error is not None
    assert preflight_error.error_code == "custom_preflight_required"

    postflight_result = await fetch_postflight_result(
        tool_name="custom_change",
        workflow_family=family,
        args={"resource": "alpha"},
        session=cast(Any, object()),
    )
    assert postflight_result == {"ok": True, "source": "custom-template"}

    inferred = infer_waiting_on_user_workflow_from_messages(
        assistant_text="I still need a reason before I can continue.",
        working_messages=[],
    )
    assert inferred == WorkflowInference(
        tool_name="custom_change",
        args={"resource": "alpha"},
    )

    assert (
        describe_workflow_activity(
            tool_name="custom_change",
            workflow_family=family,
            args={"resource": "alpha"},
        )
        == "Run custom workflow"
    )
