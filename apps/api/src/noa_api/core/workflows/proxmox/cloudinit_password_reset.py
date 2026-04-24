from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.approval import (
    approval_detail_rows as _approval_detail_rows,
    approval_presentation_from_reply_data as _approval_presentation_from_reply_data,
    approval_reason_detail as _approval_reason_detail,
)
from noa_api.core.workflows.proxmox.common import (
    _normalized_int,
    _pool_value,
    _postflight_verified,
    _reason_step_content,
    _workflow_result_failed,
)
from noa_api.core.workflows.proxmox.matching import (
    _matching_cloudinit_preflight,
    _require_cloudinit_preflight,
)
from noa_api.core.workflows.proxmox.postflight import (
    _cloudinit_postflight_result,
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


class ProxmoxVMCloudinitPasswordResetTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        subject = _cloudinit_subject(context.args)
        before_state = _matching_cloudinit_preflight(
            context.preflight_evidence, context.args
        )
        reason = normalized_text(context.args.get("reason"))

        preflight_status = "completed" if before_state is not None else "in_progress"
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
                if _cloudinit_verified(
                    context.tool_name, context.result, context.postflight_result
                )
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
                "content": _cloudinit_preflight_content(
                    subject=subject, before_state=before_state
                ),
                "status": preflight_status,
                "priority": "high",
            },
            {
                "content": _reason_step_content(
                    action_label="reset cloud-init password",
                    action_verb="reset",
                    missing_reason_text=(
                        "Ask the user for a reason—an osTicket/reference number or a brief "
                        "description—before resetting the cloud-init password."
                    ),
                    reason=reason,
                ),
                "status": reason_status,
                "priority": "high",
            },
            {
                "content": f"Request approval to reset the cloud-init password for {subject}.",
                "status": approval_status,
                "priority": "high",
            },
            {
                "content": f"Reset the cloud-init password for {subject} and regenerate cloud-init.",
                "status": execute_status,
                "priority": "high",
            },
            {
                "content": _cloudinit_verification_content(context=context),
                "status": verify_status,
                "priority": "high",
            },
        ]

    def build_reply_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowReplyTemplate | None:
        before_state = _matching_cloudinit_preflight(
            context.preflight_evidence, context.args
        )
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )
        subject = _cloudinit_subject(context.args)
        failed_result = _workflow_result_failed(result)

        if context.phase == "waiting_on_approval":
            summary = (
                f"Cloud-init password reset requested for {subject}.\n\n"
                f"{_cloudinit_approval_summary(before_state=before_state)}"
            )
            details = _approval_detail_rows(
                (
                    "Action",
                    f"Reset the cloud-init password for {subject}.",
                ),
                (
                    "Reason",
                    _approval_reason_detail(
                        normalized_text(context.args.get("reason"))
                    ),
                ),
                (
                    "Success criteria",
                    "The cloud-init password reset completes and the regenerated state "
                    f"is available for {subject}.",
                ),
            )
            approval_evidence_summary = _cloudinit_evidence_summary(
                tool_name=context.tool_name,
                before_state=before_state,
                result=result,
                postflight_result=postflight,
            )
            return WorkflowReplyTemplate(
                title="Approve cloud-init password reset",
                outcome="info",
                summary=summary,
                evidence_summary=[],
                approval_presentation=_approval_presentation_from_reply_data(
                    paragraph=None,
                    details=details,
                    evidence_summary=approval_evidence_summary,
                ),
                details=details,
                next_step="Approve the request to reset the cloud-init password.",
            )

        if context.phase == "completed" and failed_result:
            if _cloudinit_verified(context.tool_name, result, postflight):
                return WorkflowReplyTemplate(
                    title="Cloud-init password reset partially completed",
                    outcome="partial",
                    summary=(
                        f"The request to reset the cloud-init password for {subject} reported a failure, but postflight verification confirmed the regenerated state is available."
                    ),
                    evidence_summary=_cloudinit_evidence_summary(
                        tool_name=context.tool_name,
                        before_state=before_state,
                        result=result,
                        postflight_result=postflight,
                    ),
                    next_step="Use a fresh preflight before retrying the reset.",
                )
            return WorkflowReplyTemplate(
                title="Cloud-init password reset failed",
                outcome="failed",
                summary=(
                    f"The request to reset the cloud-init password for {subject} did not complete successfully."
                ),
                evidence_summary=_cloudinit_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Run proxmox_preflight_vm_cloudinit_password_reset again before retrying.",
            )

        if context.phase == "denied":
            return WorkflowReplyTemplate(
                title="Cloud-init password reset denied",
                outcome="denied",
                summary=(
                    f"Approval was denied, so the cloud-init password for {subject} was not changed."
                ),
                evidence_summary=_cloudinit_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="If the reset is still needed, rerun preflight and request approval again.",
            )

        if context.phase == "failed":
            return WorkflowReplyTemplate(
                title="Cloud-init password reset failed",
                outcome="failed",
                summary=(
                    f"The request to reset the cloud-init password for {subject} did not complete successfully."
                ),
                evidence_summary=_cloudinit_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Run proxmox_preflight_vm_cloudinit_password_reset again before retrying.",
            )

        if context.phase == "completed":
            return WorkflowReplyTemplate(
                title="Cloud-init password reset completed",
                outcome="changed",
                summary=(
                    f"Cloud-init password reset completed for {subject}. The new password may not take effect immediately and may require a VM restart or stop/start cycle.\n\n"
                    f"{_cloudinit_completion_summary(before_state=before_state, result=result, postflight_result=postflight)}"
                ),
                evidence_summary=_cloudinit_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="If the guest still shows the old password, restart or stop/start the VM before trying again.",
            )

        return None

    async def fetch_postflight_result(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        session: AsyncSession,
    ) -> dict[str, object] | None:
        import noa_api.core.workflows.proxmox as _pkg

        _ = tool_name
        resolved = await _pkg._resolve_proxmox_client(
            session=session, server_ref=args.get("server_ref")
        )
        if resolved is None:
            return None
        if isinstance(resolved, dict):
            return resolved

        client, server_id = resolved
        node = normalized_text(args.get("node"))
        vmid = _normalized_int(args.get("vmid"))
        if node is None or vmid is None:
            return None

        cloudinit_verified = await _cloudinit_postflight_result(
            client=client,
            node=node,
            vmid=vmid,
            new_password=normalized_text(args.get("new_password")),
        )
        if cloudinit_verified.get("ok") is not True:
            return cloudinit_verified

        return {
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": server_id,
            "node": node,
            "vmid": vmid,
            "cloudinit": cloudinit_verified["cloudinit"],
            "cloudinit_dump_user": cloudinit_verified["cloudinit_dump_user"],
            "verified": True,
        }

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        before_state = _matching_cloudinit_preflight(
            context.preflight_evidence, context.args
        )
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )

        return WorkflowEvidenceTemplate(
            sections=[
                WorkflowEvidenceSection(
                    key="before_state",
                    title="Before state",
                    items=_cloudinit_before_state_items(before_state),
                ),
                WorkflowEvidenceSection(
                    key="requested_change",
                    title="Requested change",
                    items=[
                        WorkflowEvidenceItem(
                            label="Action", value="Reset cloud-init password"
                        ),
                        WorkflowEvidenceItem(
                            label="Target", value=_cloudinit_subject(context.args)
                        ),
                        WorkflowEvidenceItem(
                            label="Reason",
                            value=normalized_text(context.args.get("reason"))
                            or "none provided",
                        ),
                        WorkflowEvidenceItem(label="Password supplied", value="yes"),
                    ],
                ),
                WorkflowEvidenceSection(
                    key="after_state",
                    title="After state",
                    items=_cloudinit_after_state_items(result, postflight),
                ),
                WorkflowEvidenceSection(
                    key="verification",
                    title="Verification",
                    items=_cloudinit_verification_items(
                        context.tool_name, result, postflight
                    ),
                ),
            ]
        )

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        _ = tool_name
        return f"Reset cloud-init password for {_cloudinit_subject(args)}"

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_cloudinit_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )


