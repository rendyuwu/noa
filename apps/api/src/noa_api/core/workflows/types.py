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

WorkflowApprovalPresentationBlockKind = Literal[
    "paragraph",
    "bullet_list",
    "key_value_list",
    "table",
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
    details: list[dict[str, str]] | None = None
    next_step: str | None = None
    assistant_hint: str | None = None
    approval_presentation: WorkflowApprovalPresentation | None = None


@dataclass(frozen=True, slots=True)
class WorkflowApprovalPresentationBlock:
    kind: WorkflowApprovalPresentationBlockKind
    text: str | None = None
    items: list[str] | None = None
    evidence_items: list[WorkflowEvidenceItem] | None = None
    table_headers: list[str] | None = None
    table_rows: list[list[str]] | None = None


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
class WorkflowApprovalPresentation:
    blocks: list[WorkflowApprovalPresentationBlock]


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

    for message in _messages_for_recent_preflight(working_messages):
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for raw_part in parts:
            part = _coerce_part_record(raw_part)
            if part is None:
                continue

            part_type = part.get("type")
            tool_name = part.get("toolName")
            if not isinstance(tool_name, str) or "_preflight_" not in tool_name:
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
    payload: dict[str, object] = {
        "title": template.title,
        "outcome": template.outcome,
        "summary": template.summary,
        "evidenceSummary": list(template.evidence_summary),
        "nextStep": template.next_step,
        "assistantHint": template.assistant_hint,
    }
    if template.details is not None:
        payload["details"] = [
            {"label": item["label"], "value": item["value"]}
            for item in template.details
            if item.get("label", "").strip() and item.get("value", "").strip()
        ]
    return payload


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
    sections: list[str] = [template.title]
    if template.approval_presentation is None:
        sections.append(template.summary)
    if template.approval_presentation is not None:
        approval_markdown = render_workflow_approval_markdown(
            template.approval_presentation
        )
        if approval_markdown.strip():
            sections.append(approval_markdown)
    if template.evidence_summary:
        sections.append(
            "\n".join(f"- {item}" for item in template.evidence_summary if item.strip())
        )
    if template.next_step is not None and template.next_step.strip():
        sections.append(f"Next safe step: {template.next_step.strip()}")
    return "\n\n".join(section for section in sections if section.strip())


def render_workflow_approval_markdown(
    presentation: WorkflowApprovalPresentation,
) -> str:
    sections = [
        section
        for block in presentation.blocks
        if (section := _render_workflow_approval_block(block)) is not None
    ]
    return "\n\n".join(sections)


def _render_workflow_approval_block(
    block: WorkflowApprovalPresentationBlock,
) -> str | None:
    if block.kind == "paragraph":
        return normalized_text(block.text)

    if block.kind == "bullet_list":
        items = [f"- {item}" for item in normalized_string_list(block.items)]
        return "\n".join(items) or None

    if block.kind == "key_value_list":
        if not isinstance(block.evidence_items, list):
            return None
        items = [
            f"- **{row.label}:** {row.value}"
            for row in block.evidence_items
            if isinstance(row, WorkflowEvidenceItem)
            and row.label.strip()
            and row.value.strip()
        ]
        return "\n".join(items) or None

    if block.kind == "table":
        headers = [
            _escape_markdown_table_cell(header)
            for header in normalized_string_list(block.table_headers)
        ]
        if not headers or not isinstance(block.table_rows, list):
            return None

        body_rows: list[str] = []
        for row in block.table_rows:
            if not isinstance(row, list):
                continue
            cells = [
                _escape_markdown_table_cell(normalized_text(cell) or "") for cell in row
            ]
            if not any(cell for cell in cells):
                continue
            body_rows.append(_render_markdown_table_row(cells))

        if not body_rows:
            return None

        separator = _render_markdown_table_row(["---"] * len(headers))
        return "\n".join([_render_markdown_table_row(headers), separator, *body_rows])

    return None


def _render_markdown_table_row(cells: list[str]) -> str:
    return f"| {' | '.join(cells)} |"


def _escape_markdown_table_cell(value: str) -> str:
    return (
        value.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("|", "\\|")
        .replace("\n", "<br>")
    )


def _coerce_part_record(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _messages_since_last_user(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    last_user_index = -1
    for index, message in enumerate(working_messages):
        if message.get("role") == "user":
            last_user_index = index
    return working_messages[last_user_index + 1 :]


def _messages_for_recent_preflight(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    last_user_index = _last_user_index(working_messages)
    if last_user_index < 0:
        return working_messages

    current_window = working_messages[last_user_index + 1 :]
    prior_messages = messages_before_latest_user_if_reason_follow_up(working_messages)
    if prior_messages is None:
        return current_window

    previous_user_index = _last_user_index(prior_messages)
    return working_messages[previous_user_index + 1 :]


def assistant_is_requesting_reason(text: str) -> bool:
    lowered = text.lower()
    return "reason" in lowered and any(
        phrase in lowered
        for phrase in (
            "could you provide",
            "please provide",
            "need a brief human-readable reason",
            "need a human-readable reason",
            "what reason",
        )
    )


def messages_before_latest_user_if_reason_follow_up(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]] | None:
    last_user_index = _last_user_index(working_messages)
    if last_user_index < 0:
        return None
    if not _assistant_requested_reason_before_user(
        working_messages=working_messages,
        user_index=last_user_index,
    ):
        return None
    return working_messages[:last_user_index]


def _last_user_index(working_messages: list[dict[str, object]]) -> int:
    for index in range(len(working_messages) - 1, -1, -1):
        if working_messages[index].get("role") == "user":
            return index
    return -1


def _assistant_requested_reason_before_user(
    *,
    working_messages: list[dict[str, object]],
    user_index: int,
) -> bool:
    for index in range(user_index - 1, -1, -1):
        message = working_messages[index]
        role = message.get("role")
        if role == "assistant":
            if _message_requests_reason(message):
                return True
            continue
        if role == "user":
            return False
    return False


def _message_requests_reason(message: dict[str, object]) -> bool:
    parts = message.get("parts")
    if not isinstance(parts, list):
        return False

    for raw_part in parts:
        part = _coerce_part_record(raw_part)
        if part is None or part.get("type") != "text":
            continue
        text = normalized_text(part.get("text"))
        if text is not None and assistant_is_requesting_reason(text):
            return True
    return False
