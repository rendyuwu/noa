from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.types import (
    WorkflowApprovalPresentation,
    WorkflowApprovalPresentationBlock,
    WorkflowEvidenceItem,
    WorkflowEvidenceSection,
    WorkflowEvidenceTemplate,
    WorkflowInference,
    WorkflowReplyTemplate,
    WorkflowTemplate,
    WorkflowTemplateContext,
    WorkflowTemplatePhase,
    collect_recent_preflight_evidence,
    normalized_string_list,
    normalized_text,
)
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem
from noa_api.whm.tools.preflight_tools import (
    collect_primary_domain_change_state,
    whm_preflight_account,
)
from noa_api.whm.tools.firewall_tools import whm_preflight_firewall_entries


class _WHMTemplate(WorkflowTemplate):
    def build_before_state(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        preflight_results: list[dict[str, object]],
    ) -> list[dict[str, str]] | None:
        context = WorkflowTemplateContext(
            tool_name=tool_name,
            args=args,
            phase="waiting_on_approval",
            preflight_evidence=[
                {
                    "toolName": item.get("toolName"),
                    "result": item.get("result"),
                }
                for item in preflight_results
                if isinstance(item, dict)
            ],
        )
        evidence_template = self.build_evidence_template(context)
        if evidence_template is not None:
            for section in evidence_template.sections:
                if section.key != "before_state":
                    continue
                return [
                    {"label": item.label, "value": item.value}
                    for item in section.items
                    if item.label.strip() and item.value.strip()
                ]
        return _extract_before_state(preflight_results)


