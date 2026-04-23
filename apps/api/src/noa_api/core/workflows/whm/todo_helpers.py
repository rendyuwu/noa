from __future__ import annotations

from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowTemplatePhase,
    normalized_text,
)

from noa_api.core.workflows.whm.common import (
    _account_domain,
    _account_email,
    _account_state,
    _clean_items,
    _dns_zone_exists,
    _domain_inventory,
    _domain_owner,
    _render_domain_list,
    _requested_domain_location,
)


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
