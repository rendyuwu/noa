from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.json_safety import json_safe
from noa_api.core.tools.registry import get_tool_definition
from noa_api.storage.postgres.workflow_todos import (
    SQLWorkflowTodoRepository,
    WorkflowTodoItem,
    WorkflowTodoService,
)
from noa_api.whm.tools.preflight_tools import (
    whm_preflight_account,
    whm_preflight_csf_entries,
)

WorkflowTemplatePhase = Literal[
    "waiting_on_user",
    "waiting_on_approval",
    "executing",
    "completed",
    "denied",
    "failed",
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


def get_workflow_family(
    tool_name: str, *, workflow_family: str | None = None
) -> str | None:
    if workflow_family is not None:
        return workflow_family
    tool = get_tool_definition(tool_name)
    if tool is None:
        return None
    return tool.workflow_family


def build_workflow_todos(
    *,
    tool_name: str,
    workflow_family: str | None = None,
    args: dict[str, object],
    phase: WorkflowTemplatePhase,
    preflight_evidence: list[dict[str, object]],
    result: dict[str, object] | None = None,
    postflight_result: dict[str, object] | None = None,
    error_code: str | None = None,
) -> list[WorkflowTodoItem] | None:
    family = get_workflow_family(tool_name, workflow_family=workflow_family)
    context = WorkflowTemplateContext(
        tool_name=tool_name,
        args=args,
        phase=phase,
        preflight_evidence=preflight_evidence,
        result=result,
        postflight_result=postflight_result,
        error_code=error_code,
    )
    if family == "whm-account-lifecycle":
        return _build_whm_account_lifecycle_workflow(context)
    if family == "whm-account-contact-email":
        return _build_whm_account_contact_email_workflow(context)
    if family == "whm-csf-batch-change":
        return _build_whm_csf_batch_workflow(context)
    return None


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
    if get_workflow_family(tool_name, workflow_family=workflow_family) not in {
        "whm-account-lifecycle",
        "whm-account-contact-email",
        "whm-csf-batch-change",
    }:
        return None

    family = get_workflow_family(tool_name, workflow_family=workflow_family)

    server_ref = _normalized_text(args.get("server_ref"))
    if server_ref is None:
        return None
    if family in {"whm-account-lifecycle", "whm-account-contact-email"}:
        username = _normalized_text(args.get("username"))
        if username is None:
            return None
        result = await whm_preflight_account(
            session=session,
            server_ref=server_ref,
            username=username,
        )
        return result if isinstance(result, dict) else None

    targets = _normalized_string_list(args.get("targets"))
    if not targets:
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


def _build_whm_account_lifecycle_workflow(
    context: WorkflowTemplateContext,
) -> list[WorkflowTodoItem]:
    subject = _account_subject(context.tool_name, context.args)
    action_label = _action_label(context.tool_name)
    before_account = _matching_account_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_account = _postflight_account(context.postflight_result)
    reason = _normalized_text(context.args.get("reason"))

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
            "content": _reason_step_content(action_label=action_label, reason=reason),
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


def _build_whm_account_contact_email_workflow(
    context: WorkflowTemplateContext,
) -> list[WorkflowTodoItem]:
    subject = _account_subject(context.tool_name, context.args)
    before_account = _matching_account_preflight(
        preflight_evidence=context.preflight_evidence,
        args=context.args,
    )
    after_account = _postflight_account(context.postflight_result)
    reason = _normalized_text(context.args.get("reason"))
    new_email = _normalized_text(context.args.get("new_email"))

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


def _build_whm_csf_batch_workflow(
    context: WorkflowTemplateContext,
) -> list[WorkflowTodoItem]:
    targets = _normalized_string_list(context.args.get("targets"))
    subject = _csf_subject(context.args)
    reason = _normalized_text(context.args.get("reason"))
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


def _account_subject(tool_name: str, args: dict[str, object]) -> str:
    username = _normalized_text(args.get("username")) or "the account"
    server_ref = _normalized_text(args.get("server_ref"))
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


def _preflight_step_content(
    *, subject: str, before_account: dict[str, object] | None
) -> str:
    if before_account is None:
        return f"Account lookup / preflight for {subject}."

    state = _account_state(before_account)
    details: list[str] = [f"state: {state}"]
    domain = _normalized_text(before_account.get("domain"))
    if domain is not None:
        details.append(f"domain: {domain}")
    contact = _normalized_text(before_account.get("contactemail"))
    if contact is not None:
        details.append(f"contact: {contact}")
    suspend_reason = _normalized_text(before_account.get("suspendreason"))
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
                _normalized_text(postflight_result.get("error_code")) or "unknown"
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
        _normalized_text(result.get("status")) if isinstance(result, dict) else None
    )
    if result_status == "no-op":
        return f"Conclusion: no-op for {subject}. Contact email remained '{before_email}'.{reason_suffix}"
    return f"Conclusion: contact email for {subject} moved from '{before_email}' to '{after_email}'.{reason_suffix}"


def _csf_subject(args: dict[str, object]) -> str:
    targets = _normalized_string_list(args.get("targets"))
    server_ref = _normalized_text(args.get("server_ref")) or "the server"
    if not targets:
        return f"the requested targets on '{server_ref}'"
    return f"{', '.join(repr(target) for target in targets)} on '{server_ref}'"


def _csf_preflight_step_content(
    *, subject: str, entries: list[dict[str, object]], targets: list[str]
) -> str:
    if not entries:
        return f"Account lookup / preflight for {subject}."
    seen_targets = {_normalized_text(entry.get("target")) for entry in entries}
    summaries = [
        f"{entry.get('target')}: {entry.get('verdict')}"
        for entry in entries
        if _normalized_text(entry.get("target")) is not None
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
                _normalized_text(postflight_result.get("error_code")) or "unknown"
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
        if _normalized_text(entry.get("target")) is not None
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
                _normalized_text(postflight_result.get("error_code")) or "unknown"
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
        _normalized_text(result.get("status")) if isinstance(result, dict) else None
    )
    if result_status == "no-op":
        return f"Conclusion: no-op for {subject}. Before state: {before_text}. After state: {after_text}.{reason_suffix}"
    return f"Conclusion: {subject} moved from {before_text} to {after_text}.{reason_suffix}"


def _matching_account_preflight(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> dict[str, object] | None:
    requested_server_ref = _normalized_text(args.get("server_ref"))
    requested_username = _normalized_text(args.get("username"))
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_account":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if _normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if _normalized_text(account.get("user")) != requested_username:
            continue
        return account
    return None


def _matching_csf_preflight_entries(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> list[dict[str, object]]:
    requested_server_ref = _normalized_text(args.get("server_ref"))
    requested_targets = set(_normalized_string_list(args.get("targets")))
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
        if _normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        target = _normalized_text(result.get("target"))
        if target is None or target not in requested_targets:
            continue
        matches.append(result)
    matches.sort(key=lambda entry: _normalized_text(entry.get("target")) or "")
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
    contact = _normalized_text(account.get("contactemail"))
    if contact is not None:
        return contact
    return _normalized_text(account.get("email"))


def _normalized_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = _normalized_text(item)
        if text is not None:
            normalized.append(text)
    return normalized


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
        if _normalized_text(entry.get("target")) is not None
    )


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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
