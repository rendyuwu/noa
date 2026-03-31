from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.redaction import is_sensitive_key
from noa_api.core.tools.registry import get_tool_definition
from noa_api.core.workflows.types import (
    WorkflowEvidenceTemplate,
    WorkflowInference,
    WorkflowReplyTemplate,
    WorkflowTemplate,
    WorkflowTemplateContext,
    collect_recent_preflight_evidence,
    collect_recent_preflight_results,
    workflow_evidence_template_payload,
    workflow_reply_template_payload,
)
from noa_api.core.workflows.proxmox import (
    WORKFLOW_TEMPLATES as PROXMOX_WORKFLOW_TEMPLATES,
)
from noa_api.core.workflows.whm import WORKFLOW_TEMPLATES as WHM_WORKFLOW_TEMPLATES
from noa_api.storage.postgres.workflow_todos import (
    SQLWorkflowTodoRepository,
    WorkflowTodoItem,
    WorkflowTodoService,
)

_WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {}

__all__ = [
    "build_approval_context",
    "build_workflow_reply_template",
    "build_workflow_evidence_template",
    "build_workflow_todos",
    "collect_recent_preflight_evidence",
    "collect_recent_preflight_results",
    "describe_workflow_activity",
    "fetch_postflight_result",
    "get_workflow_family",
    "get_workflow_template",
    "infer_waiting_on_user_workflow_from_messages",
    "list_registered_workflow_families",
    "persist_workflow_todos",
    "register_workflow_template",
    "require_matching_preflight",
]


def register_workflow_template(*, family: str, template: WorkflowTemplate) -> None:
    if family in _WORKFLOW_TEMPLATES:
        raise ValueError(f"Workflow template already registered for family '{family}'")
    _WORKFLOW_TEMPLATES[family] = template


def list_registered_workflow_families() -> tuple[str, ...]:
    return tuple(sorted(_WORKFLOW_TEMPLATES))


def get_workflow_family(
    tool_name: str, *, workflow_family: str | None = None
) -> str | None:
    if workflow_family is not None:
        return workflow_family
    tool = get_tool_definition(tool_name)
    if tool is None:
        return None
    return tool.workflow_family


def get_workflow_template(
    tool_name: str, *, workflow_family: str | None = None
) -> WorkflowTemplate | None:
    family = get_workflow_family(tool_name, workflow_family=workflow_family)
    if family is None:
        return None
    return _WORKFLOW_TEMPLATES.get(family)


def build_workflow_todos(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
    phase: str,
    preflight_evidence: list[dict[str, object]],
    result: dict[str, object] | None = None,
    postflight_result: dict[str, object] | None = None,
    error_code: str | None = None,
) -> list[WorkflowTodoItem] | None:
    template = get_workflow_template(tool_name, workflow_family=workflow_family)
    if template is None:
        return None

    context = WorkflowTemplateContext(
        tool_name=tool_name,
        args=args,
        phase=phase,  # type: ignore[arg-type]
        preflight_evidence=preflight_evidence,
        result=result,
        postflight_result=postflight_result,
        error_code=error_code,
    )
    return template.build_todos(context)


def build_workflow_reply_template(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
    phase: str,
    preflight_evidence: list[dict[str, object]],
    result: dict[str, object] | None = None,
    postflight_result: dict[str, object] | None = None,
    error_code: str | None = None,
) -> WorkflowReplyTemplate | None:
    template = get_workflow_template(tool_name, workflow_family=workflow_family)
    if template is None:
        return None

    context = WorkflowTemplateContext(
        tool_name=tool_name,
        args=args,
        phase=phase,  # type: ignore[arg-type]
        preflight_evidence=preflight_evidence,
        result=result,
        postflight_result=postflight_result,
        error_code=error_code,
    )
    return template.build_reply_template(context)


def build_workflow_evidence_template(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
    phase: str,
    preflight_evidence: list[dict[str, object]],
    result: dict[str, object] | None = None,
    postflight_result: dict[str, object] | None = None,
    error_code: str | None = None,
) -> WorkflowEvidenceTemplate | None:
    template = get_workflow_template(tool_name, workflow_family=workflow_family)
    if template is None:
        return None

    context = WorkflowTemplateContext(
        tool_name=tool_name,
        args=args,
        phase=phase,  # type: ignore[arg-type]
        preflight_evidence=preflight_evidence,
        result=result,
        postflight_result=postflight_result,
        error_code=error_code,
    )
    return template.build_evidence_template(context)