def _cloudinit_subject(args: dict[str, object]) -> str:
    node = normalized_text(args.get("node")) or "unknown-node"
    vmid = _normalized_int(args.get("vmid"))
    vmid_text = str(vmid) if vmid is not None else "unknown-vmid"
    return f"VM {vmid_text} on node {node}"


def _cloudinit_approval_summary(before_state: dict[str, object] | None) -> str:
    if before_state is None:
        return "Matching preflight evidence has not been captured yet."
    digest = (
        normalized_text((before_state.get("config") or {}).get("digest"))
        if isinstance(before_state.get("config"), dict)
        else None
    )
    cloudinit = (
        before_state.get("cloudinit")
        if isinstance(before_state.get("cloudinit"), dict)
        else None
    )
    cloudinit_ok = "yes" if isinstance(cloudinit, dict) else "unknown"
    return f"Preflight captured for the exact VM. Config digest: {digest or 'unknown'}. Cloud-init state available: {cloudinit_ok}."


def _cloudinit_completion_summary(
    *,
    before_state: dict[str, object] | None,
    result: dict[str, object],
    postflight_result: dict[str, object],
) -> str:
    before_digest = None
    if isinstance(before_state, dict):
        config = before_state.get("config")
        if isinstance(config, dict):
            before_digest = normalized_text(config.get("digest"))
    parts: list[str] = []
    if before_digest is not None:
        parts.append(f"Before config digest: {before_digest}.")
    return " ".join(parts) if parts else "Cloud-init state was refreshed and verified."


