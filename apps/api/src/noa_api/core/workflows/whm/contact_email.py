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
    _account_email,
    _account_subject,
    _clean_items,
    _default_step_statuses,
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
    _extract_email,
    _latest_user_text,
    _select_account_preflight_candidate,
)
from noa_api.core.workflows.whm.todo_helpers import (
    _contact_email_postflight_step_content,
    _preflight_step_content,
    _reason_step_content,
)


def _build_contact_email_reply_template(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    return _build_contact_email_reply_template_impl(context)


class WHMAccountContactEmailTemplate(_WHMAccountTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        subject = _account_subject(context.args)
        before_account = _matching_account_preflight(
            preflight_evidence=context.preflight_evidence,
            args=context.args,
        )
        after_account = _postflight_account(context.postflight_result)
        reason = normalized_text(context.args.get("reason"))
        new_email = normalized_text(context.args.get("new_email"))

        statuses = _default_step_statuses(reason=reason, phase=context.phase)
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
                    action_label="changing the contact email",
                    reason=reason,
                    missing_reason_text=(
                        "Ask the user for a reason—an osTicket/reference number or a brief "
                        "description—before changing the contact email for the account."
                    ),
                ),
                "status": cast(Any, statuses["reason"]),
                "priority": "high",
            },
            {
                "content": f"Request approval to change the contact email for {subject} to '{new_email or 'the requested value'}'.",
                "status": cast(Any, statuses["approval"]),
                "priority": "high",
            },
            {
                "content": f"Execute the contact email change for {subject}.",
                "status": cast(Any, statuses["execute"]),
                "priority": "high",
            },
            {
                "content": _contact_email_postflight_step_content(
                    subject=subject,
                    requested_email=new_email,
                    after_account=after_account,
                    postflight_result=context.postflight_result,
                ),
                "status": cast(Any, statuses["postflight"]),
                "priority": "high",
            },
        ]

    def build_reply_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowReplyTemplate | None:
        return _build_contact_email_reply_template(context)

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        return _build_contact_email_evidence_template(context)

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        _ = tool_name
        return (
            f"Change contact email for '{args.get('username', 'unknown')}' "
            f"to '{args.get('new_email', 'unknown')}'"
        )

    def infer_waiting_on_user_workflow(
        self,
        *,
        assistant_text: str,
        working_messages: list[dict[str, object]],
    ) -> WorkflowInference | None:
        _ = assistant_text
        last_user_text = _latest_user_text(working_messages)
        if last_user_text is None or "email" not in last_user_text.lower():
            return None

        new_email = _extract_email(last_user_text)
        if new_email is None:
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
            tool_name="whm_change_contact_email",
            args={
                "server_ref": server_ref,
                "username": username,
                "new_email": new_email,
            },
        )