async def persist_workflow_todos(
    *,
    session: AsyncSession | None,
    thread_id: UUID,
    todos: list[WorkflowTodoItem] | None,
) -> None:
    if session is None or todos is None:
        return
    workflow_todo_service = WorkflowTodoService(
        repository=SQLWorkflowTodoRepository(session)
    )
    await workflow_todo_service.replace_workflow(thread_id=thread_id, todos=todos)


async def fetch_postflight_result(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
    session: AsyncSession | None,
) -> dict[str, object] | None:
    if session is None:
        return None
    template = get_workflow_template(tool_name, workflow_family=workflow_family)
    if template is None:
        return None
    return await template.fetch_postflight_result(
        tool_name=tool_name,
        args=args,
        session=session,
    )


def require_matching_preflight(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None = None,
):
    template = get_workflow_template(tool_name, workflow_family=workflow_family)
    if template is None:
        return None
    return template.require_preflight(
        tool_name=tool_name,
        args=args,
        working_messages=working_messages,
        requested_server_id=requested_server_id,
    )


def describe_workflow_activity(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
) -> str:
    template = get_workflow_template(tool_name, workflow_family=workflow_family)
    if template is not None:
        activity = template.describe_activity(tool_name=tool_name, args=args)
        if isinstance(activity, str) and activity.strip():
            return activity
    return _humanize_tool_name(tool_name)


def build_approval_context(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
) -> dict[str, object]:
    preflight_evidence = collect_recent_preflight_evidence(working_messages)
    preflight_results = collect_recent_preflight_results(working_messages)
    template = get_workflow_template(tool_name, workflow_family=workflow_family)
    before_state: list[dict[str, str]] = []
    evidence_template = build_workflow_evidence_template(
        tool_name=tool_name,
        workflow_family=workflow_family,
        args=args,
        phase="waiting_on_approval",
        preflight_evidence=preflight_evidence,
    )
    evidence_payload = (
        workflow_evidence_template_payload(evidence_template)
        if evidence_template is not None
        else None
    )
    if evidence_payload is not None:
        evidence_sections = evidence_payload.get("evidenceSections")
        if isinstance(evidence_sections, list):
            for section in evidence_sections:
                if not isinstance(section, dict):
                    continue
                if section.get("key") != "before_state":
                    continue
                items = section.get("items")
                if not isinstance(items, list):
                    continue
                before_state = [item for item in items if isinstance(item, dict)]
                break
    elif template is not None:
        built_before_state = template.build_before_state(
            tool_name=tool_name,
            args=args,
            preflight_results=preflight_results,
        )
        if isinstance(built_before_state, list):
            before_state = built_before_state

    reply_template = build_workflow_reply_template(
        tool_name=tool_name,
        workflow_family=workflow_family,
        args=args,
        phase="waiting_on_approval",
        preflight_evidence=preflight_evidence,
    )

    context = {
        "activity": describe_workflow_activity(
            tool_name=tool_name,
            workflow_family=workflow_family,
            args=args,
        ),
        "argumentSummary": _summarize_arguments(args),
        "beforeState": before_state,
        "preflightResults": preflight_results,
    }
    if reply_template is not None:
        context["replyTemplate"] = workflow_reply_template_payload(reply_template)
    if evidence_payload is not None:
        evidence_sections = evidence_payload.get("evidenceSections")
        if isinstance(evidence_sections, list) and evidence_sections:
            context["evidenceSections"] = evidence_sections
    return context


def infer_waiting_on_user_workflow_from_messages(
    *, assistant_text: str, working_messages: list[dict[str, object]]
) -> WorkflowInference | None:
    for template in _WORKFLOW_TEMPLATES.values():
        inferred = template.infer_waiting_on_user_workflow(
            assistant_text=assistant_text,
            working_messages=working_messages,
        )
        if inferred is not None:
            return inferred
    return None


def _humanize_tool_name(tool_name: str) -> str:
    return (
        " ".join(part.capitalize() for part in tool_name.split("_") if part.strip())
        or "Tool"
    )


def _format_argument_value(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if value is None:
        return "none"
    if isinstance(value, list):
        return ", ".join(_format_argument_value(item) for item in value[:5])
    return str(value)


def _summarize_arguments(args: dict[str, object]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key, value in args.items():
        if is_sensitive_key(key):
            continue
        items.append(
            {
                "label": key.replace("_", " ").capitalize(),
                "value": _format_argument_value(value),
            }
        )
    return items


for _family, _template in WHM_WORKFLOW_TEMPLATES.items():
    register_workflow_template(family=_family, template=_template)

for _family, _template in PROXMOX_WORKFLOW_TEMPLATES.items():
    register_workflow_template(family=_family, template=_template)
