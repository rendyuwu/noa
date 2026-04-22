from __future__ import annotations

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
    WorkflowInference,
    WorkflowReplyTemplate,
    WorkflowTemplateContext,
    normalized_text,
)
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem
from noa_api.whm.tools.preflight_tools import collect_primary_domain_change_state

from noa_api.core.workflows.whm.base import _WHMTemplate
from noa_api.core.workflows.whm.common import (
    _account_domain,
    _account_subject,
    _clean_items,
    _default_step_statuses,
    _dns_zone_exists,
    _domain_inventory,
    _domain_owner,
    _render_domain_list,
    _requested_domain_location,
    _result_error_code,
    _result_message,
    _result_ok,
    _result_status,
)
from noa_api.core.workflows.whm.matching import (
    _matching_primary_domain_preflight,
    _postflight_account,
    _primary_domain_preflight_candidates,
    _require_primary_domain_preflight,
)
from noa_api.core.workflows.whm.inference import (
    _extract_domain,
    _latest_user_text,
    _select_primary_domain_preflight_candidate,
)
from noa_api.core.workflows.whm.todo_helpers import (
    _primary_domain_postflight_step_content,
    _primary_domain_preflight_step_content,
    _reason_step_content,
)


def _build_primary_domain_reply_template(
    context: WorkflowTemplateContext,
) -> WorkflowReplyTemplate | None:
    return _build_primary_domain_reply_template_impl(context)


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