def _build_account_lifecycle_reply_template(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    return _build_account_lifecycle_reply_template_impl(context)


def _build_contact_email_reply_template(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    return _build_contact_email_reply_template_impl(context)


def _build_primary_domain_reply_template(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    return _build_primary_domain_reply_template_impl(context)


class _WHMAccountTemplate(_WHMTemplate):
    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_account_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )

    async def fetch_postflight_result(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        session: AsyncSession,
    ) -> dict[str, object] | None:
        _ = tool_name
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        if server_ref is None or username is None:
            return None
        result = await whm_preflight_account(
            session=session,
            server_ref=server_ref,
            username=username,
        )
        return result if isinstance(result, dict) else None


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


class WHMAccountPrimaryDomainTemplate(_WHMTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        subject = _account_subject(context.args)
        before_preflight = _matching_primary_domain_preflight(
            preflight_evidence=context.preflight_evidence,
            args=context.args,
        )
        after_account = _postflight_account(context.postflight_result)
        reason = normalized_text(context.args.get("reason"))
        requested_domain = normalized_text(context.args.get("new_domain"))
        statuses = _default_step_statuses(reason=reason, phase=context.phase)

        return [
            {
                "content": _primary_domain_preflight_step_content(
                    subject=subject,
                    requested_domain=requested_domain,
                    preflight_result=before_preflight,
                ),
                "status": "completed"
                if before_preflight is not None
                else "in_progress",
                "priority": "high",
            },
            {
                "content": _reason_step_content(
                    action_label="changing the primary domain",
                    reason=reason,
                    missing_reason_text=(
                        "Ask the user for a reason—an osTicket/reference number or a brief "
                        "description—before changing the primary domain for the account."
                    ),
                ),
                "status": cast(Any, statuses["reason"]),
                "priority": "high",
            },
            {
                "content": f"Request approval to change the primary domain for {subject} to '{requested_domain or 'the requested value'}'.",
                "status": cast(Any, statuses["approval"]),
                "priority": "high",
            },
            {
                "content": f"Execute the primary domain change for {subject}.",
                "status": cast(Any, statuses["execute"]),
                "priority": "high",
            },
            {
                "content": _primary_domain_postflight_step_content(
                    subject=subject,
                    requested_domain=requested_domain,
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
        return _build_primary_domain_reply_template(context)

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        return _build_primary_domain_evidence_template(context)

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        _ = tool_name
        return (
            f"Change primary domain for '{args.get('username', 'unknown')}' "
            f"to '{args.get('new_domain', 'unknown')}'"
        )

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_primary_domain_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )

    async def fetch_postflight_result(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        session: AsyncSession,
    ) -> dict[str, object] | None:
        _ = tool_name
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        new_domain = normalized_text(args.get("new_domain"))
        if server_ref is None or username is None or new_domain is None:
            return None
        result = await collect_primary_domain_change_state(
            session=session,
            server_ref=server_ref,
            username=username,
            new_domain=new_domain,
            check_dns_zone=True,
        )
        return result if isinstance(result, dict) else None

    def infer_waiting_on_user_workflow(
        self,
        *,
        assistant_text: str,
        working_messages: list[dict[str, object]],
    ) -> WorkflowInference | None:
        _ = assistant_text
        last_user_text = _latest_user_text(working_messages)
        if last_user_text is None or "domain" not in last_user_text.lower():
            return None

        new_domain = _extract_domain(last_user_text)
        if new_domain is None:
            return None

        candidates = _primary_domain_preflight_candidates(working_messages)
        if not candidates:
            return None

        match = _select_primary_domain_preflight_candidate(
            candidates=candidates,
            user_text=last_user_text,
            new_domain=new_domain,
        )
        if match is None:
            return None

        args = cast(dict[str, object], match.get("args"))
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        if server_ref is None or username is None:
            return None

        return WorkflowInference(
            tool_name="whm_change_primary_domain",
            args={
                "server_ref": server_ref,
                "username": username,
                "new_domain": new_domain,
            },
        )


class WHMFirewallBatchTemplate(_WHMTemplate):
    """
    Workflow template for unified firewall operations (CSF + Imunify).

    Similar to WHMCSFBatchTemplate but handles whm_preflight_firewall_entries
    and combined CSF/Imunify results.
    """

    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        targets = normalized_string_list(context.args.get("targets"))
        subject = _firewall_subject(context.args)
        reason = normalized_text(context.args.get("reason"))
        before_entries = _matching_firewall_preflight_entries(
            preflight_evidence=context.preflight_evidence,
            args=context.args,
        )
        postflight_entries = _postflight_firewall_entries(context.postflight_result)
        statuses = _default_step_statuses(reason=reason, phase=context.phase)
        preflight_complete = len(before_entries) == len(targets) and len(targets) > 0

        return [
            {
                "content": _firewall_preflight_step_content(
                    subject=subject, entries=before_entries, targets=targets
                ),
                "status": "completed" if preflight_complete else "in_progress",
                "priority": "high",
            },
            {
                "content": _reason_step_content(
                    action_label=_firewall_action_phrase(context.tool_name),
                    reason=reason,
                    missing_reason_text=_firewall_missing_reason_text(
                        context.tool_name
                    ),
                ),
                "status": cast(Any, statuses["reason"]),
                "priority": "high",
            },
            {
                "content": f"Request approval to {_firewall_action_phrase(context.tool_name)} for {subject}.",
                "status": cast(Any, statuses["approval"]),
                "priority": "high",
            },
            {
                "content": f"Execute {_firewall_action_phrase(context.tool_name)} for {subject}.",
                "status": cast(Any, statuses["execute"]),
                "priority": "high",
            },
            {
                "content": _firewall_postflight_step_content(
                    tool_name=context.tool_name,
                    subject=subject,
                    entries=postflight_entries,
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
        return _build_firewall_reply_template(context)

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        return _build_firewall_evidence_template(context)

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        return f"{_firewall_activity_phrase(tool_name)} '{_format_argument_value(args.get('targets'))}'"

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_firewall_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )

    async def fetch_postflight_result(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        session: AsyncSession,
    ) -> dict[str, object] | None:
        _ = tool_name
        server_ref = normalized_text(args.get("server_ref"))
        targets = normalized_string_list(args.get("targets"))
        if server_ref is None or not targets:
            return None

        results: list[dict[str, object]] = []
        for target in targets:
            result = await whm_preflight_firewall_entries(
                session=session,
                server_ref=server_ref,
                target=target,
            )
            if isinstance(result, dict):
                results.append(result)
        return {"ok": True, "results": results}


WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {
    "whm-account-lifecycle": WHMAccountLifecycleTemplate(),
    "whm-account-contact-email": WHMAccountContactEmailTemplate(),
    "whm-account-primary-domain": WHMAccountPrimaryDomainTemplate(),
    "whm-firewall-batch-change": WHMFirewallBatchTemplate(),
}


def _require_account_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    if requested_server_ref is None or requested_username is None:
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_account"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required WHM preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_account with the same server_ref and username before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) == requested_username:
            return None

    return SanitizedToolError(
        error="Required WHM preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful whm_preflight_account was found for server_ref '{requested_server_ref}' and username '{requested_username}' in the current turn.",
        ),
    )


def _require_primary_domain_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    requested_domain = normalized_text(args.get("new_domain"))
    if (
        requested_server_ref is None
        or requested_username is None
        or requested_domain is None
    ):
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_primary_domain_change"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required WHM preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_primary_domain_change with the same server_ref, username, and new_domain before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) != requested_username:
            continue
        if normalized_text(result.get("requested_domain")) != requested_domain:
            continue
        return None

    return SanitizedToolError(
        error="Required WHM preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful whm_preflight_primary_domain_change was found for server_ref '{requested_server_ref}', username '{requested_username}', and new_domain '{requested_domain}' in the current turn.",
        ),
    )


def _server_identity_matches(
    *,
    item_args: dict[str, object],
    result: dict[str, object],
    requested_server_ref: str,
    requested_server_id: str | None,
) -> bool:
    result_server_id = normalized_text(result.get("server_id"))
    if requested_server_id is not None and result_server_id is not None:
        return result_server_id == requested_server_id
    return normalized_text(item_args.get("server_ref")) == requested_server_ref


def _account_preflight_candidates(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_account"
        and isinstance(item.get("args"), dict)
    ]


def _primary_domain_preflight_candidates(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_primary_domain_change"
        and isinstance(item.get("args"), dict)
    ]


def _latest_user_text(working_messages: list[dict[str, object]]) -> str | None:
    for message in reversed(working_messages):
        if message.get("role") != "user":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _infer_whm_account_lifecycle_tool_name(user_text: str) -> str | None:
    lowered = user_text.lower()
    if "unsuspend" in lowered:
        return "whm_unsuspend_account"
    if "suspend" in lowered:
        return "whm_suspend_account"
    return None


def _select_account_preflight_candidate(
    *, account_candidates: list[dict[str, object]], user_text: str
) -> dict[str, object] | None:
    if len(account_candidates) == 1:
        return account_candidates[0]

    lowered = user_text.lower()
    for candidate in reversed(account_candidates):
        args = candidate.get("args")
        if not isinstance(args, dict):
            continue
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        if server_ref is None or username is None:
            continue
        if server_ref.lower() in lowered and username.lower() in lowered:
            return candidate

    return None


def _select_primary_domain_preflight_candidate(
    *, candidates: list[dict[str, object]], user_text: str, new_domain: str
) -> dict[str, object] | None:
    lowered = user_text.lower()
    normalized_domain = new_domain.lower()

    for candidate in reversed(candidates):
        args = candidate.get("args")
        result = candidate.get("result")
        if not isinstance(args, dict) or not isinstance(result, dict):
            continue
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        requested_domain = normalized_text(result.get("requested_domain"))
        if (
            server_ref is None
            or username is None
            or requested_domain is None
            or requested_domain.lower() != normalized_domain
        ):
            continue
        if server_ref.lower() in lowered and username.lower() in lowered:
            return candidate

    for candidate in reversed(candidates):
        result = candidate.get("result")
        if not isinstance(result, dict):
            continue
        requested_domain = normalized_text(result.get("requested_domain"))
        if (
            requested_domain is not None
            and requested_domain.lower() == normalized_domain
        ):
            return candidate

    return None


def _extract_email(text: str) -> str | None:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match is not None else None


def _extract_domain(text: str) -> str | None:
    for match in re.finditer(
        r"\b(?!(?:[A-Za-z0-9._%+-]+@))([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b",
        text,
    ):
        domain = normalized_text(match.group(1))
        if domain is not None:
            return domain.rstrip(".").lower()
    return None


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


def _extract_before_state(
    preflight_results: list[dict[str, object]],
) -> list[dict[str, str]]:
    before_state: list[dict[str, str]] = []
    for item in preflight_results:
        tool_name = item.get("toolName")
        result = item.get("result")
        if not isinstance(tool_name, str) or not isinstance(result, dict):
            continue
        if tool_name == "whm_preflight_account":
            account = result.get("account")
            if isinstance(account, dict):
                for key, label in (
                    ("user", "Username"),
                    ("domain", "Domain"),
                    ("contactemail", "Contact email"),
                    ("suspended", "Suspended"),
                    ("suspendreason", "Suspend reason"),
                    ("plan", "Plan"),
                ):
                    value = account.get(key)
                    if value in (None, ""):
                        continue
                    before_state.append(
                        {"label": label, "value": _format_argument_value(value)}
                    )
    return before_state


def _default_step_statuses(
    *, reason: str | None, phase: WorkflowTemplatePhase
) -> dict[str, str]:
    statuses = {
        "reason": "completed" if reason is not None else "pending",
        "approval": "pending",
        "execute": "pending",
        "postflight": "pending",
    }
    if phase == "waiting_on_user":
        statuses["reason"] = "waiting_on_user"
    elif phase == "waiting_on_approval":
        statuses["approval"] = "waiting_on_approval"
    elif phase == "executing":
        statuses["approval"] = "completed"
        statuses["execute"] = "in_progress"
    elif phase == "completed":
        statuses["approval"] = "completed"
        statuses["execute"] = "completed"
        statuses["postflight"] = "completed"
    elif phase == "denied":
        statuses["approval"] = "cancelled"
        statuses["execute"] = "cancelled"
        statuses["postflight"] = "cancelled"
    elif phase == "failed":
        statuses["approval"] = "completed"
        statuses["execute"] = "cancelled"
        statuses["postflight"] = "cancelled"
    if reason is None and phase in {"completed", "denied", "failed"}:
        statuses["reason"] = "cancelled"
    return statuses


def _approval_detail_rows(*rows: tuple[str, str | None]) -> list[dict[str, str]]:
    return [
        {"label": label, "value": value}
        for label, value in rows
        if value is not None and label.strip() and value.strip()
    ]


def _approval_key_value_block_from_details(
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


def _approval_reason_detail(reason: str | None) -> str:
    return reason or "none provided"


def _approval_paragraph_block(text: str | None) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(kind="paragraph", text=text)


def _approval_bullet_list_block(
    *items: str | None,
) -> WorkflowApprovalPresentationBlock:
    return WorkflowApprovalPresentationBlock(
        kind="bullet_list",
        items=[item for item in items if isinstance(item, str) and item.strip()],
    )


def _approval_key_value_block(
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


def _approval_presentation(
    *blocks: WorkflowApprovalPresentationBlock,
) -> WorkflowApprovalPresentation:
    return WorkflowApprovalPresentation(
        blocks=[
            block
            for block in blocks
            if isinstance(block, WorkflowApprovalPresentationBlock)
        ]
    )


def _approval_presentation_from_reply_data(
    *,
    paragraph: str | None,
    details: list[dict[str, str]] | None,
    evidence_summary: list[str],
) -> WorkflowApprovalPresentation:
    return _approval_presentation(
        _approval_paragraph_block(paragraph),
        _approval_key_value_block_from_details(details),
        _approval_bullet_list_block(*evidence_summary),
    )


def _join_with_and(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def _approval_sentence_summary(items: list[str]) -> str | None:
    summaries = [
        text.removesuffix(".")
        for item in items
        for text in [normalized_text(item)]
        if text is not None
    ]
    if not summaries:
        return None
    return "; ".join(summaries) + "."


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
            f"Success condition: {success_criteria}",
        ]
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
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
            f"Success condition: {success_criteria}",
        ]
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
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


def _build_primary_domain_reply_template_impl(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    subject = _account_subject(context.args)
    requested_domain = (
        normalized_text(context.args.get("new_domain")) or "the requested domain"
    )
    before_preflight = _matching_primary_domain_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    before_account = (
        before_preflight.get("account") if isinstance(before_preflight, dict) else None
    )
    after_account = _postflight_account(context.postflight_result)
    reason = normalized_text(context.args.get("reason"))
    before_domain = _account_domain(before_account) or "unknown"
    after_domain = _account_domain(after_account) or before_domain
    requested_location = _requested_domain_location(before_preflight) or "unknown"
    owner = _domain_owner(before_preflight)
    result_ok = _result_ok(context.result)
    result_status = _result_status(context.result)
    result_message = _result_message(context.result)
    dns_zone_exists = _dns_zone_exists(context.postflight_result)

    if context.phase == "waiting_on_approval":
        success_criteria = f"The primary domain changes to '{requested_domain}' and the DNS zone exists."
        details = _approval_detail_rows(
            (
                "Action",
                f"Change the primary domain for {subject} to '{requested_domain}'.",
            ),
            ("Reason", _approval_reason_detail(reason)),
            ("Success criteria", success_criteria),
        )
        evidence = [
            f"Preflight found the current primary domain as '{before_domain}'.",
            f"Requested domain location on the account: {requested_location}.",
            (
                f"Server ownership check: '{requested_domain}' is currently owned by '{owner}'."
                if owner is not None
                else f"Server ownership check: '{requested_domain}' is not owned by another account."
            ),
            f"Success condition: {success_criteria}",
        ]
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title="Primary domain approval requested",
            outcome="info",
            summary=f"This will change the primary domain for {subject} to '{requested_domain}' after approval.",
            evidence_summary=[],
            approval_presentation=_approval_presentation_from_reply_data(
                paragraph=f"WHM primary domain change for {subject}.",
                details=details,
                evidence_summary=evidence,
            ),
            details=details,
            next_step="Approve the request to apply the primary-domain change, or deny it to keep the current domain.",
        )

    if context.phase == "denied":
        evidence = [f"Last confirmed primary domain: '{before_domain}'."]
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title="Primary domain change denied",
            outcome="denied",
            summary=f"The request to change the primary domain for {subject} was denied. No change was applied.",
            evidence_summary=evidence,
            next_step="Submit a new approval request if you still need to change this primary domain.",
        )

    if context.phase == "failed":
        evidence = [f"Last confirmed primary domain: '{before_domain}'."]
        if context.error_code is not None:
            evidence.append(f"Error code: {context.error_code}.")
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title="Primary domain change failed",
            outcome="failed",
            summary=f"NOA could not complete the primary domain change for {subject}.",
            evidence_summary=evidence,
            next_step="Run the primary-domain preflight again to confirm the current state before retrying.",
        )

    if context.phase != "completed":
        return None

    evidence = [f"Before primary domain: '{before_domain}'."]
    if result_message is not None:
        evidence.append(f"Tool result: {result_message}.")
    if after_account is not None:
        evidence.append(f"Postflight primary domain: '{after_domain}'.")
    if dns_zone_exists is True:
        evidence.append(f"DNS zone found for '{requested_domain}'.")
    elif dns_zone_exists is False:
        evidence.append(f"DNS zone was not found for '{requested_domain}'.")
    if reason is not None:
        evidence.append(f"Recorded reason: {reason}.")

    if result_ok is False:
        error_code = (
            _result_error_code(context.result) or context.error_code or "unknown"
        )
        evidence.append(f"Error code: {error_code}.")
        return WorkflowReplyTemplate(
            title="Primary domain change failed",
            outcome="failed",
            summary=f"NOA did not confirm the primary domain change for {subject}.",
            evidence_summary=evidence,
            next_step="Review the error and rerun the primary-domain preflight before retrying.",
        )

    if result_status == "no-op":
        return WorkflowReplyTemplate(
            title="Primary domain change no-op",
            outcome="no_op",
            summary=f"No primary domain change was needed for {subject}.",
            evidence_summary=evidence,
            next_step="No further action is required unless you expected a different primary domain.",
        )

    if (
        after_account is None
        or after_domain.lower() != requested_domain.lower()
        or dns_zone_exists is not True
    ):
        if after_account is None:
            evidence.append(
                "Postflight verification did not confirm the final primary domain."
            )
        elif after_domain.lower() != requested_domain.lower():
            evidence.append(f"Expected final primary domain: '{requested_domain}'.")
        if dns_zone_exists is not True:
            evidence.append(
                f"Expected to find a DNS zone for '{requested_domain}' after the change."
            )
        return WorkflowReplyTemplate(
            title="Primary domain change partially verified",
            outcome="partial",
            summary=f"The primary domain change finished for {subject}, but NOA could not fully verify the final domain state.",
            evidence_summary=evidence,
            next_step="Run the primary-domain preflight again before making another change.",
        )

    return WorkflowReplyTemplate(
        title="Primary domain change completed",
        outcome="changed",
        summary=(
            f"The primary domain for {subject} moved from '{before_domain}' to '{after_domain}'."
        ),
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


def _build_primary_domain_evidence_template(
    context: WorkflowTemplateContext,
) -> WorkflowEvidenceTemplate | None:
    subject = _account_subject(context.args)
    requested_domain = normalized_text(context.args.get("new_domain")) or "unknown"
    before_preflight = _matching_primary_domain_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    before_account = (
        before_preflight.get("account") if isinstance(before_preflight, dict) else None
    )
    after_account = _postflight_account(context.postflight_result)
    inventory = _domain_inventory(before_preflight)
    reason = normalized_text(context.args.get("reason"))
    before_domain = _account_domain(before_account) or "unknown"
    after_domain = _account_domain(after_account) or before_domain
    requested_location = _requested_domain_location(before_preflight) or "unknown"
    owner = _domain_owner(before_preflight) or "none"
    dns_zone_exists = _dns_zone_exists(context.postflight_result)
    result_status = _result_status(context.result)
    result_ok = _result_ok(context.result)

    sections: list[WorkflowEvidenceSection] = [
        WorkflowEvidenceSection(
            key="before_state",
            title="Before state",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(label="Subject", value=subject),
                    WorkflowEvidenceItem(label="Primary domain", value=before_domain),
                ]
            ),
        ),
        WorkflowEvidenceSection(
            key="requested_change",
            title="Requested change",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(label="Action", value="change primary domain"),
                    WorkflowEvidenceItem(
                        label="Requested domain", value=requested_domain
                    ),
                    WorkflowEvidenceItem(
                        label="Reason", value=reason or "none provided"
                    ),
                ]
            ),
        ),
        WorkflowEvidenceSection(
            key="preflight_results",
            title="Preflight results",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(
                        label="Requested domain location", value=requested_location
                    ),
                    WorkflowEvidenceItem(label="Server owner", value=owner),
                    WorkflowEvidenceItem(
                        label="Account main domain",
                        value=(
                            normalized_text(inventory.get("main_domain")) or "unknown"
                            if isinstance(inventory, dict)
                            else "unknown"
                        ),
                    ),
                    WorkflowEvidenceItem(
                        label="Addon domains",
                        value=_render_domain_list(
                            inventory.get("addon_domains")
                            if isinstance(inventory, dict)
                            else None
                        ),
                    ),
                    WorkflowEvidenceItem(
                        label="Parked domains",
                        value=_render_domain_list(
                            inventory.get("parked_domains")
                            if isinstance(inventory, dict)
                            else None
                        ),
                    ),
                    WorkflowEvidenceItem(
                        label="Subdomains",
                        value=_render_domain_list(
                            inventory.get("sub_domains")
                            if isinstance(inventory, dict)
                            else None
                        ),
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
                    WorkflowEvidenceItem(
                        label="Observed primary domain", value=after_domain
                    ),
                    WorkflowEvidenceItem(
                        label="Expected primary domain", value=requested_domain
                    ),
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
                        label="DNS zone exists",
                        value=(
                            "yes"
                            if dns_zone_exists is True
                            else "no"
                            if dns_zone_exists is False
                            else "unknown"
                        ),
                    ),
                    WorkflowEvidenceItem(
                        label="Verified",
                        value=(
                            "yes"
                            if after_account is not None
                            and after_domain.lower() == requested_domain.lower()
                            and dns_zone_exists is True
                            else "partial"
                            if after_account is not None or dns_zone_exists is not None
                            else "no"
                        ),
                    ),
                ]
            ),
        )
    )
    return WorkflowEvidenceTemplate(sections=sections)


