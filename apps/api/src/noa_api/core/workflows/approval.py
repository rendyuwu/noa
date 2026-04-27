"""Shared approval-presentation helpers used by workflow template families."""

from __future__ import annotations

from noa_api.core.workflows.types import (
    WorkflowApprovalPresentation,
    WorkflowApprovalPresentationBlock,
    WorkflowEvidenceItem,
    normalized_text,
)


def _clean_items(items: list[WorkflowEvidenceItem]) -> list[WorkflowEvidenceItem]:
    return [item for item in items if item.label.strip() and item.value.strip()]


def approval_detail_rows(*rows: tuple[str, str | None]) -> list[dict[str, str]]:
    return [
        {"label": label, "value": value}
        for label, value in rows
        if value is not None and label.strip() and value.strip()
    ]


def approval_key_value_block_from_details(
    details: list[dict[str, str]] | None,
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        kind="key_value_list",
        evidence_items=_clean_items(
            [
                WorkflowEvidenceItem(label=label, value=value)
                for item in details or []
                for label in [normalized_text(item.get("label"))]
                for value in [normalized_text(item.get("value"))]
                if label is not None and value is not None
            ]
        ),
    )


def approval_reason_detail(reason: str | None) -> str:
    return reason or "none provided"


def approval_paragraph_block(text: str | None) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(kind="paragraph", text=text)


def approval_bullet_list_block(
    *items: str | None,
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        kind="bullet_list",
        items=[item for item in items if isinstance(item, str) and item.strip()],
    )


def approval_key_value_block(
    *rows: tuple[str, str | None],
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        kind="key_value_list",
        evidence_items=_clean_items(
            [
                WorkflowEvidenceItem(label=label, value=value)
                for label, value in rows
                if value is not None
            ]
        ),
    )


def approval_presentation(
    *blocks: WorkflowApprovalPresentationBlock,
) -> WorkflowApprovalPresentation:
    return WorkflowApprovalPresentation(
        blocks=[
            block
            for block in blocks
            if isinstance(block, WorkflowApprovalPresentationBlock)
        ]
    )


def approval_presentation_from_reply_data(
    *,
    paragraph: str | None,
    details: list[dict[str, str]] | None,
    evidence_summary: list[str],
    extra_blocks: list[WorkflowApprovalPresentationBlock] | None = None,
) -> WorkflowApprovalPresentation:
    return approval_presentation(
        approval_paragraph_block(paragraph),
        *(extra_blocks or []),
        approval_bullet_list_block(*evidence_summary),
        approval_key_value_block_from_details(details),
    )
