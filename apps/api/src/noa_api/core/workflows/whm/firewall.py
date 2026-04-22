from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.approval import (
    approval_detail_rows as _approval_detail_rows,
    approval_presentation_from_reply_data as _approval_presentation_from_reply_data,
    approval_reason_detail as _approval_reason_detail,
)
from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowEvidenceSection,
    WorkflowEvidenceTemplate,
    WorkflowReplyTemplate,
    WorkflowTemplateContext,
    normalized_string_list,
    normalized_text,
)
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem
from noa_api.whm.tools.firewall_tools import whm_preflight_firewall_entries

from noa_api.core.workflows.whm.base import _WHMTemplate
from noa_api.core.workflows.whm.common import (
    _approval_sentence_summary,
    _clean_items,
    _default_step_statuses,
    _format_argument_value,
    _result_error_code,
    _result_items,
    _result_ok,
    _targets_with_status,
)
from noa_api.core.workflows.whm.matching import (
    _matching_firewall_preflight_entries,
    _postflight_firewall_entries,
    _require_firewall_preflight,
)
from noa_api.core.workflows.whm.todo_helpers import (
    _firewall_postflight_step_content,
    _firewall_preflight_step_content,
    _reason_step_content,
)


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
        "Ask the user for a reason\u2014an osTicket/reference number or a brief "
        f"description\u2014{suffix}"
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
            items.append(WorkflowEvidenceItem(label=f"{target} \u00b7 CSF", value=csf_value))

    imunify = entry.get("imunify")
    if isinstance(imunify, dict):
        imunify_value = _firewall_imunify_receipt_value(imunify, target=target)
        if imunify_value is not None:
            items.append(
                WorkflowEvidenceItem(label=f"{target} \u00b7 Imunify", value=imunify_value)
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