def _clean_items(items: list[WorkflowEvidenceItem]) -> list[WorkflowEvidenceItem]:
    return [item for item in items if item.label.strip() and item.value.strip()]


def _account_before_state_items(
    account: dict[str, object] | None,
) -> list[WorkflowEvidenceItem]:
    if not isinstance(account, dict):
        return []
    items = [
        WorkflowEvidenceItem(
            label="Username",
            value=normalized_text(account.get("user")) or "unknown",
        ),
        WorkflowEvidenceItem(
            label="Domain",
            value=normalized_text(account.get("domain")) or "unknown",
        ),
        WorkflowEvidenceItem(
            label="Contact email",
            value=_account_email(account) or "unknown",
        ),
        WorkflowEvidenceItem(
            label="State",
            value=_account_state(account) or "unknown",
        ),
    ]
    suspend_reason = normalized_text(account.get("suspendreason"))
    if suspend_reason is not None:
        items.append(WorkflowEvidenceItem(label="Suspend reason", value=suspend_reason))
    return _clean_items(items)


def _account_after_state_items(
    account: dict[str, object] | None,
    *,
    expected_state: str,
) -> list[WorkflowEvidenceItem]:
    if not isinstance(account, dict):
        return [WorkflowEvidenceItem(label="Observed state", value="unknown")]
    return _clean_items(
        [
            WorkflowEvidenceItem(
                label="Expected state",
                value=expected_state,
            ),
            WorkflowEvidenceItem(
                label="Observed state",
                value=_account_state(account) or "unknown",
            ),
        ]
    )


