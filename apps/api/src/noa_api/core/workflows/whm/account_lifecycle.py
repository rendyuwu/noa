from __future__ import annotations

from typing import Any, cast

from noa_api.core.workflows.approval import (
    approval_detail_rows as _approval_detail_rows,
    approval_presentation_from_reply_data as _approval_presentation_from_reply_data,
    approval_reason_detail as _approval_reason_detail,
)
from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowEvidenceSection,
    WorkflowEvidenceTemplate,
    WorkflowInference,
    WorkflowReplyTemplate,
    WorkflowTemplateContext,
    normalized_text,
)
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem

from noa_api.core.workflows.whm.base import _WHMAccountTemplate
from noa_api.core.workflows.whm.common import (
    _account_state,
    _account_subject,
    _action_label,
    _clean_items,
    _format_argument_value,
    _result_error_code,
    _result_message,
    _result_ok,
    _result_status,
)
from noa_api.core.workflows.whm.matching import (
    _account_preflight_candidates,
    _matching_account_preflight,
    _postflight_account,
)
from noa_api.core.workflows.whm.inference import (
    _infer_whm_account_lifecycle_tool_name,
    _latest_user_text,
    _select_account_preflight_candidate,
)
from noa_api.core.workflows.whm.todo_helpers import (
    _account_after_state_items,
    _account_before_state_items,
    _postflight_step_content,
    _preflight_step_content,
    _reason_step_content,
)


