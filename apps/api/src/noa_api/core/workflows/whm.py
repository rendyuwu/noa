from __future__ import annotations

import re
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.types import (
    WorkflowInference,
    WorkflowTemplate,
    WorkflowTemplateContext,
    WorkflowTemplatePhase,
    collect_recent_preflight_evidence,
    normalized_string_list,
    normalized_text,
)
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem
from noa_api.whm.tools.preflight_tools import (
    whm_preflight_account,
    whm_preflight_csf_entries,
)


class _WHMTemplate(WorkflowTemplate):
    def build_before_state(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        preflight_results: list[dict[str, object]],
    ) -> list[dict[str, str]] | None:
        _ = args
        return _extract_before_state(preflight_results)


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
        conclusion_step_status = "pending"

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
            conclusion_step_status = "completed"
        elif context.phase == "denied":
            approval_step_status = "cancelled"
            execute_step_status = "cancelled"
            postflight_step_status = "cancelled"
            conclusion_step_status = "completed"
        elif context.phase == "failed":
            approval_step_status = "completed"
            execute_step_status = "cancelled"
            postflight_step_status = "cancelled"
            conclusion_step_status = "completed"

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
            {
                "content": _conclusion_step_content(
                    tool_name=context.tool_name,
                    subject=subject,
                    reason=reason,
                    before_account=before_account,
                    after_account=after_account,
                    result=context.result,
                    phase=context.phase,
                    error_code=context.error_code,
                ),
                "status": cast(Any, conclusion_step_status),
                "priority": "high",
            },
        ]

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
                    action_label="changing the contact email", reason=reason
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
            {
                "content": _contact_email_conclusion_step_content(
                    subject=subject,
                    reason=reason,
                    requested_email=new_email,
                    before_account=before_account,
                    after_account=after_account,
                    result=context.result,
                    phase=context.phase,
                    error_code=context.error_code,
                ),
                "status": cast(Any, statuses["conclusion"]),
                "priority": "high",
            },
        ]

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