def _build_contact_email_reply_template_impl(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    subject = _account_subject(context.args)
    requested_email = (
        normalized_text(context.args.get("new_email")) or "the requested email"
    )
    before_account = _matching_account_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_account = _postflight_account(context.postflight_result)
    reason = normalized_text(context.args.get("reason"))
    before_email = _account_email(before_account) or "unknown"
    after_email = _account_email(after_account) or before_email
    result_ok = _result_ok(context.result)
    result_status = _result_status(context.result)
    result_message = _result_message(context.result)

    if context.phase == "waiting_on_approval":
        success_criteria = (
            f"The contact email changes from '{before_email}' to '{requested_email}'."
        )
        details = _approval_detail_rows(
            (
                "Action",
                f"Change the contact email for {subject} to '{requested_email}'.",
            ),
            ("Reason", _approval_reason_detail(reason)),
            ("Success criteria", success_criteria),
        )
        evidence = [
            f"Preflight found the current contact email as '{before_email}'.",
        ]
        return WorkflowReplyTemplate(
            title="Contact email approval requested",
            outcome="info",
            summary=f"This will change the contact email for {subject} to '{requested_email}' after approval.",
            evidence_summary=[],
            approval_presentation=_approval_presentation_from_reply_data(
                paragraph=f"WHM contact email change for {subject}.",
                details=details,
                evidence_summary=evidence,
            ),
            details=details,
            next_step="Approve the request to apply the email change, or deny it to keep the current address.",
        )

    if context.phase == "denied":
        evidence = [f"Last confirmed contact email: '{before_email}'."]
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title="Contact email change denied",
            outcome="denied",
            summary=f"The request to change the contact email for {subject} was denied. No change was applied.",
            evidence_summary=evidence,
            next_step="Submit a new approval request if you still need to change this email.",
        )

    if context.phase == "failed":
        evidence = [f"Last confirmed contact email: '{before_email}'."]
        if context.error_code is not None:
            evidence.append(f"Error code: {context.error_code}.")
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title="Contact email change failed",
            outcome="failed",
            summary=f"NOA could not complete the contact email change for {subject}.",
            evidence_summary=evidence,
            next_step="Run account preflight again to confirm the current email before retrying.",
        )

    if context.phase != "completed":
        return None

    evidence = [f"Before email: '{before_email}'."]
    if result_message is not None:
        evidence.append(f"Tool result: {result_message}.")
    if after_account is not None:
        evidence.append(f"Postflight email: '{after_email}'.")
    if reason is not None:
        evidence.append(f"Recorded reason: {reason}.")

    if result_ok is False:
        error_code = (
            _result_error_code(context.result) or context.error_code or "unknown"
        )
        evidence.append(f"Error code: {error_code}.")
        return WorkflowReplyTemplate(
            title="Contact email change failed",
            outcome="failed",
            summary=f"NOA did not confirm the contact email change for {subject}.",
            evidence_summary=evidence,
            next_step="Review the error and rerun account preflight before retrying.",
        )

    if result_status == "no-op":
        return WorkflowReplyTemplate(
            title="Contact email change no-op",
            outcome="no_op",
            summary=f"No contact email change was needed for {subject}.",
            evidence_summary=evidence,
            next_step="No further action is required unless you expected a different email address.",
        )

    if after_account is None or after_email.lower() != requested_email.lower():
        if after_account is None:
            evidence.append(
                "Postflight verification did not confirm the final contact email."
            )
        else:
            evidence.append(f"Expected final email: '{requested_email}'.")
        return WorkflowReplyTemplate(
            title="Contact email change partially verified",
            outcome="partial",
            summary=f"The contact email change finished for {subject}, but NOA could not fully verify the final email.",
            evidence_summary=evidence,
            next_step="Run account preflight again before making another change.",
        )

    return WorkflowReplyTemplate(
        title="Contact email change completed",
        outcome="changed",
        summary=(
            f"The contact email for {subject} moved from '{before_email}' to '{after_email}'."
        ),
        evidence_summary=evidence,
    )


def _build_contact_email_evidence_template(
    context: WorkflowTemplateContext,
) -> WorkflowEvidenceTemplate | None:
    subject = _account_subject(context.args)
    requested_email = normalized_text(context.args.get("new_email")) or "unknown"
    before_account = _matching_account_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_account = _postflight_account(context.postflight_result)
    reason = normalized_text(context.args.get("reason"))
    before_email = _account_email(before_account) or "unknown"
    after_email = _account_email(after_account) or before_email
    result_status = _result_status(context.result)
    result_ok = _result_ok(context.result)

    sections: list[WorkflowEvidenceSection] = [
        WorkflowEvidenceSection(
            key="before_state",
            title="Before state",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(label="Subject", value=subject),
                    WorkflowEvidenceItem(label="Contact email", value=before_email),
                ]
            ),
        ),
        WorkflowEvidenceSection(
            key="requested_change",
            title="Requested change",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(label="Action", value="change contact email"),
                    WorkflowEvidenceItem(
                        label="Requested email", value=requested_email
                    ),
                    WorkflowEvidenceItem(
                        label="Reason", value=reason or "none provided"
                    ),
                ]
            ),
        ),
    ]

    if context.phase == "denied":
        sections.append(
            WorkflowEvidenceSection(
                key="failure",
                title="Failure",
                items=[
                    WorkflowEvidenceItem(label="Status", value="denied"),
                    WorkflowEvidenceItem(
                        label="Result", value="Approval denied; no change executed."
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
            items=_clean_items(
                [
                    WorkflowEvidenceItem(label="Observed email", value=after_email),
                    WorkflowEvidenceItem(label="Expected email", value=requested_email),
                ]
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
                    WorkflowEvidenceItem(
                        label="Verified",
                        value=(
                            "yes"
                            if after_account is not None
                            and after_email.lower() == requested_email.lower()
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