def _build_account_lifecycle_reply_template(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    return _build_account_lifecycle_reply_template_impl(context)


class WHMAccountLifecycleTemplate(_WHMAccountTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        subject = _account_subject(context.args)
        action_label = _action_label(context.tool_name)
        before_account = _matching_account_preflight(
            preflight_evidence=context.preflight_evidence,
            args=context.args,
        )
        after_account = _postflight_account(context.postflight_result)
        reason = normalized_text(context.args.get("reason"))

        reason_step_status = "completed" if reason is not None else "pending"
        approval_step_status = "pending"
        execute_step_status = "pending"
        postflight_step_status = "pending"

        if context.phase == "waiting_on_user":
            reason_step_status = "waiting_on_user"
        elif context.phase == "waiting_on_approval":
            approval_step_status = "waiting_on_approval"
        elif context.phase == "executing":
            approval_step_status = "completed"
            execute_step_status = "in_progress"
        elif context.phase == "completed":
            approval_step_status = "completed"
            execute_step_status = "completed"
            postflight_step_status = "completed"
        elif context.phase == "denied":
            approval_step_status = "cancelled"
            execute_step_status = "cancelled"
            postflight_step_status = "cancelled"
        elif context.phase == "failed":
            approval_step_status = "completed"
            execute_step_status = "cancelled"
            postflight_step_status = "cancelled"

        if reason is None and context.phase in {"completed", "denied", "failed"}:
            reason_step_status = "cancelled"

        return [
            {
                "content": _preflight_step_content(
                    subject=subject, before_account=before_account
                ),
                "status": "completed" if before_account is not None else "in_progress",
                "priority": "high",
            },
            {
                "content": _reason_step_content(
                    action_label=action_label, reason=reason
                ),
                "status": cast(Any, reason_step_status),
                "priority": "high",
            },
            {
                "content": f"Request approval to {action_label} {subject}.",
                "status": cast(Any, approval_step_status),
                "priority": "high",
            },
            {
                "content": f"Execute {action_label} for {subject}.",
                "status": cast(Any, execute_step_status),
                "priority": "high",
            },
            {
                "content": _postflight_step_content(
                    tool_name=context.tool_name,
                    subject=subject,
                    after_account=after_account,
                    postflight_result=context.postflight_result,
                ),
                "status": cast(Any, postflight_step_status),
                "priority": "high",
            },
        ]

    def build_reply_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowReplyTemplate | None:
        return _build_account_lifecycle_reply_template(context)

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        return _build_account_lifecycle_evidence_template(context)

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        _ = args
        username = _format_argument_value(args.get("username", "unknown"))
        if tool_name == "whm_unsuspend_account":
            return f"Unsuspend account '{username}'"
        return f"Suspend account '{username}'"

    def infer_waiting_on_user_workflow(
        self,
        *,
        assistant_text: str,
        working_messages: list[dict[str, object]],
    ) -> WorkflowInference | None:
        _ = assistant_text
        last_user_text = _latest_user_text(working_messages)
        if last_user_text is None:
            return None

        tool_name = _infer_whm_account_lifecycle_tool_name(last_user_text)
        if tool_name is None:
            return None

        account_candidates = _account_preflight_candidates(working_messages)
        if not account_candidates:
            return None

        match = _select_account_preflight_candidate(
            account_candidates=account_candidates,
            user_text=last_user_text,
        )
        if match is None:
            return None

        args = cast(dict[str, object], match.get("args"))
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        if server_ref is None or username is None:
            return None

        return WorkflowInference(
            tool_name=tool_name,
            args={
                "server_ref": server_ref,
                "username": username,
            },
        )


def _build_account_lifecycle_reply_template_impl(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    subject = _account_subject(context.args)
    action_label = _action_label(context.tool_name)
    action_title = action_label.capitalize()
    desired_state = (
        "active" if context.tool_name == "whm_unsuspend_account" else "suspended"
    )
    before_account = _matching_account_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_account = _postflight_account(context.postflight_result)
    reason = normalized_text(context.args.get("reason"))
    before_state = _account_state(before_account) or "unknown"
    after_state = _account_state(after_account) or before_state
    result_ok = _result_ok(context.result)
    result_status = _result_status(context.result)
    result_message = _result_message(context.result)

    if context.phase == "waiting_on_approval":
        success_criteria = f"{subject} ends in {desired_state} state."
        details = _approval_detail_rows(
            ("Action", f"{action_title} {subject}."),
            ("Reason", _approval_reason_detail(reason)),
            ("Success criteria", success_criteria),
        )
        evidence = [
            f"Preflight found {subject} in {before_state} state.",
        ]
        return WorkflowReplyTemplate(
            title=f"{action_title} approval requested",
            outcome="info",
            summary=f"This will {action_label} {subject} after approval.",
            evidence_summary=[],
            approval_presentation=_approval_presentation_from_reply_data(
                paragraph=f"WHM account lifecycle request for {subject}.",
                details=details,
                evidence_summary=evidence,
            ),
            details=details,
            next_step=(
                f"Approve the request to {action_label} the account, or deny it to leave the current state unchanged."
            ),
        )

    if context.phase == "denied":
        evidence = [f"Last confirmed account state: {before_state}."]
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title=f"{action_title} denied",
            outcome="denied",
            summary=f"The request to {action_label} {subject} was denied. No change was applied.",
            evidence_summary=evidence,
            next_step=(
                f"Submit a new approval request if you still need to {action_label} this account."
            ),
        )

    if context.phase == "failed":
        evidence = [f"Last confirmed account state: {before_state}."]
        if context.error_code is not None:
            evidence.append(f"Error code: {context.error_code}.")
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title=f"{action_title} failed",
            outcome="failed",
            summary=f"NOA could not complete the {action_label} request for {subject}.",
            evidence_summary=evidence,
            next_step="Run account preflight again to confirm the current state before retrying.",
        )

    if context.phase != "completed":
        return None

    evidence = [f"Before state: {before_state}."]
    if result_message is not None:
        evidence.append(f"Tool result: {result_message}.")
    if after_account is not None:
        evidence.append(f"Postflight state: {after_state}.")
    if reason is not None:
        evidence.append(f"Recorded reason: {reason}.")

    if result_ok is False:
        error_code = (
            _result_error_code(context.result) or context.error_code or "unknown"
        )
        evidence.append(f"Error code: {error_code}.")
        return WorkflowReplyTemplate(
            title=f"{action_title} failed",
            outcome="failed",
            summary=f"NOA did not confirm the {action_label} for {subject}.",
            evidence_summary=evidence,
            next_step="Review the error and rerun account preflight before retrying.",
        )

    if result_status == "no-op":
        return WorkflowReplyTemplate(
            title=f"{action_title} no-op",
            outcome="no_op",
            summary=f"No change was needed for {subject}.",
            evidence_summary=evidence,
            next_step="No further action is required unless you expected a different account state.",
        )

    if after_account is None or after_state != desired_state:
        if after_account is None:
            evidence.append(
                "Postflight verification did not confirm the final account state."
            )
        else:
            evidence.append(f"Expected postflight state: {desired_state}.")
        return WorkflowReplyTemplate(
            title=f"{action_title} partially verified",
            outcome="partial",
            summary=f"The {action_label} call finished for {subject}, but NOA could not fully verify the final state.",
            evidence_summary=evidence,
            next_step="Run account preflight again before making another change.",
        )

    return WorkflowReplyTemplate(
        title=f"{action_title} completed",
        outcome="changed",
        summary=f"{subject} moved from {before_state} to {after_state}.",
        evidence_summary=evidence,
    )


def _build_account_lifecycle_evidence_template(
    context: WorkflowTemplateContext,
) -> WorkflowEvidenceTemplate | None:
    subject = _account_subject(context.args)
    action_label = _action_label(context.tool_name)
    desired_state = (
        "active" if context.tool_name == "whm_unsuspend_account" else "suspended"
    )
    before_account = _matching_account_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_account = _postflight_account(context.postflight_result)
    reason = normalized_text(context.args.get("reason"))
    before_state = _account_state(before_account) or "unknown"
    after_state = _account_state(after_account) or before_state
    result_status = _result_status(context.result)
    result_ok = _result_ok(context.result)

    sections: list[WorkflowEvidenceSection] = []
    sections.append(
        WorkflowEvidenceSection(
            key="before_state",
            title="Before state",
            items=_account_before_state_items(before_account),
        )
    )
    sections.append(
        WorkflowEvidenceSection(
            key="requested_change",
            title="Requested change",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(label="Action", value=action_label),
                    WorkflowEvidenceItem(label="Subject", value=subject),
                    WorkflowEvidenceItem(label="Expected state", value=desired_state),
                    WorkflowEvidenceItem(
                        label="Reason", value=reason or "none provided"
                    ),
                ]
            ),
        )
    )

    if context.phase == "denied":
        sections.append(
            WorkflowEvidenceSection(
                key="failure",
                title="Failure",
                items=[
                    WorkflowEvidenceItem(label="Status", value="denied"),
                    WorkflowEvidenceItem(
                        label="Result",
                        value="Approval denied; no change executed.",
                    ),
                ],
            )
        )
        return WorkflowEvidenceTemplate(sections=sections)

    if context.phase == "failed" or result_ok is False:
        sections.append(
            WorkflowEvidenceSection(
                key="failure",
                title="Failure",
                items=_clean_items(
                    [
                        WorkflowEvidenceItem(label="Status", value="failed"),
                        WorkflowEvidenceItem(
                            label="Error code",
                            value=(
                                _result_error_code(context.result)
                                or context.error_code
                                or "unknown"
                            ),
                        ),
                    ]
                ),
            )
        )
        return WorkflowEvidenceTemplate(sections=sections)

    sections.append(
        WorkflowEvidenceSection(
            key="after_state",
            title="After state",
            items=_account_after_state_items(
                after_account, expected_state=desired_state
            ),
        )
    )
    sections.append(
        WorkflowEvidenceSection(
            key="verification",
            title="Verification",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(
                        label="Result status", value=result_status or "unknown"
                    ),
                    WorkflowEvidenceItem(label="Before", value=before_state),
                    WorkflowEvidenceItem(label="After", value=after_state),
                    WorkflowEvidenceItem(
                        label="Verified",
                        value=(
                            "yes"
                            if after_account is not None
                            and after_state == desired_state
                            else "partial"
                            if after_account is not None
                            else "no"
                        ),
                    ),
                ]
            ),
        )
    )
    return WorkflowEvidenceTemplate(sections=sections)