class WHMCSFBatchTemplate(_WHMTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        targets = normalized_string_list(context.args.get("targets"))
        subject = _csf_subject(context.args)
        reason = normalized_text(context.args.get("reason"))
        before_entries = _matching_csf_preflight_entries(
            preflight_evidence=context.preflight_evidence,
            args=context.args,
        )
        postflight_entries = _postflight_csf_entries(context.postflight_result)
        statuses = _default_step_statuses(reason=reason, phase=context.phase)
        preflight_complete = len(before_entries) == len(targets) and len(targets) > 0

        return [
            {
                "content": _csf_preflight_step_content(
                    subject=subject, entries=before_entries, targets=targets
                ),
                "status": "completed" if preflight_complete else "in_progress",
                "priority": "high",
            },
            {
                "content": _reason_step_content(
                    action_label=_csf_action_phrase(context.tool_name), reason=reason
                ),
                "status": cast(Any, statuses["reason"]),
                "priority": "high",
            },
            {
                "content": f"Request approval to {_csf_action_phrase(context.tool_name)} for {subject}.",
                "status": cast(Any, statuses["approval"]),
                "priority": "high",
            },
            {
                "content": f"Execute {_csf_action_phrase(context.tool_name)} for {subject}.",
                "status": cast(Any, statuses["execute"]),
                "priority": "high",
            },
            {
                "content": _csf_postflight_step_content(
                    tool_name=context.tool_name,
                    subject=subject,
                    entries=postflight_entries,
                    postflight_result=context.postflight_result,
                ),
                "status": cast(Any, statuses["postflight"]),
                "priority": "high",
            },
            {
                "content": _csf_conclusion_step_content(
                    tool_name=context.tool_name,
                    subject=subject,
                    reason=reason,
                    before_entries=before_entries,
                    after_entries=postflight_entries,
                    result=context.result,
                    phase=context.phase,
                    error_code=context.error_code,
                ),
                "status": cast(Any, statuses["conclusion"]),
                "priority": "high",
            },
        ]

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        return f"{_csf_activity_phrase(tool_name)} '{_format_argument_value(args.get('targets'))}'"

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_csf_preflight(
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
            result = await whm_preflight_csf_entries(
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
    "whm-csf-batch-change": WHMCSFBatchTemplate(),
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


def _require_csf_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_targets = normalized_string_list(args.get("targets"))
    if requested_server_ref is None or not requested_targets:
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_csf_entries"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required WHM preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_csf_entries for each target with the same server_ref before requesting this change.",
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
        error="Required WHM preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            "Missing successful whm_preflight_csf_entries results for target(s): "
            + ", ".join(f"'{target}'" for target in missing_targets),
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


def _extract_email(text: str) -> str | None:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match is not None else None


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
        if tool_name == "whm_preflight_csf_entries":
            verdict = result.get("verdict")
            target = result.get("target")
            if target not in (None, ""):
                before_state.append(
                    {"label": "Target", "value": _format_argument_value(target)}
                )
            if verdict not in (None, ""):
                before_state.append(
                    {
                        "label": "Current CSF state",
                        "value": _format_argument_value(verdict),
                    }
                )
            matches = result.get("matches")
            if isinstance(matches, list) and matches:
                before_state.append(
                    {
                        "label": "Matched entries",
                        "value": "; ".join(
                            _format_argument_value(match) for match in matches[:3]
                        ),
                    }
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
        "conclusion": "pending",
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
        statuses["conclusion"] = "completed"
    elif phase == "denied":
        statuses["approval"] = "cancelled"
        statuses["execute"] = "cancelled"
        statuses["postflight"] = "cancelled"
        statuses["conclusion"] = "completed"
    elif phase == "failed":
        statuses["approval"] = "completed"
        statuses["execute"] = "cancelled"
        statuses["postflight"] = "cancelled"
        statuses["conclusion"] = "completed"
    if reason is None and phase in {"completed", "denied", "failed"}:
        statuses["reason"] = "cancelled"
    return statuses


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


def _csf_action_phrase(tool_name: str) -> str:
    mapping = {
        "whm_csf_unblock": "remove CSF blocks",
        "whm_csf_allowlist_remove": "remove CSF allowlist entries",
        "whm_csf_allowlist_add_ttl": "add temporary CSF allowlist entries",
        "whm_csf_denylist_add_ttl": "add temporary CSF denylist entries",
    }
    return mapping.get(tool_name, "apply the CSF change")


def _csf_activity_phrase(tool_name: str) -> str:
    mapping = {
        "whm_csf_unblock": "Remove CSF block for",
        "whm_csf_allowlist_remove": "Remove",
        "whm_csf_allowlist_add_ttl": "Add",
        "whm_csf_denylist_add_ttl": "Add",
    }
    return mapping.get(tool_name, "Apply CSF change for")


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


def _reason_step_content(*, action_label: str, reason: str | None) -> str:
    if reason is None:
        return f"Ask for reason if missing before {action_label}ing the account."
    return f"Reason captured for the {action_label}: {reason}."


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


def _csf_subject(args: dict[str, object]) -> str:
    targets = normalized_string_list(args.get("targets"))
    server_ref = normalized_text(args.get("server_ref")) or "the server"
    if not targets:
        return f"the requested targets on '{server_ref}'"
    return f"{', '.join(repr(target) for target in targets)} on '{server_ref}'"


def _csf_preflight_step_content(
    *, subject: str, entries: list[dict[str, object]], targets: list[str]
) -> str:
    if not entries:
        return f"Account lookup / preflight for {subject}."
    seen_targets = {normalized_text(entry.get("target")) for entry in entries}
    summaries = [
        f"{entry.get('target')}: {entry.get('verdict')}"
        for entry in entries
        if normalized_text(entry.get("target")) is not None
    ]
    missing = [target for target in targets if target not in seen_targets]
    if missing:
        summaries.append("missing: " + ", ".join(missing))
    return f"Account lookup / preflight for {subject}: {'; '.join(summaries)}."


def _csf_postflight_step_content(
    *,
    tool_name: str,
    subject: str,
    entries: list[dict[str, object]],
    postflight_result: dict[str, object] | None,
) -> str:
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
        "whm_csf_unblock": "not blocked",
        "whm_csf_allowlist_remove": "not allowlisted",
        "whm_csf_allowlist_add_ttl": "allowlisted",
        "whm_csf_denylist_add_ttl": "blocked",
    }
    expected = expectations.get(tool_name, "updated")
    summaries = [
        f"{entry.get('target')}: expected {expected}, observed {entry.get('verdict')}"
        for entry in entries
        if normalized_text(entry.get("target")) is not None
    ]
    return f"Postflight verification for {subject}: {'; '.join(summaries)}."


def _csf_conclusion_step_content(
    *,
    tool_name: str,
    subject: str,
    reason: str | None,
    before_entries: list[dict[str, object]],
    after_entries: list[dict[str, object]],
    result: dict[str, object] | None,
    phase: WorkflowTemplatePhase,
    error_code: str | None,
) -> str:
    _ = tool_name
    reason_suffix = f" Reason: {reason}." if reason is not None else ""
    if phase == "waiting_on_user":
        return f"Conclusion with before/after CSF evidence for {subject} after the reason is provided."
    if phase == "waiting_on_approval":
        return f"Conclusion with before/after CSF evidence for {subject} after approval and execution.{reason_suffix}"
    if phase == "executing":
        return f"Conclusion for {subject} after execution and CSF postflight verification.{reason_suffix}"
    if phase == "denied":
        return f"Conclusion: approval denied for {subject}; no CSF change executed.{reason_suffix}"
    if phase == "failed":
        return f"Conclusion: CSF change for {subject} did not complete successfully (error: {error_code or 'tool_execution_failed'}).{reason_suffix}"
    result_items = _result_items(result)
    changed = [
        item.get("target") for item in result_items if item.get("status") == "changed"
    ]
    noop = [
        item.get("target") for item in result_items if item.get("status") == "no-op"
    ]
    before_summary = _csf_entries_summary(before_entries)
    after_summary = _csf_entries_summary(after_entries)
    parts: list[str] = [f"Before: {before_summary}.", f"After: {after_summary}."]
    if changed:
        parts.append("Changed: " + ", ".join(str(item) for item in changed if item))
    if noop:
        parts.append("No-op: " + ", ".join(str(item) for item in noop if item))
    return f"Conclusion for {subject}: {' '.join(parts)}{reason_suffix}"


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


def _matching_csf_preflight_entries(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> list[dict[str, object]]:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_targets = set(normalized_string_list(args.get("targets")))
    matches: list[dict[str, object]] = []
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_csf_entries":
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


def _postflight_csf_entries(
    postflight_result: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not isinstance(postflight_result, dict):
        return []
    results = postflight_result.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


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


def _result_items(result: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        return []
    items = result.get("results")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _csf_entries_summary(entries: list[dict[str, object]]) -> str:
    if not entries:
        return "no evidence"
    return "; ".join(
        f"{entry.get('target')}={entry.get('verdict')}"
        for entry in entries
        if normalized_text(entry.get("target")) is not None
    )
