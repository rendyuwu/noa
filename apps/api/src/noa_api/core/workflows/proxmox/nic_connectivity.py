from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.approval import (
    approval_detail_rows as _approval_detail_rows,
    approval_presentation_from_reply_data as _approval_presentation_from_reply_data,
    approval_reason_detail as _approval_reason_detail,
)
from noa_api.core.workflows.proxmox.common import (
    _action_completed_label,
    _action_label,
    _action_outcome_adjective,
    _action_verb,
    _approval_action_label,
    _desired_link_state,
    _link_state,
    _normalized_int,
    _postflight_verified,
    _reason_step_content,
    _subject,
    _title_subject,
    _workflow_result_failed,
)
from noa_api.core.workflows.proxmox.matching import (
    _matching_preflight,
    _require_vm_nic_preflight,
)
from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowEvidenceSection,
    WorkflowEvidenceTemplate,
    WorkflowReplyTemplate,
    WorkflowTemplate,
    WorkflowTemplateContext,
    normalized_text,
)
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem


class ProxmoxVMNicConnectivityTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        action_label = _action_label(context.tool_name)
        subject = _subject(context.args)
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )
        before_state = _matching_preflight(
            context.preflight_evidence,
            context.args,
            requested_server_id=normalized_text(postflight.get("server_id"))
            or normalized_text(result.get("server_id")),
        )
        reason = normalized_text(context.args.get("reason"))

        reason_status = "completed" if reason is not None else "pending"
        approval_status = "pending"
        execute_status = "pending"
        verify_status = "pending"

        if context.phase == "waiting_on_user":
            reason_status = "waiting_on_user"
        if context.phase == "waiting_on_approval":
            approval_status = "waiting_on_approval"
        elif context.phase == "executing":
            reason_status = "completed"
            approval_status = "completed"
            execute_status = "in_progress"
        elif context.phase == "completed":
            reason_status = "completed"
            approval_status = "completed"
            execute_status = "completed"
            verify_status = (
                "completed"
                if _verification_confirmed(context.tool_name, result, postflight)
                else "cancelled"
            )
        elif context.phase == "denied":
            approval_status = "cancelled"
            execute_status = "cancelled"
            verify_status = "cancelled"
        elif context.phase == "failed":
            approval_status = "completed"
            execute_status = "cancelled"
            verify_status = "cancelled"

        if reason is None and context.phase in {"completed", "denied", "failed"}:
            reason_status = "cancelled"

        return [
            {
                "content": _preflight_content(
                    subject=subject, before_state=before_state
                ),
                "status": "completed" if before_state is not None else "in_progress",
                "priority": "high",
            },
            {
                "content": _reason_step_content(
                    action_label=action_label,
                    action_verb=_action_verb(context.tool_name),
                    reason=reason,
                ),
                "status": reason_status,
                "priority": "high",
            },
            {
                "content": f"Request approval to {action_label} {subject}.",
                "status": approval_status,
                "priority": "high",
            },
            {
                "content": f"Apply the Proxmox NIC configuration change for {subject}.",
                "status": execute_status,
                "priority": "high",
            },
            {
                "content": _verification_content(context=context),
                "status": verify_status,
                "priority": "high",
            },
        ]

    def build_reply_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowReplyTemplate | None:
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )
        before_state = _matching_preflight(
            context.preflight_evidence,
            context.args,
            requested_server_id=normalized_text(postflight.get("server_id"))
            or normalized_text(result.get("server_id")),
        )
        subject = _subject(context.args)
        title_subject = _title_subject(context.args)
        desired_state = _desired_link_state(context.tool_name)
        current_state = _link_state(before_state)
        after_state = _final_link_state(result, postflight, fallback=desired_state)
        failed_result = _workflow_result_failed(result)

        if context.phase == "waiting_on_approval":
            summary = f"{subject} is currently link {current_state or 'unknown'} and is ready to be moved to link {desired_state}."
            details = _approval_detail_rows(
                (
                    "Action",
                    f"{_approval_action_label(context.tool_name)} {subject}.",
                ),
                (
                    "Reason",
                    _approval_reason_detail(
                        normalized_text(context.args.get("reason"))
                    ),
                ),
                (
                    "Success criteria",
                    f"{subject} ends in link state {desired_state}.",
                ),
            )
            approval_evidence_summary = _evidence_summary(
                tool_name=context.tool_name,
                before_state=before_state,
                result=result,
                postflight_result=postflight,
            )
            return WorkflowReplyTemplate(
                title=f"Approve {_action_verb(context.tool_name)} {title_subject}",
                outcome="info",
                summary=summary,
                evidence_summary=[],
                approval_presentation=_approval_presentation_from_reply_data(
                    paragraph=None,
                    details=details,
                    evidence_summary=approval_evidence_summary,
                ),
                details=details,
                next_step=f"Approve the request to {_action_label(context.tool_name)} {subject}.",
            )

        if context.phase == "completed":
            status = normalized_text(result.get("status"))
            if status == "no-op":
                return WorkflowReplyTemplate(
                    title=f"{title_subject} already {_action_outcome_adjective(context.tool_name)}",
                    outcome="no_op",
                    summary=(
                        f"{subject} was already link {desired_state}; no Proxmox config change was required."
                    ),
                    evidence_summary=_evidence_summary(
                        tool_name=context.tool_name,
                        before_state=before_state,
                        result=result,
                        postflight_result=postflight,
                    ),
                    next_step="Run preflight again before requesting another NIC state change.",
                )

            if failed_result:
                if _postflight_verified(context.tool_name, postflight):
                    return WorkflowReplyTemplate(
                        title=f"{_action_completed_label(context.tool_name)} {title_subject}",
                        outcome="partial",
                        summary=(
                            f"{subject} reported a failure, but postflight verification confirmed the NIC finished in link state {_link_state(postflight) or _desired_link_state(context.tool_name)}."
                        ),
                        evidence_summary=_evidence_summary(
                            tool_name=context.tool_name,
                            before_state=before_state,
                            result=result,
                            postflight_result=postflight,
                        ),
                        next_step="Use a fresh preflight before making another VM NIC change.",
                    )
                return WorkflowReplyTemplate(
                    title=f"Failed to {_action_verb(context.tool_name)} {title_subject}",
                    outcome="failed",
                    summary=(
                        f"The request to {_action_label(context.tool_name)} {subject} did not complete successfully."
                    ),
                    evidence_summary=_evidence_summary(
                        tool_name=context.tool_name,
                        before_state=before_state,
                        result=result,
                        postflight_result=postflight,
                    ),
                    next_step="Run proxmox_preflight_vm_nic_toggle again to refresh the digest before retrying.",
                )

            return WorkflowReplyTemplate(
                title=f"{_action_completed_label(context.tool_name)} {title_subject}",
                outcome="changed",
                summary=(
                    f"{subject} moved from link {current_state or 'unknown'} to link {after_state}, and {_verification_summary_sentence(context.tool_name, result, postflight)}."
                ),
                evidence_summary=_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Use the new digest from a fresh preflight before making another VM NIC change.",
            )

        if context.phase == "denied":
            return WorkflowReplyTemplate(
                title=f"Denied {_action_verb(context.tool_name)} {title_subject}",
                outcome="denied",
                summary=(
                    f"Approval was denied, so {subject} remains link {current_state or 'unknown'}."
                ),
                evidence_summary=_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="If the change is still needed, rerun preflight and request approval again.",
            )

        if context.phase == "failed":
            return WorkflowReplyTemplate(
                title=f"Failed to {_action_verb(context.tool_name)} {title_subject}",
                outcome="failed",
                summary=(
                    f"The request to {_action_label(context.tool_name)} {subject} did not complete successfully."
                ),
                evidence_summary=_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Run proxmox_preflight_vm_nic_toggle again to refresh the digest before retrying.",
            )

        return None

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )
        before_state = _matching_preflight(
            context.preflight_evidence,
            context.args,
            requested_server_id=normalized_text(postflight.get("server_id"))
            or normalized_text(result.get("server_id")),
        )

        before_items = _before_state_items(before_state)
        after_items = _after_state_items(result, postflight)
        verification_items = _verification_items(context.tool_name, result, postflight)

        return WorkflowEvidenceTemplate(
            sections=[
                WorkflowEvidenceSection(
                    key="before_state",
                    title="Before state",
                    items=before_items,
                ),
                WorkflowEvidenceSection(
                    key="requested_change",
                    title="Requested change",
                    items=[
                        WorkflowEvidenceItem(
                            label="Action", value=_action_label(context.tool_name)
                        ),
                        WorkflowEvidenceItem(
                            label="Target", value=_subject(context.args)
                        ),
                        WorkflowEvidenceItem(
                            label="Requested digest",
                            value=normalized_text(context.args.get("digest")) or "none",
                        ),
                        WorkflowEvidenceItem(
                            label="Reason",
                            value=normalized_text(context.args.get("reason"))
                            or "none provided",
                        ),
                    ],
                ),
                WorkflowEvidenceSection(
                    key="after_state",
                    title="After state",
                    items=after_items,
                ),
                WorkflowEvidenceSection(
                    key="verification",
                    title="Verification",
                    items=verification_items,
                ),
            ]
        )

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        return f"{_action_label(tool_name).capitalize()} {_subject(args)}"

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_vm_nic_preflight(
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
        import noa_api.core.workflows.proxmox as _pkg

        _ = tool_name
        server_ref = normalized_text(args.get("server_ref"))
        node = normalized_text(args.get("node"))
        net = normalized_text(args.get("net"))
        vmid = _normalized_int(args.get("vmid"))
        if server_ref is None or node is None or net is None or vmid is None:
            return None
        result = await _pkg.proxmox_preflight_vm_nic_toggle(
            session=session,
            server_ref=server_ref,
            node=node,
            vmid=vmid,
            net=net,
        )
        return result if isinstance(result, dict) else None


def _preflight_content(*, subject: str, before_state: dict[str, object] | None) -> str:
    if before_state is None:
        return f"Read the current Proxmox NIC state for {subject}."
    link_state = _link_state(before_state) or "unknown"
    digest = normalized_text(before_state.get("digest")) or "unknown"
    return f"Read the current Proxmox NIC state for {subject}: link {link_state}, digest {digest}."


def _verification_content(context: WorkflowTemplateContext) -> str:
    result = context.result if isinstance(context.result, dict) else {}
    postflight = (
        context.postflight_result if isinstance(context.postflight_result, dict) else {}
    )
    verified = _verification_confirmed(context.tool_name, result, postflight)
    if context.phase == "completed" and verified:
        final_state = _final_link_state(
            result,
            postflight,
            fallback=_desired_link_state(context.tool_name),
        )
        return f"Verify that the NIC finished in link state {final_state}."
    return "Poll the task and verify the NIC link state after the change."


def _before_state_items(
    before_state: dict[str, object] | None,
) -> list[WorkflowEvidenceItem]:
    if not isinstance(before_state, dict):
        return [
            WorkflowEvidenceItem(
                label="Status", value="No matching preflight evidence yet"
            )
        ]

    return [
        WorkflowEvidenceItem(
            label="Selected NIC",
            value=normalized_text(before_state.get("net")) or "unknown",
        ),
        WorkflowEvidenceItem(
            label="Current link state",
            value=_link_state(before_state) or "unknown",
        ),
        WorkflowEvidenceItem(
            label="Digest",
            value=normalized_text(before_state.get("digest")) or "unknown",
        ),
        WorkflowEvidenceItem(
            label="Current NIC config",
            value=normalized_text(before_state.get("before_net")) or "unknown",
        ),
    ]


def _after_state_items(
    result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    after_net = normalized_text(postflight_result.get("before_net")) or normalized_text(
        result.get("after_net")
    )
    link_state = _final_link_state(result, postflight_result)
    return [
        WorkflowEvidenceItem(label="Link state", value=link_state or "pending"),
        WorkflowEvidenceItem(label="NIC config", value=after_net or "pending"),
    ]


def _verification_items(
    tool_name: str, result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    verified = (
        "yes" if _verification_confirmed(tool_name, result, postflight_result) else "no"
    )
    items = [
        WorkflowEvidenceItem(label="Verified", value=verified),
        WorkflowEvidenceItem(
            label="Task status",
            value=normalized_text(result.get("task_status")) or "none",
        ),
        WorkflowEvidenceItem(
            label="Task exit status",
            value=normalized_text(result.get("task_exit_status")) or "none",
        ),
        WorkflowEvidenceItem(
            label="UPID",
            value=normalized_text(result.get("upid")) or "none",
        ),
    ]

    postflight_digest = normalized_text(postflight_result.get("digest"))
    if postflight_digest is not None:
        items.append(
            WorkflowEvidenceItem(label="Current digest", value=postflight_digest)
        )
    return items


def _evidence_summary(
    *,
    tool_name: str,
    before_state: dict[str, object] | None,
    result: dict[str, object],
    postflight_result: dict[str, object],
) -> list[str]:
    current_state = _link_state(before_state)
    final_state = _final_link_state(result, postflight_result)
    summary: list[str] = []
    if current_state is not None:
        summary.append(f"Before: link {current_state}.")
    if final_state is not None:
        summary.append(f"After: link {final_state}.")
    if normalized_text(result.get("message")) is not None:
        summary.append(f"Tool result: {normalized_text(result.get('message'))}.")
    if result.get("verified") is True and _verification_confirmed(
        tool_name, result, postflight_result
    ):
        summary.append("Verification succeeded.")
    elif result.get("verified") is not True and _postflight_verified(
        tool_name, postflight_result
    ):
        summary.append("Postflight verification succeeded.")
    return summary


def _verification_summary_sentence(
    tool_name: str,
    result: dict[str, object],
    postflight_result: dict[str, object],
) -> str:
    if result.get("verified") is True and _verification_confirmed(
        tool_name, result, postflight_result
    ):
        return "verification succeeded"
    if result.get("verified") is not True and _postflight_verified(
        tool_name, postflight_result
    ):
        return "postflight verification succeeded"
    return "verification is not confirmed"


def _final_link_state(
    result: dict[str, object],
    postflight_result: dict[str, object],
    *,
    fallback: str | None = None,
) -> str | None:
    return _link_state(postflight_result) or _link_state(result) or fallback


def _verification_confirmed(
    tool_name: str,
    result: dict[str, object],
    postflight_result: dict[str, object],
) -> bool:
    if result.get("verified") is True:
        if tool_name in {"proxmox_disable_vm_nic", "proxmox_enable_vm_nic"}:
            desired_state = _desired_link_state(tool_name)
            result_state = _link_state(result)
            postflight_state = _link_state(postflight_result)
            if result_state != desired_state:
                return False
            if postflight_result and not _postflight_verified(
                tool_name, postflight_result
            ):
                return False
            if postflight_state is not None and postflight_state != desired_state:
                return False
        return True
    return _postflight_verified(tool_name, postflight_result)
