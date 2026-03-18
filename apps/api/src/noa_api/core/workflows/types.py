from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.json_safety import json_safe
from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem

WorkflowTemplatePhase = Literal[
    "waiting_on_user",
    "waiting_on_approval",
    "executing",
    "completed",
    "denied",
    "failed",
]

WorkflowReplyOutcome = Literal[
    "info",
    "changed",
    "no_op",
    "partial",
    "failed",
    "denied",
]


@dataclass(frozen=True, slots=True)
class WorkflowTemplateContext:
    tool_name: str
    args: dict[str, object]
    phase: WorkflowTemplatePhase
    preflight_evidence: list[dict[str, object]]
    result: dict[str, object] | None = None
    postflight_result: dict[str, object] | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowInference:
    tool_name: str
    args: dict[str, object]


@dataclass(frozen=True, slots=True)
class WorkflowReplyTemplate:
    title: str
    outcome: WorkflowReplyOutcome
    summary: str
    evidence_summary: list[str]
    next_step: str | None = None
    assistant_hint: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowEvidenceItem:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class WorkflowEvidenceSection:
    key: str
    title: str
    items: list[WorkflowEvidenceItem]


@dataclass(frozen=True, slots=True)
class WorkflowEvidenceTemplate:
    sections: list[WorkflowEvidenceSection]


class WorkflowTemplate:
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        raise NotImplementedError

    def build_reply_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowReplyTemplate | None:
        return None

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        return None

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        return None

    def build_before_state(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        preflight_results: list[dict[str, object]],
    ) -> list[dict[str, str]] | None:
        return None

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        return None

    async def fetch_postflight_result(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        session: AsyncSession,
    ) -> dict[str, object] | None:
        return None

    def infer_waiting_on_user_workflow(
        self,
        *,
        assistant_text: str,
        working_messages: list[dict[str, object]],
    ) -> WorkflowInference | None:
        return None


def collect_recent_preflight_evidence(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    tool_calls_by_id: dict[str, dict[str, object]] = {}
    evidence: list[dict[str, object]] = []

    for message in _messages_since_last_user(working_messages):
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for raw_part in parts:
            part = _coerce_part_record(raw_part)
            if part is None:
                continue

            part_type = part.get("type")
            tool_name = part.get("toolName")
            if not isinstance(tool_name, str) or not tool_name.startswith(
                "whm_preflight_"
            ):
                continue

            tool_call_id = part.get("toolCallId")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue

            if part_type == "tool-call":
                args = part.get("args")
                args_obj = args if isinstance(args, dict) else {}
                tool_calls_by_id[tool_call_id] = {
                    "toolName": tool_name,
                    "args": json_safe(args_obj),
                }
                continue

            if part_type != "tool-result" or part.get("isError") is True:
                continue

            result = part.get("result")
            if not isinstance(result, dict):
                continue

            call = tool_calls_by_id.get(tool_call_id, {})
            entry: dict[str, object] = {
                "toolName": tool_name,
                "result": json_safe(result),
            }
            call_args = call.get("args")
            if isinstance(call_args, dict):
                entry["args"] = call_args
            evidence.append(entry)

    return evidence


def collect_recent_preflight_results(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            "toolName": item["toolName"],
            "result": item["result"],
        }
        for item in collect_recent_preflight_evidence(working_messages)
    ]


def normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def normalized_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = normalized_text(item)
        if text is not None:
            normalized.append(text)
    return normalized


def workflow_reply_template_payload(
    template: WorkflowReplyTemplate,
) -> dict[str, object]:
    return {
        "title": template.title,
        "outcome": template.outcome,
        "summary": template.summary,
        "evidenceSummary": list(template.evidence_summary),
        "nextStep": template.next_step,
        "assistantHint": template.assistant_hint,
    }


def workflow_evidence_template_payload(
    template: WorkflowEvidenceTemplate,
) -> dict[str, object]:
    return {
        "evidenceSections": [
            {
                "key": section.key,
                "title": section.title,
                "items": [
                    {"label": item.label, "value": item.value}
                    for item in section.items
                    if item.label.strip() and item.value.strip()
                ],
            }
            for section in template.sections
            if section.key.strip() and section.title.strip()
        ]
    }


def render_workflow_reply_text(template: WorkflowReplyTemplate) -> str:
    sections: list[str] = [template.title, template.summary]
    if template.evidence_summary:
        sections.append(
            "\n".join(f"- {item}" for item in template.evidence_summary if item.strip())
        )
    if template.next_step is not None and template.next_step.strip():
        sections.append(f"Next safe step: {template.next_step.strip()}")
    return "\n\n".join(section for section in sections if section.strip())


def _coerce_part_record(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def _messages_since_last_user(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    last_user_index = -1
    for index, message in enumerate(working_messages):
        if message.get("role") == "user":
            last_user_index = index
    return working_messages[last_user_index + 1 :]