def _cloudinit_preflight_content(
    *,
    subject: str,
    before_state: dict[str, object] | None,
) -> str:
    if before_state is None:
        return f"Read the current Proxmox cloud-init preflight state for {subject}."
    config = before_state.get("config") if isinstance(before_state, dict) else None
    digest = normalized_text(config.get("digest")) if isinstance(config, dict) else None
    return f"Read the current Proxmox cloud-init preflight state for {subject}: digest {digest or 'unknown'}."


def _cloudinit_verification_content(context: WorkflowTemplateContext) -> str:
    if context.phase == "completed" and _cloudinit_verified(
        context.tool_name, context.result, context.postflight_result
    ):
        return "Verify that the cloud-init password reset completed and the regenerated state is available."
    return "Poll the task and verify the cloud-init password reset after the change."


def _cloudinit_before_state_items(
    before_state: dict[str, object] | None,
) -> list[WorkflowEvidenceItem]:
    if not isinstance(before_state, dict):
        return [
            WorkflowEvidenceItem(
                label="Status", value="No matching preflight evidence yet"
            )
        ]

    config = (
        before_state.get("config")
        if isinstance(before_state.get("config"), dict)
        else {}
    )
    cloudinit = (
        before_state.get("cloudinit")
        if isinstance(before_state.get("cloudinit"), dict)
        else {}
    )
    return [
        WorkflowEvidenceItem(label="Node", value=_pool_value(before_state.get("node"))),
        WorkflowEvidenceItem(
            label="VMID",
            value=str(before_state.get("vmid"))
            if isinstance(before_state.get("vmid"), int)
            else "unknown",
        ),
        WorkflowEvidenceItem(
            label="Config digest",
            value=normalized_text(config.get("digest")) or "unknown",
        ),
        WorkflowEvidenceItem(
            label="Cloud-init state",
            value="available" if cloudinit else "unknown",
        ),
    ]


def _cloudinit_after_state_items(
    result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    return [
        WorkflowEvidenceItem(
            label="Password task",
            value="completed" if result.get("set_password_task") else "pending",
        ),
        WorkflowEvidenceItem(
            label="Cloud-init regenerated",
            value="yes" if result.get("regenerate_cloudinit") else "no",
        ),
        WorkflowEvidenceItem(
            label="Cloud-init verified",
            value="yes"
            if _cloudinit_verified(
                "proxmox_reset_vm_cloudinit_password", result, postflight_result
            )
            else "no",
        ),
    ]


def _cloudinit_verification_items(
    tool_name: str, result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    items = [
        WorkflowEvidenceItem(
            label="Verified",
            value=(
                "yes"
                if _cloudinit_verified(tool_name, result, postflight_result)
                else "no"
            ),
        ),
        WorkflowEvidenceItem(
            label="Task status",
            value=(
                normalized_text(
                    (result.get("set_password_task") or {}).get("task_status")
                )
                or "none"
            )
            if isinstance(result.get("set_password_task"), dict)
            else "none",
        ),
        WorkflowEvidenceItem(
            label="Task exit status",
            value=(
                normalized_text(
                    (result.get("set_password_task") or {}).get("task_exit_status")
                )
                or "none"
            )
            if isinstance(result.get("set_password_task"), dict)
            else "none",
        ),
        WorkflowEvidenceItem(
            label="UPID",
            value=(
                normalized_text((result.get("set_password_task") or {}).get("data"))
                or "none"
            )
            if isinstance(result.get("set_password_task"), dict)
            else "none",
        ),
    ]
    return items


def _cloudinit_verified(
    tool_name: str,
    result: dict[str, object] | None,
    postflight_result: dict[str, object] | None,
) -> bool:
    """Consistent verification: postflight disagreement downgrades inline success."""
    postflight = postflight_result if isinstance(postflight_result, dict) else {}
    r = result if isinstance(result, dict) else {}
    # If postflight ran and explicitly failed, downgrade even if inline said verified
    if postflight and postflight.get("ok") is False:
        return False
    if _postflight_verified(tool_name, postflight_result):
        return True
    return r.get("verified") is True and not postflight


def _cloudinit_evidence_summary(
    *,
    tool_name: str,
    before_state: dict[str, object] | None,
    result: dict[str, object],
    postflight_result: dict[str, object],
) -> list[str]:
    summary: list[str] = []
    if isinstance(before_state, dict):
        vmid = before_state.get("vmid")
        summary.append(
            f"Before: VM {str(vmid) if isinstance(vmid, int) and not isinstance(vmid, bool) else 'unknown'} on {normalized_text(before_state.get('node')) or 'unknown-node'}."
        )
    if _cloudinit_verified(tool_name, result, postflight_result):
        if result.get("verified") is True:
            summary.append("Verification succeeded.")
        else:
            summary.append("Postflight verification succeeded.")
    return summary