def _result_ok(result: dict[str, object] | None) -> bool | None:
    if not isinstance(result, dict):
        return None
    value = result.get("ok")
    return value if isinstance(value, bool) else None


def _result_status(result: dict[str, object] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    return normalized_text(result.get("status"))


def _result_message(result: dict[str, object] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    return normalized_text(result.get("message"))


def _result_error_code(result: dict[str, object] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    return normalized_text(result.get("error_code"))


def _targets_with_status(
    result_items: list[dict[str, object]],
    status: str,
) -> list[str]:
    return [
        target
        for item in result_items
        if normalized_text(item.get("status")) == status
        for target in [normalized_text(item.get("target"))]
        if target is not None
    ]


def _account_subject(args: dict[str, object]) -> str:
    username = normalized_text(args.get("username")) or "the account"
    server_ref = normalized_text(args.get("server_ref"))
    if server_ref is None:
        return f"'{username}'"
    return f"'{username}' on '{server_ref}'"


def _action_label(tool_name: str) -> str:
    if tool_name == "whm_unsuspend_account":
        return "unsuspend"
    return "suspend"


def _preflight_step_content(
    *, subject: str, before_account: dict[str, object] | None
) -> str:
    if before_account is None:
        return f"Account lookup / preflight for {subject}."

    state = _account_state(before_account)
    details: list[str] = [f"state: {state}"]
    domain = normalized_text(before_account.get("domain"))
    if domain is not None:
        details.append(f"domain: {domain}")
    contact = normalized_text(before_account.get("contactemail"))
    if contact is not None:
        details.append(f"contact: {contact}")
    suspend_reason = normalized_text(before_account.get("suspendreason"))
    if suspend_reason is not None:
        details.append(f"suspend reason: {suspend_reason}")
    return f"Account lookup / preflight for {subject}: {'; '.join(details)}."


def _reason_step_content(
    *,
    action_label: str,
    reason: str | None,
    missing_reason_text: str | None = None,
) -> str:
    if reason is None:
        if missing_reason_text is not None:
            return missing_reason_text
        return (
            f"Ask the user for a reason—an osTicket/reference number or a brief "
            f"description—before {action_label}ing the account."
        )
    return f"Reason captured for the {action_label}: {reason}."


def _render_domain_list(value: object) -> str:
    domains = normalized_string_list(value)
    return ", ".join(domains) if domains else "none"


def _primary_domain_preflight_step_content(
    *,
    subject: str,
    requested_domain: str | None,
    preflight_result: dict[str, object] | None,
) -> str:
    if not isinstance(preflight_result, dict):
        return f"Primary-domain lookup / preflight for {subject}."

    account = preflight_result.get("account")
    before_domain = _account_domain(account if isinstance(account, dict) else None)
    location = _requested_domain_location(preflight_result) or "unknown"
    owner = _domain_owner(preflight_result) or "none"
    inventory = _domain_inventory(preflight_result)
    details = [
        f"current primary domain: {before_domain or 'unknown'}",
        f"requested domain: {requested_domain or 'unknown'}",
        f"requested domain location: {location}",
        f"server owner: {owner}",
    ]
    if isinstance(inventory, dict):
        details.append(
            "addon domains: " + _render_domain_list(inventory.get("addon_domains"))
        )
    return f"Primary-domain lookup / preflight for {subject}: {'; '.join(details)}."


def _primary_domain_postflight_step_content(
    *,
    subject: str,
    requested_domain: str | None,
    after_account: dict[str, object] | None,
    postflight_result: dict[str, object] | None,
) -> str:
    if after_account is None:
        if (
            isinstance(postflight_result, dict)
            and postflight_result.get("ok") is not True
        ):
            error_code = (
                normalized_text(postflight_result.get("error_code")) or "unknown"
            )
            return f"Postflight verification for {subject} could not confirm the primary domain ({error_code})."
        return f"Postflight verification for {subject}."

    observed_domain = _account_domain(after_account) or "unknown"
    zone_text = (
        "dns zone found"
        if _dns_zone_exists(postflight_result) is True
        else "dns zone missing"
        if _dns_zone_exists(postflight_result) is False
        else "dns zone unknown"
    )
    return (
        f"Postflight verification for {subject}: expected primary domain '{requested_domain or 'unknown'}', "
        f"observed '{observed_domain}', {zone_text}."
    )


def _contact_email_postflight_step_content(
    *,
    subject: str,
    requested_email: str | None,
    after_account: dict[str, object] | None,
    postflight_result: dict[str, object] | None,
) -> str:
    if after_account is None:
        if (
            isinstance(postflight_result, dict)
            and postflight_result.get("ok") is not True
        ):
            error_code = (
                normalized_text(postflight_result.get("error_code")) or "unknown"
            )
            return f"Postflight verification for {subject} could not confirm the contact email ({error_code})."
        return f"Postflight verification for {subject}."
    observed_email = _account_email(after_account) or "unknown"
    return f"Postflight verification for {subject}: expected contact email '{requested_email or 'unknown'}', observed '{observed_email}'."


def _contact_email_conclusion_step_content(
    *,
    subject: str,
    reason: str | None,
    requested_email: str | None,
    before_account: dict[str, object] | None,
    after_account: dict[str, object] | None,
    result: dict[str, object] | None,
    phase: WorkflowTemplatePhase,
    error_code: str | None,
) -> str:
    before_email = _account_email(before_account) or "unknown"
    after_email = _account_email(after_account) or before_email
    reason_suffix = f" Reason: {reason}." if reason is not None else ""
    if phase == "waiting_on_user":
        return f"Conclusion with before/after contact email evidence for {subject} after the reason is provided."
    if phase == "waiting_on_approval":
        return f"Conclusion with before/after contact email evidence for {subject} after approval and execution.{reason_suffix}"
    if phase == "executing":
        return f"Conclusion for {subject} after execution and contact email verification.{reason_suffix}"
    if phase == "denied":
        return f"Conclusion: approval denied for {subject}; contact email stayed '{before_email}'.{reason_suffix}"
    if phase == "failed":
        return f"Conclusion: contact email change for {subject} did not complete successfully (error: {error_code or 'tool_execution_failed'}). Before email: '{before_email}'.{reason_suffix}"
    result_status = (
        normalized_text(result.get("status")) if isinstance(result, dict) else None
    )
    if result_status == "no-op":
        return f"Conclusion: no-op for {subject}. Contact email remained '{before_email}'.{reason_suffix}"
    return f"Conclusion: contact email for {subject} moved from '{before_email}' to '{after_email}'.{reason_suffix}"


def _postflight_step_content(
    *,
    tool_name: str,
    subject: str,
    after_account: dict[str, object] | None,
    postflight_result: dict[str, object] | None,
) -> str:
    if after_account is None:
        if (
            isinstance(postflight_result, dict)
            and postflight_result.get("ok") is not True
        ):
            error_code = (
                normalized_text(postflight_result.get("error_code")) or "unknown"
            )
            return f"Postflight verification for {subject} could not be completed ({error_code})."
        return f"Postflight verification for {subject}."

    expected_state = "active" if tool_name == "whm_unsuspend_account" else "suspended"
    actual_state = _account_state(after_account)
    return f"Postflight verification for {subject}: expected {expected_state}, observed {actual_state}."


def _conclusion_step_content(
    *,
    tool_name: str,
    subject: str,
    reason: str | None,
    before_account: dict[str, object] | None,
    after_account: dict[str, object] | None,
    result: dict[str, object] | None,
    phase: WorkflowTemplatePhase,
    error_code: str | None,
) -> str:
    _ = tool_name
    before_state = _account_state(before_account)
    after_state = _account_state(after_account)
    before_text = before_state or "unknown"
    after_text = after_state or before_text
    reason_suffix = f" Reason: {reason}." if reason is not None else ""

    if phase == "waiting_on_user":
        return f"Conclusion with before/after evidence for {subject} after the reason is provided."
    if phase == "waiting_on_approval":
        return f"Conclusion with before/after evidence for {subject} after approval and execution.{reason_suffix}"
    if phase == "executing":
        return f"Conclusion for {subject} after execution and postflight verification.{reason_suffix}"
    if phase == "denied":
        return f"Conclusion: approval denied for {subject}; no change executed. Before state remained {before_text}.{reason_suffix}"
    if phase == "failed":
        error_text = error_code or "tool_execution_failed"
        return f"Conclusion: {subject} did not complete successfully (error: {error_text}). Before state: {before_text}.{reason_suffix}"

    result_status = (
        normalized_text(result.get("status")) if isinstance(result, dict) else None
    )
    if result_status == "no-op":
        return f"Conclusion: no-op for {subject}. Before state: {before_text}. After state: {after_text}.{reason_suffix}"
    return f"Conclusion: {subject} moved from {before_text} to {after_text}.{reason_suffix}"


def _matching_account_preflight(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_account":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) != requested_username:
            continue
        return account
    return None


def _matching_primary_domain_preflight(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    requested_domain = normalized_text(args.get("new_domain"))
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_primary_domain_change":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) != requested_username:
            continue
        if normalized_text(result.get("requested_domain")) != requested_domain:
            continue
        return result
    return None


def _postflight_account(
    postflight_result: dict[str, object] | None,
) -> dict[str, object] | None:
    if (
        not isinstance(postflight_result, dict)
        or postflight_result.get("ok") is not True
    ):
        return None
    account = postflight_result.get("account")
    if isinstance(account, dict):
        return account
    return None


def _account_state(account: dict[str, object] | None) -> str | None:
    if not isinstance(account, dict):
        return None
    value = account.get("suspended")
    if isinstance(value, bool):
        return "suspended" if value else "active"
    if isinstance(value, int):
        return "suspended" if value == 1 else "active"
    if isinstance(value, str):
        return (
            "suspended"
            if value.strip().lower() in {"1", "true", "yes", "y"}
            else "active"
        )
    return None


def _account_email(account: dict[str, object] | None) -> str | None:
    if not isinstance(account, dict):
        return None
    contact = normalized_text(account.get("contactemail"))
    if contact is not None:
        return contact
    return normalized_text(account.get("email"))


def _account_domain(account: dict[str, object] | None) -> str | None:
    if not isinstance(account, dict):
        return None
    domain = normalized_text(account.get("domain"))
    if domain is None:
        return None
    return domain.rstrip(".").lower()


def _domain_inventory(
    preflight_result: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(preflight_result, dict):
        return None
    inventory = preflight_result.get("domain_inventory")
    if isinstance(inventory, dict):
        return inventory
    return None


def _requested_domain_location(
    preflight_result: dict[str, object] | None,
) -> str | None:
    if not isinstance(preflight_result, dict):
        return None
    return normalized_text(preflight_result.get("requested_domain_location"))


def _domain_owner(preflight_result: dict[str, object] | None) -> str | None:
    if not isinstance(preflight_result, dict):
        return None
    return normalized_text(preflight_result.get("domain_owner"))


def _dns_zone_exists(postflight_result: dict[str, object] | None) -> bool | None:
    if not isinstance(postflight_result, dict):
        return None
    value = postflight_result.get("dns_zone_exists")
    return value if isinstance(value, bool) else None


def _result_items(result: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        return []
    items = result.get("results")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# Firewall (unified CSF + Imunify) helper functions
# ---------------------------------------------------------------------------


def _firewall_subject(args: dict[str, object]) -> str:
    """Format subject string for firewall operations."""
    targets = normalized_string_list(args.get("targets"))
    server_ref = normalized_text(args.get("server_ref")) or "the server"
    if not targets:
        return f"the requested targets on '{server_ref}'"
    return f"{', '.join(repr(target) for target in targets)} on '{server_ref}'"


def _firewall_action_phrase(tool_name: str) -> str:
    """Get action phrase for firewall tool names."""
    phrases = {
        "whm_firewall_unblock": "unblock",
        "whm_firewall_allowlist_add_ttl": "add to allowlist",
        "whm_firewall_allowlist_remove": "remove from allowlist",
        "whm_firewall_denylist_add_ttl": "add to denylist",
    }
    return phrases.get(tool_name, "change firewall")


def _firewall_missing_reason_text(tool_name: str) -> str:
    phrases = {
        "whm_firewall_unblock": "before unblocking the account firewall.",
        "whm_firewall_allowlist_add_ttl": "before adding the target to the firewall allowlist.",
        "whm_firewall_allowlist_remove": "before removing the target from the firewall allowlist.",
        "whm_firewall_denylist_add_ttl": "before adding the target to the firewall denylist.",
    }
    suffix = phrases.get(tool_name, "before changing the account firewall.")
    return (
        "Ask the user for a reason—an osTicket/reference number or a brief "
        f"description—{suffix}"
    )


def _firewall_activity_phrase(tool_name: str) -> str:
    """Get activity phrase for firewall tool names."""
    phrases = {
        "whm_firewall_unblock": "Unblock",
        "whm_firewall_allowlist_add_ttl": "Add to firewall allowlist",
        "whm_firewall_allowlist_remove": "Remove from firewall allowlist",
        "whm_firewall_denylist_add_ttl": "Add to firewall denylist",
    }
    return phrases.get(tool_name, "Firewall change")


def _firewall_preflight_step_content(
    *, subject: str, entries: list[dict[str, object]], targets: list[str]
) -> str:
    """Build preflight step content for firewall operations."""
    if not entries:
        return f"Firewall lookup / preflight for {subject}."
    seen_targets = {normalized_text(entry.get("target")) for entry in entries}
    summaries = []
    for entry in entries:
        target = normalized_text(entry.get("target"))
        if target is None:
            continue
        combined = entry.get("combined_verdict", "unknown")
        available = entry.get("available_tools", {})
        tools_str = []
        if available.get("csf"):
            tools_str.append("CSF")
        if available.get("imunify"):
            tools_str.append("Imunify")
        tools_desc = "+".join(tools_str) if tools_str else "none"
        summaries.append(f"{target}: {combined} [{tools_desc}]")
    missing = [target for target in targets if target not in seen_targets]
    if missing:
        summaries.append("missing: " + ", ".join(missing))
    return f"Firewall lookup / preflight for {subject}: {'; '.join(summaries)}."


def _firewall_postflight_step_content(
    *,
    tool_name: str,
    subject: str,
    entries: list[dict[str, object]],
    postflight_result: dict[str, object] | None,
) -> str:
    """Build postflight step content for firewall operations."""
    if not entries:
        if (
            isinstance(postflight_result, dict)
            and postflight_result.get("ok") is not True
        ):
            error_code = (
                normalized_text(postflight_result.get("error_code")) or "unknown"
            )
            return f"Postflight verification for {subject} could not be completed ({error_code})."
        return f"Postflight verification for {subject}."
    expectations = {
        "whm_firewall_unblock": "not blocked",
        "whm_firewall_allowlist_remove": "not allowlisted",
        "whm_firewall_allowlist_add_ttl": "allowlisted",
        "whm_firewall_denylist_add_ttl": "blocked",
    }
    expected = expectations.get(tool_name, "updated")
    summaries = [
        f"{entry.get('target')}: expected {expected}, observed {entry.get('combined_verdict')}"
        for entry in entries
        if normalized_text(entry.get("target")) is not None
    ]
    return f"Postflight verification for {subject}: {'; '.join(summaries)}."


def _matching_firewall_preflight_entries(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> list[dict[str, object]]:
    """Extract matching firewall preflight entries for the given args."""
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_targets = set(normalized_string_list(args.get("targets")))
    matches: list[dict[str, object]] = []
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_firewall_entries":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        target = normalized_text(result.get("target"))
        if target is None or target not in requested_targets:
            continue
        matches.append(result)
    matches.sort(key=lambda entry: normalized_text(entry.get("target")) or "")
    return matches


def _postflight_firewall_entries(
    postflight_result: dict[str, object] | None,
) -> list[dict[str, object]]:
    """Extract postflight entries from firewall postflight result."""
    if not isinstance(postflight_result, dict):
        return []
    results = postflight_result.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _firewall_entries_summary(entries: list[dict[str, object]]) -> str:
    """Summarize firewall entries for display."""
    if not entries:
        return "no evidence"
    return "; ".join(
        f"{entry.get('target')}={entry.get('combined_verdict')}"
        for entry in entries
        if normalized_text(entry.get("target")) is not None
    )


def _firewall_available_tool_names(entry: dict[str, object]) -> list[str]:
    available = entry.get("available_tools")
    if not isinstance(available, dict):
        return []
    names: list[str] = []
    if available.get("csf") is True:
        names.append("CSF")
    if available.get("imunify") is True:
        names.append("Imunify")
    return names


def _firewall_entry_status_summary(entry: dict[str, object]) -> str | None:
    target = normalized_text(entry.get("target"))
    verdict = normalized_text(entry.get("combined_verdict")) or "unknown"
    if target is None:
        return None
    tool_names = _firewall_available_tool_names(entry)
    if tool_names:
        return f"{target} is currently {verdict} ({', '.join(tool_names)})."
    return f"{target} is currently {verdict}."


def _last_non_empty_line(value: str) -> str | None:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1]


def _firewall_csf_receipt_value(
    result: dict[str, object], *, target: str
) -> str | None:
    raw_output = result.get("raw_output")
    if isinstance(raw_output, str):
        raw_tail = _last_non_empty_line(raw_output)
        if raw_tail is not None:
            return raw_tail

    verdict = normalized_text(result.get("verdict"))
    if verdict == "blocked":
        return f"Blocked: {target}"
    if verdict == "allowlisted":
        return f"Allowlisted: {target}"
    if verdict == "not_found":
        return f"No matches found for {target}"
    return verdict


def _format_firewall_timestamp(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _firewall_imunify_entry_value(
    entry: dict[str, object], *, target: str
) -> str | None:
    ip = normalized_text(entry.get("ip")) or target
    purpose = normalized_text(entry.get("purpose"))
    if purpose == "drop":
        summary = f"Blacklist entry for {ip}"
    elif purpose == "white":
        summary = f"Whitelist entry for {ip}"
    else:
        summary = f"Imunify entry for {ip}"

    details: list[str] = []
    comment = normalized_text(entry.get("comment"))
    if comment is not None:
        details.append(f"comment: {comment}")
    expiration = _format_firewall_timestamp(entry.get("expiration"))
    if expiration is not None:
        details.append(f"expires: {expiration}")
    country = normalized_text(entry.get("country_name")) or normalized_text(
        entry.get("country_code")
    )
    if country is not None:
        details.append(f"country: {country}")
    if entry.get("manual") is True:
        details.append("manual")

    if details:
        return summary + "; " + "; ".join(details)
    return summary


def _firewall_imunify_metadata_value(raw_data: object) -> str | None:
    if not isinstance(raw_data, dict):
        return None
    if isinstance(raw_data.get("items"), list):
        return None

    parts: list[str] = []
    strategy = normalized_text(raw_data.get("strategy"))
    if strategy is not None:
        parts.append(f"strategy: {strategy}")
    version = normalized_text(raw_data.get("version"))
    if version is not None:
        parts.append(f"version: {version}")

    license_info = raw_data.get("license")
    if isinstance(license_info, dict):
        license_state = license_info.get("status")
        license_type = normalized_text(license_info.get("license_type"))
        if license_state is True:
            label = "license: active"
        elif license_state is False:
            label = "license: inactive"
        else:
            label = None
        if label is not None:
            if license_type is not None:
                parts.append(f"{label} ({license_type})")
            else:
                parts.append(label)

    if not parts:
        return None
    return "Imunify metadata: " + "; ".join(parts)


def _firewall_imunify_receipt_value(
    result: dict[str, object], *, target: str
) -> str | None:
    entries = result.get("entries")
    if isinstance(entries, list):
        rendered = [
            value
            for item in entries
            if isinstance(item, dict)
            for value in [_firewall_imunify_entry_value(item, target=target)]
            if value is not None
        ]
        if rendered:
            return " | ".join(rendered)

    metadata_value = _firewall_imunify_metadata_value(result.get("raw_data"))
    if metadata_value is not None:
        return metadata_value

    verdict = normalized_text(result.get("verdict"))
    if verdict == "blacklisted":
        return f"Blacklist entry for {target}"
    if verdict == "whitelisted":
        return f"Whitelist entry for {target}"
    if verdict == "not_found":
        return f"No Imunify entry found for {target}"
    return verdict


def _firewall_entry_receipt_items(
    entry: dict[str, object],
    *,
    include_full_csf_raw_output: bool = False,
) -> list[WorkflowEvidenceItem]:
    target = normalized_text(entry.get("target"))
    if target is None:
        return []

    items: list[WorkflowEvidenceItem] = []
    csf = entry.get("csf")
    if isinstance(csf, dict):
        csf_value: str | None = None
        if include_full_csf_raw_output:
            raw_output = csf.get("raw_output")
            if isinstance(raw_output, str) and raw_output.strip():
                csf_value = raw_output.strip()
        if csf_value is None:
            csf_value = _firewall_csf_receipt_value(csf, target=target)
        if csf_value is not None:
            items.append(WorkflowEvidenceItem(label=f"{target} · CSF", value=csf_value))

    imunify = entry.get("imunify")
    if isinstance(imunify, dict):
        imunify_value = _firewall_imunify_receipt_value(imunify, target=target)
        if imunify_value is not None:
            items.append(
                WorkflowEvidenceItem(label=f"{target} · Imunify", value=imunify_value)
            )

    if items:
        return items

    combined_verdict = normalized_text(entry.get("combined_verdict")) or "unknown"
    return [WorkflowEvidenceItem(label=target, value=combined_verdict)]


def _firewall_entries_items(
    entries: list[dict[str, object]],
    *,
    include_full_csf_raw_output: bool = False,
) -> list[WorkflowEvidenceItem]:
    return [
        item
        for entry in entries
        for item in _firewall_entry_receipt_items(
            entry,
            include_full_csf_raw_output=include_full_csf_raw_output,
        )
        if item.label.strip() and item.value.strip()
    ]


def _firewall_expected_state(tool_name: str) -> str:
    expectations = {
        "whm_firewall_unblock": "not blocked",
        "whm_firewall_allowlist_remove": "not allowlisted",
        "whm_firewall_allowlist_add_ttl": "allowlisted",
        "whm_firewall_denylist_add_ttl": "blocked",
    }
    return expectations.get(tool_name, "updated")


def _require_firewall_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    """Require matching firewall preflight evidence before allowing firewall change."""
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_targets = normalized_string_list(args.get("targets"))
    if requested_server_ref is None or not requested_targets:
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_firewall_entries"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required firewall preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_firewall_entries for each target with the same server_ref before requesting this change.",
            ),
        )

    matched_targets: set[str] = set()
    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        target = normalized_text(result.get("target"))
        if target is not None:
            matched_targets.add(target)

    missing_targets = [
        target for target in requested_targets if target not in matched_targets
    ]
    if not missing_targets:
        return None

    return SanitizedToolError(
        error="Required firewall preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            "Missing successful whm_preflight_firewall_entries results for target(s): "
            + ", ".join(f"'{target}'" for target in missing_targets),
        ),
    )


def _build_firewall_reply_template(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    subject = _firewall_subject(context.args)
    action_phrase = _firewall_action_phrase(context.tool_name)
    before_entries = _matching_firewall_preflight_entries(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_entries = _postflight_firewall_entries(context.postflight_result)
    targets = normalized_string_list(context.args.get("targets"))
    reason = normalized_text(context.args.get("reason"))
    duration_minutes = context.args.get("duration_minutes")
    result_items = _result_items(context.result)
    changed_targets = _targets_with_status(result_items, "changed")
    noop_targets = _targets_with_status(result_items, "no-op")
    failed_targets = _targets_with_status(result_items, "error")

    if context.phase == "waiting_on_approval":
        preflight_evidence = [
            summary
            for entry in before_entries
            for summary in [_firewall_entry_status_summary(entry)]
            if summary is not None
        ]
        success_criteria = (
            "Postflight reflects the requested firewall state for "
            + ", ".join(repr(target) for target in targets)
            + "."
        )
        details = _approval_detail_rows(
            (
                "Action",
                f"{action_phrase.capitalize()} firewall entries for {subject}.",
            ),
            ("Reason", _approval_reason_detail(reason)),
            ("Evidence", _approval_sentence_summary(preflight_evidence)),
            ("Success criteria", success_criteria),
        )
        evidence = list(preflight_evidence)
        if duration_minutes is not None:
            evidence.append(f"Requested TTL: {duration_minutes} minute(s).")
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        evidence.append(f"Success condition: {success_criteria}")
        return WorkflowReplyTemplate(
            title="Firewall change approval requested",
            outcome="info",
            summary=f"This will {action_phrase} for {subject} after approval.",
            evidence_summary=[],
            approval_presentation=_approval_presentation_from_reply_data(
                paragraph=f"WHM firewall change for {subject}.",
                details=details,
                evidence_summary=evidence,
            ),
            details=details,
            next_step="Approve the request to run the firewall change, or deny it to leave the current state unchanged.",
        )

    if context.phase == "denied":
        evidence = [
            f"Last confirmed state: {summary.removesuffix('.')}"
            for entry in before_entries
            for summary in [_firewall_entry_status_summary(entry)]
            if summary is not None
        ]
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title="Firewall change denied",
            outcome="denied",
            summary=f"The request to {action_phrase} for {subject} was denied. No change was applied.",
            evidence_summary=evidence,
            next_step="Submit a new approval request if you still need this firewall change.",
        )

    if context.phase == "failed":
        evidence = [f"Requested targets: {', '.join(targets) or 'unknown'}."]
        if context.error_code is not None:
            evidence.append(f"Error code: {context.error_code}.")
        if reason is not None:
            evidence.append(f"Recorded reason: {reason}.")
        return WorkflowReplyTemplate(
            title="Firewall change failed",
            outcome="failed",
            summary=f"NOA could not complete the request to {action_phrase} for {subject}.",
            evidence_summary=evidence,
            next_step="Run firewall preflight again for the affected targets before retrying.",
        )

    if context.phase != "completed":
        return None

    evidence = []
    if before_entries:
        evidence.append(f"Before: {_firewall_entries_summary(before_entries)}.")
    if after_entries:
        evidence.append(f"Postflight: {_firewall_entries_summary(after_entries)}.")
    if changed_targets:
        evidence.append("Changed targets: " + ", ".join(changed_targets) + ".")
    if noop_targets:
        evidence.append("No-op targets: " + ", ".join(noop_targets) + ".")
    if failed_targets:
        evidence.append("Failed targets: " + ", ".join(failed_targets) + ".")
    if reason is not None:
        evidence.append(f"Recorded reason: {reason}.")

    result_ok = _result_ok(context.result)
    if result_ok is False and not result_items:
        error_code = (
            _result_error_code(context.result) or context.error_code or "unknown"
        )
        evidence.append(f"Error code: {error_code}.")
        return WorkflowReplyTemplate(
            title="Firewall change failed",
            outcome="failed",
            summary=f"NOA did not complete the request to {action_phrase} for {subject}.",
            evidence_summary=evidence,
            next_step="Review the error and rerun firewall preflight before retrying.",
        )

    if failed_targets:
        return WorkflowReplyTemplate(
            title="Firewall change partially completed",
            outcome="partial",
            summary=f"The request to {action_phrase} for {subject} finished with mixed results.",
            evidence_summary=evidence,
            next_step="Rerun firewall preflight for the failed targets before retrying the change.",
        )

    if changed_targets and noop_targets:
        return WorkflowReplyTemplate(
            title="Firewall change partially completed",
            outcome="partial",
            summary=f"The request to {action_phrase} for {subject} finished with mixed results: some targets changed and others were already in the desired state.",
            evidence_summary=evidence,
        )

    if changed_targets:
        return WorkflowReplyTemplate(
            title="Firewall change completed",
            outcome="changed",
            summary=f"The request to {action_phrase} for {subject} completed successfully.",
            evidence_summary=evidence,
        )

    return WorkflowReplyTemplate(
        title="Firewall change no-op",
        outcome="no_op",
        summary=f"No firewall changes were needed for {subject}.",
        evidence_summary=evidence,
        next_step="No further action is required unless you expected a different firewall state.",
    )


def _build_firewall_evidence_template(
    context: WorkflowTemplateContext,
) -> WorkflowEvidenceTemplate | None:
    action_phrase = _firewall_action_phrase(context.tool_name)
    subject = _firewall_subject(context.args)
    targets = normalized_string_list(context.args.get("targets"))
    reason = normalized_text(context.args.get("reason"))
    duration_minutes = context.args.get("duration_minutes")
    before_entries = _matching_firewall_preflight_entries(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_entries = _postflight_firewall_entries(context.postflight_result)
    result_items = _result_items(context.result)
    changed_targets = _targets_with_status(result_items, "changed")
    noop_targets = _targets_with_status(result_items, "no-op")
    failed_targets = _targets_with_status(result_items, "error")
    result_ok = _result_ok(context.result)

    requested_change_items = [
        WorkflowEvidenceItem(label="Action", value=action_phrase),
        WorkflowEvidenceItem(label="Subject", value=subject),
        WorkflowEvidenceItem(
            label="Expected state",
            value=_firewall_expected_state(context.tool_name),
        ),
        WorkflowEvidenceItem(label="Reason", value=reason or "none provided"),
    ]
    if duration_minutes is not None:
        requested_change_items.insert(
            2,
            WorkflowEvidenceItem(
                label="Requested TTL",
                value=f"{duration_minutes} minute(s)",
            ),
        )

    sections: list[WorkflowEvidenceSection] = [
        WorkflowEvidenceSection(
            key="before_state",
            title="Before state",
            items=(
                _firewall_entries_items(
                    before_entries,
                    include_full_csf_raw_output=context.phase == "waiting_on_approval",
                )
                or [
                    WorkflowEvidenceItem(
                        label="Targets", value=", ".join(targets) or "unknown"
                    )
                ]
            ),
        ),
        WorkflowEvidenceSection(
            key="requested_change",
            title="Requested change",
            items=_clean_items(requested_change_items),
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

    if context.phase == "failed" or (result_ok is False and not result_items):
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
            items=(
                _firewall_entries_items(after_entries)
                or [WorkflowEvidenceItem(label="Postflight", value="unavailable")]
            ),
        )
    )
    sections.append(
        WorkflowEvidenceSection(
            key="outcomes",
            title="Per-target outcomes",
            items=_clean_items(
                [
                    WorkflowEvidenceItem(
                        label="Changed", value=", ".join(changed_targets) or "none"
                    ),
                    WorkflowEvidenceItem(
                        label="No-op", value=", ".join(noop_targets) or "none"
                    ),
                    WorkflowEvidenceItem(
                        label="Failed", value=", ".join(failed_targets) or "none"
                    ),
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
                        label="Expected state",
                        value=_firewall_expected_state(context.tool_name),
                    ),
                    WorkflowEvidenceItem(
                        label="Observed postflight",
                        value=_firewall_entries_summary(after_entries),
                    ),
                    WorkflowEvidenceItem(
                        label="Result",
                        value=(
                            "partial"
                            if failed_targets or (changed_targets and noop_targets)
                            else "changed"
                            if changed_targets
                            else "no-op"
                        ),
                    ),
                ]
            ),
        )
    )
    return WorkflowEvidenceTemplate(sections=sections)
