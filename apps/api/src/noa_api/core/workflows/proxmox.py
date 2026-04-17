from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowEvidenceSection,
    WorkflowEvidenceTemplate,
    WorkflowReplyTemplate,
    WorkflowTemplate,
    WorkflowTemplateContext,
    collect_recent_preflight_evidence,
    normalized_text,
)
from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository
from noa_api.proxmox.tools.nic_tools import proxmox_preflight_vm_nic_toggle
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem


class ProxmoxVMNicConnectivityTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        action_label = _action_label(context.tool_name)
        subject = _subject(context.args)
        before_state = _matching_preflight(context.preflight_evidence, context.args)
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
                if _postflight_verified(context.tool_name, context.postflight_result)
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
        before_state = _matching_preflight(context.preflight_evidence, context.args)
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )
        subject = _subject(context.args)
        title_subject = _title_subject(context.args)
        desired_state = _desired_link_state(context.tool_name)
        current_state = _link_state(before_state)
        after_state = _link_state(result) or _link_state(postflight) or desired_state
        failed_result = _workflow_result_failed(result)

        if context.phase == "waiting_on_approval":
            return WorkflowReplyTemplate(
                title=f"Approve {_action_verb(context.tool_name)} {title_subject}",
                outcome="info",
                summary=(
                    f"{subject} is currently link {current_state or 'unknown'} and is ready to be moved to link {desired_state}."
                ),
                evidence_summary=_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
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
                    f"{subject} moved from link {current_state or 'unknown'} to link {after_state}, and verification succeeded."
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
        before_state = _matching_preflight(context.preflight_evidence, context.args)
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )

        before_items = _before_state_items(before_state)
        after_items = _after_state_items(result, postflight)
        verification_items = _verification_items(result, postflight)

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
        _ = tool_name
        server_ref = normalized_text(args.get("server_ref"))
        node = normalized_text(args.get("node"))
        net = normalized_text(args.get("net"))
        vmid = _normalized_int(args.get("vmid"))
        if server_ref is None or node is None or net is None or vmid is None:
            return None
        result = await proxmox_preflight_vm_nic_toggle(
            session=session,
            server_ref=server_ref,
            node=node,
            vmid=vmid,
            net=net,
        )
        return result if isinstance(result, dict) else None


def _normalized_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _action_label(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "enable VM NIC"
    return "disable VM NIC"


def _desired_link_state(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "up"
    return "down"


def _action_verb(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "enable"
    return "disable"


def _action_completed_label(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "Enabled"
    return "Disabled"


def _action_outcome_adjective(tool_name: str) -> str:
    if tool_name == "proxmox_enable_vm_nic":
        return "enabled"
    return "disabled"


def _subject(args: dict[str, object]) -> str:
    node = normalized_text(args.get("node")) or "unknown-node"
    vmid = _normalized_int(args.get("vmid"))
    net = normalized_text(args.get("net")) or "unknown-net"
    vmid_text = str(vmid) if vmid is not None else "unknown-vmid"
    return f"VM {vmid_text} NIC {net} on node {node}"


def _title_subject(args: dict[str, object]) -> str:
    node = normalized_text(args.get("node")) or "unknown-node"
    vmid = _normalized_int(args.get("vmid"))
    net = normalized_text(args.get("net")) or "unknown-net"
    vmid_text = str(vmid) if vmid is not None else "unknown-vmid"
    return f"VM {vmid_text} {net} on {node}"


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


def _server_identity_matches_any(
    *,
    item_args: dict[str, object],
    result: dict[str, object],
    requested_server_ref: str,
    requested_server_id: str | None,
) -> bool:
    result_server_id = normalized_text(result.get("server_id"))
    item_server_ref = normalized_text(item_args.get("server_ref"))
    if requested_server_id is not None and result_server_id is not None:
        return (
            result_server_id == requested_server_id
            or item_server_ref == requested_server_ref
        )
    if item_server_ref == requested_server_ref:
        return True
    return result_server_id is not None and result_server_id == requested_server_ref


def _matching_preflight(
    preflight_evidence: list[dict[str, object]],
    args: dict[str, object],
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_net = normalized_text(args.get("net"))
    requested_digest = normalized_text(args.get("digest"))
    requested_vmid = _normalized_int(args.get("vmid"))

    if (
        requested_server_ref is None
        or requested_node is None
        or requested_net is None
        or requested_vmid is None
    ):
        return None

    for item in reversed(preflight_evidence):
        if item.get("toolName") != "proxmox_preflight_vm_nic_toggle":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=None,
        ):
            continue
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        if normalized_text(result.get("net")) != requested_net:
            continue
        if (
            requested_digest is not None
            and normalized_text(result.get("digest")) != requested_digest
        ):
            continue
        return result

    return None


def _require_vm_nic_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_net = normalized_text(args.get("net"))
    requested_digest = normalized_text(args.get("digest"))
    requested_vmid = _normalized_int(args.get("vmid"))
    if (
        requested_server_ref is None
        or requested_node is None
        or requested_net is None
        or requested_digest is None
        or requested_vmid is None
    ):
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "proxmox_preflight_vm_nic_toggle"
        and isinstance(item.get("result"), dict)
        and item["result"].get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required Proxmox preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run proxmox_preflight_vm_nic_toggle with the same server_ref, node, vmid, net, and digest before requesting this change.",
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
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        if normalized_text(result.get("net")) != requested_net:
            continue
        if normalized_text(result.get("digest")) != requested_digest:
            continue
        return None

    return SanitizedToolError(
        error="Required Proxmox preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful proxmox_preflight_vm_nic_toggle was found for server_ref '{requested_server_ref}', node '{requested_node}', vmid '{requested_vmid}', net '{requested_net}', and digest '{requested_digest}' in the current turn.",
        ),
    )


def _link_state(source: dict[str, object] | None) -> str | None:
    if not isinstance(source, dict):
        return None
    return normalized_text(source.get("link_state"))


def _preflight_content(*, subject: str, before_state: dict[str, object] | None) -> str:
    if before_state is None:
        return f"Read the current Proxmox NIC state for {subject}."
    link_state = _link_state(before_state) or "unknown"
    digest = normalized_text(before_state.get("digest")) or "unknown"
    return f"Read the current Proxmox NIC state for {subject}: link {link_state}, digest {digest}."


def _verification_content(context: WorkflowTemplateContext) -> str:
    result = context.result if isinstance(context.result, dict) else {}
    verified = result.get("verified") is True
    if context.phase == "completed" and (
        verified or _postflight_verified(context.tool_name, context.postflight_result)
    ):
        final_state = _link_state(result) or _desired_link_state(context.tool_name)
        return f"Verify that the NIC finished in link state {final_state}."
    return "Poll the task and verify the NIC link state after the change."


def _postflight_verified(*args: object) -> bool:
    if len(args) == 1:
        tool_name = None
        postflight_result = args[0]
    elif len(args) == 2:
        tool_name = args[0] if isinstance(args[0], str) else None
        postflight_result = args[1]
    else:
        raise TypeError("_postflight_verified expects 1 or 2 positional arguments")
    if not isinstance(postflight_result, dict):
        return False
    if tool_name in {"proxmox_disable_vm_nic", "proxmox_enable_vm_nic"}:
        desired_state = _desired_link_state(tool_name)
        return (
            postflight_result.get("ok") is True
            and _link_state(postflight_result) == desired_state
        )
    return postflight_result.get("verified") is True


def _reason_step_content(
    *,
    action_label: str,
    action_verb: str,
    reason: str | None,
    missing_reason_text: str | None = None,
) -> str:
    if reason is None:
        if missing_reason_text is not None:
            return missing_reason_text
        gerund = "enabling" if action_verb == "enable" else "disabling"
        return f"Ask for a short reason before {gerund} the VM NIC."
    return f"Reason captured for the {action_label} change: {reason}."


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
    after_net = normalized_text(result.get("after_net")) or normalized_text(
        postflight_result.get("before_net")
    )
    link_state = _link_state(result) or _link_state(postflight_result)
    return [
        WorkflowEvidenceItem(label="Link state", value=link_state or "pending"),
        WorkflowEvidenceItem(label="NIC config", value=after_net or "pending"),
    ]


def _verification_items(
    result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    verified = (
        "yes"
        if result.get("verified") is True or _postflight_verified(postflight_result)
        else "no"
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
    final_state = _link_state(result) or _link_state(postflight_result)
    summary: list[str] = []
    if current_state is not None:
        summary.append(f"Before: link {current_state}.")
    if final_state is not None:
        summary.append(f"After: link {final_state}.")
    if normalized_text(result.get("message")) is not None:
        summary.append(f"Tool result: {normalized_text(result.get('message'))}.")
    if result.get("verified") is True:
        summary.append("Verification succeeded.")
    elif _postflight_verified(tool_name, postflight_result):
        summary.append("Postflight verification succeeded.")
    return summary


class ProxmoxVMCloudinitPasswordResetTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        subject = _cloudinit_subject(context.args)
        before_state = _matching_cloudinit_preflight(
            context.preflight_evidence, context.args
        )
        reason = normalized_text(context.args.get("reason"))
        failed_result = _workflow_result_failed(context.result)

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
            if failed_result:
                execute_status = "completed"
                verify_status = (
                    "completed"
                    if _postflight_verified(context.postflight_result)
                    else "cancelled"
                )
            else:
                execute_status = "completed"
                verify_status = "completed"
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
                    missing_reason_text="Ask for a short reason before resetting the cloud-init password.",
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
            return WorkflowReplyTemplate(
                title="Approve cloud-init password reset",
                outcome="info",
                summary=(
                    f"Cloud-init password reset requested for {subject}.\n\n"
                    f"{_cloudinit_approval_summary(before_state=before_state)}"
                ),
                evidence_summary=_cloudinit_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Approve the request to reset the cloud-init password.",
            )

        if context.phase == "completed" and failed_result:
            if _postflight_verified(context.tool_name, postflight):
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
        _ = tool_name
        resolved = await _resolve_proxmox_client(
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
            client=client, node=node, vmid=vmid
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
                    items=_cloudinit_verification_items(result, postflight),
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


class ProxmoxPoolMembershipMoveTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        subject = _pool_move_subject(context.args)
        before_state = _matching_pool_move_preflight(
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
                if _postflight_verified(context.tool_name, context.postflight_result)
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
                "content": _pool_move_preflight_content(
                    subject=subject, before_state=before_state
                ),
                "status": preflight_status,
                "priority": "high",
            },
            {
                "content": _reason_step_content(
                    action_label="move pool membership",
                    action_verb="move",
                    missing_reason_text="Ask for a short reason before moving pool membership.",
                    reason=reason,
                ),
                "status": reason_status,
                "priority": "high",
            },
            {
                "content": f"Request approval to move {subject}.",
                "status": approval_status,
                "priority": "high",
            },
            {
                "content": f"Move the requested VMIDs from the source pool to the destination pool for {subject}.",
                "status": execute_status,
                "priority": "high",
            },
            {
                "content": _pool_move_verification_content(context=context),
                "status": verify_status,
                "priority": "high",
            },
        ]

    def build_reply_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowReplyTemplate | None:
        before_state = _matching_pool_move_preflight(
            context.preflight_evidence, context.args
        )
        result = context.result if isinstance(context.result, dict) else {}
        postflight = (
            context.postflight_result
            if isinstance(context.postflight_result, dict)
            else {}
        )
        subject = _pool_move_subject(context.args)
        failed_result = _workflow_result_failed(result)

        if context.phase == "waiting_on_approval":
            return WorkflowReplyTemplate(
                title="Approve Proxmox pool membership move",
                outcome="info",
                summary=(
                    f"Pool membership move requested for {subject}.\n\n"
                    f"{_pool_move_approval_summary(before_state=before_state, args=context.args)}"
                ),
                evidence_summary=_pool_move_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Approve the request to move the VMIDs between pools.",
            )

        if context.phase == "completed" and failed_result:
            if _postflight_verified(context.tool_name, postflight):
                return WorkflowReplyTemplate(
                    title="Proxmox pool membership move partially completed",
                    outcome="partial",
                    summary=(
                        f"The request to move {subject} reported a failure, but postflight verification confirmed the requested VMIDs are in the destination pool."
                    ),
                    evidence_summary=_pool_move_evidence_summary(
                        before_state=before_state,
                        result=result,
                        postflight_result=postflight,
                    ),
                    next_step="Use a fresh preflight before retrying the pool move.",
                )
            return WorkflowReplyTemplate(
                title="Proxmox pool membership move failed",
                outcome="failed",
                summary=(
                    f"The request to move {subject} did not complete successfully."
                ),
                evidence_summary=_pool_move_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Run proxmox_preflight_move_vms_between_pools again before retrying.",
            )

        if context.phase == "denied":
            return WorkflowReplyTemplate(
                title="Proxmox pool membership move denied",
                outcome="denied",
                summary=(f"Approval was denied, so {subject} was not changed."),
                evidence_summary=_pool_move_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="If the move is still needed, rerun preflight and request approval again.",
            )

        if context.phase == "failed":
            return WorkflowReplyTemplate(
                title="Proxmox pool membership move failed",
                outcome="failed",
                summary=(
                    f"The request to move {subject} did not complete successfully."
                ),
                evidence_summary=_pool_move_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Run proxmox_preflight_move_vms_between_pools again before retrying.",
            )

        if context.phase == "completed":
            return WorkflowReplyTemplate(
                title="Proxmox pool membership move completed",
                outcome="changed",
                summary=(
                    f"Pool membership move completed for {subject}.\n\n"
                    f"{_pool_move_completion_summary(before_state=before_state, result=result, postflight_result=postflight, args=context.args)}"
                ),
                evidence_summary=_pool_move_evidence_summary(
                    tool_name=context.tool_name,
                    before_state=before_state,
                    result=result,
                    postflight_result=postflight,
                ),
                next_step="Review both pools and confirm the moved VMIDs now appear in the destination pool.",
            )

        return None

    async def fetch_postflight_result(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        session: AsyncSession,
    ) -> dict[str, object] | None:
        _ = tool_name
        resolved = await _resolve_proxmox_client(
            session=session, server_ref=args.get("server_ref")
        )
        if resolved is None:
            return None
        if isinstance(resolved, dict):
            return resolved

        client, server_id = resolved
        source_pool = normalized_text(args.get("source_pool"))
        destination_pool = normalized_text(args.get("destination_pool"))
        if source_pool is None or destination_pool is None:
            return None

        pool_state = await _pool_postflight_result(
            client=client,
            source_pool=source_pool,
            destination_pool=destination_pool,
            vmids=_normalized_int_list(args.get("vmids")),
        )
        if pool_state.get("ok") is not True:
            return pool_state

        return {
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": server_id,
            "source_pool_after": pool_state["source_pool_after"],
            "destination_pool_after": pool_state["destination_pool_after"],
            "verified": True,
        }

    def build_evidence_template(
        self,
        context: WorkflowTemplateContext,
    ) -> WorkflowEvidenceTemplate | None:
        before_state = _matching_pool_move_preflight(
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
                    items=_pool_move_before_state_items(before_state),
                ),
                WorkflowEvidenceSection(
                    key="requested_change",
                    title="Requested change",
                    items=[
                        WorkflowEvidenceItem(
                            label="Source pool",
                            value=_pool_value(context.args.get("source_pool")),
                        ),
                        WorkflowEvidenceItem(
                            label="Destination pool",
                            value=_pool_value(context.args.get("destination_pool")),
                        ),
                        WorkflowEvidenceItem(
                            label="VMIDs", value=_vmids_text(context.args.get("vmids"))
                        ),
                        WorkflowEvidenceItem(
                            label="Target user",
                            value=_pool_value(context.args.get("email")),
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
                    items=_pool_move_after_state_items(result, postflight),
                ),
                WorkflowEvidenceSection(
                    key="verification",
                    title="Verification",
                    items=_pool_move_verification_items(result, postflight),
                ),
            ]
        )

    def describe_activity(
        self, *, tool_name: str, args: dict[str, object]
    ) -> str | None:
        _ = tool_name
        return f"Move pool membership for {_pool_move_subject(args)}"

    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_pool_move_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )


def _cloudinit_subject(args: dict[str, object]) -> str:
    node = normalized_text(args.get("node")) or "unknown-node"
    vmid = _normalized_int(args.get("vmid"))
    vmid_text = str(vmid) if vmid is not None else "unknown-vmid"
    return f"VM {vmid_text} on node {node}"


async def _resolve_proxmox_client(
    *, session: AsyncSession, server_ref: object
) -> tuple[ProxmoxClient, str] | dict[str, object] | None:
    server_ref_text = normalized_text(server_ref)
    if server_ref_text is None:
        return None

    repo: Any = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref_text, repo=repo)
    if not resolution.ok:
        return {
            "ok": False,
            "error_code": str(resolution.error_code or "unknown"),
            "message": str(resolution.message or "Proxmox server lookup failed"),
        }

    server = resolution.server
    if server is None or resolution.server_id is None:
        return None

    client = ProxmoxClient(
        base_url=str(getattr(server, "base_url")),
        api_token_id=str(getattr(server, "api_token_id")),
        api_token_secret=maybe_decrypt_text(str(getattr(server, "api_token_secret"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )
    return client, str(resolution.server_id)


async def _cloudinit_postflight_result(
    *, client: ProxmoxClient, node: str, vmid: int
) -> dict[str, object] | None:
    verification_result = await _wait_for_cloudinit_verification(
        client=client, node=node, vmid=vmid
    )
    if verification_result.get("ok") is not True:
        return verification_result
    return {
        "ok": True,
        "cloudinit": verification_result["cloudinit"],
        "cloudinit_dump_user": verification_result["cloudinit_dump_user"],
        "verified": True,
    }


async def _pool_postflight_result(
    *,
    client: ProxmoxClient,
    source_pool: str,
    destination_pool: str,
    vmids: list[int],
) -> dict[str, object] | None:
    source_pool_after = await client.get_pool(source_pool)
    if source_pool_after.get("ok") is not True:
        return _upstream_error(
            source_pool_after,
            fallback_message="Unable to fetch the source pool after the move",
        )
    destination_pool_after = await client.get_pool(destination_pool)
    if destination_pool_after.get("ok") is not True:
        return _upstream_error(
            destination_pool_after,
            fallback_message="Unable to fetch the destination pool after the move",
        )
    try:
        source_vmids_after = _pool_result_vmids(source_pool_after)
        destination_vmids_after = _pool_result_vmids(destination_pool_after)
    except ValueError:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected pool payload",
        }
    if not all(
        vmid not in source_vmids_after and vmid in destination_vmids_after
        for vmid in vmids
    ):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox pool move verification did not confirm the requested VMIDs",
        }
    return {
        "ok": True,
        "message": "ok",
        "verified": True,
        "source_pool_after": source_pool_after,
        "destination_pool_after": destination_pool_after,
    }


async def _wait_for_cloudinit_verification(
    *,
    client: ProxmoxClient,
    node: str,
    vmid: int,
) -> dict[str, object]:
    cloudinit_result = await client.get_qemu_cloudinit(node, vmid)
    if cloudinit_result.get("ok") is not True:
        return _upstream_error(
            cloudinit_result,
            fallback_message="Unable to verify Proxmox cloud-init values",
        )

    dump_result = await client.get_qemu_cloudinit_dump_user(node, vmid)
    if dump_result.get("ok") is not True:
        return _upstream_error(
            dump_result,
            fallback_message="Unable to verify Proxmox cloud-init user dump",
        )

    sanitized_dump, confirmed = _sanitize_cloudinit_dump_user(dump_result.get("data"))
    if not confirmed or sanitized_dump is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    if not _cloudinit_confirms_password_reset(cloudinit_result):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        }

    return {
        "ok": True,
        "cloudinit": cloudinit_result,
        "cloudinit_dump_user": {**dump_result, "data": sanitized_dump},
        "verified": True,
    }


def _upstream_error(
    result: dict[str, object], *, fallback_message: str
) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or fallback_message),
    }


def _cloudinit_confirms_password_reset(result: dict[str, object]) -> bool:
    data = result.get("data")
    if isinstance(data, dict):
        return normalized_text(data.get("cipassword")) is not None
    if not isinstance(data, list):
        return False
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if normalized_text(entry.get("key")) != "cipassword":
            continue
        if normalized_text(entry.get("value")) is not None:
            return True
    return False


def _sanitize_cloudinit_dump_user(dump_value: object) -> tuple[str | None, bool]:
    if not isinstance(dump_value, str):
        return None, False

    dump_text = dump_value.strip()
    if not dump_text:
        return None, False

    sanitized_lines: list[str] = []
    found_password = False
    for line in dump_text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("password:"):
            sanitized_lines.append(line)
            continue

        value = stripped[len("password:") :].strip()
        if not value:
            return None, False

        leading = line[: len(line) - len(stripped)]
        sanitized_lines.append(f"{leading}password: [REDACTED]")
        found_password = True

    if not found_password:
        return None, False

    sanitized = "\n".join(sanitized_lines)
    if dump_value.endswith("\n"):
        sanitized += "\n"
    return sanitized, True


def _pool_result_vmids(result: dict[str, object]) -> set[int]:
    vmids: set[int] = set()
    for member in _pool_members_from_result(result):
        vmid = member.get("vmid")
        if isinstance(vmid, int) and not isinstance(vmid, bool):
            vmids.add(vmid)
    return vmids


def _pool_move_subject(args: dict[str, object]) -> str:
    source_pool = normalized_text(args.get("source_pool")) or "unknown-source-pool"
    destination_pool = (
        normalized_text(args.get("destination_pool")) or "unknown-destination-pool"
    )
    vmids_text = _vmids_text(args.get("vmids"))
    return f"VMIDs {vmids_text} from {source_pool} to {destination_pool}"


def _workflow_result_failed(result: dict[str, object] | None) -> bool:
    return isinstance(result, dict) and result.get("ok") is False


def _vmids_text(value: object) -> str:
    vmids = _normalized_int_list(value)
    if not vmids:
        return "unknown-vmids"
    return ", ".join(str(vmid) for vmid in vmids)


def _normalized_int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    vmids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        vmids.append(item)
    return vmids


def _pool_value(value: object) -> str:
    return normalized_text(value) or "unknown"


def _pool_members_from_result(
    result: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if isinstance(data, list):
        members: list[dict[str, object]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            entry_members = entry.get("members")
            if not isinstance(entry_members, list):
                continue
            for member in entry_members:
                if isinstance(member, dict):
                    members.append(member)
        return members
    members = result.get("members")
    if isinstance(members, list):
        return [member for member in members if isinstance(member, dict)]
    return []


def _pool_table(title: str, result: dict[str, object] | None) -> str:
    rows = _pool_members_from_result(result)
    lines = [title, "", "| VMID | Name | Node | Status |", "| --- | --- | --- | --- |"]
    if not rows:
        lines.append("| — | — | — | — |")
        return "\n".join(lines)

    for member in rows:
        vmid = member.get("vmid")
        vmid_text = (
            str(vmid)
            if isinstance(vmid, int) and not isinstance(vmid, bool)
            else "unknown"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    vmid_text,
                    _pool_value(member.get("name")),
                    _pool_value(member.get("node")),
                    _pool_value(member.get("status")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


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
    after_digest = None
    cloudinit = (
        result.get("cloudinit") if isinstance(result.get("cloudinit"), dict) else None
    )
    if isinstance(cloudinit, dict):
        after_digest = normalized_text(cloudinit.get("digest"))
    postflight_digest = _cloudinit_digest_from_postflight(postflight_result)
    parts: list[str] = []
    if before_digest is not None:
        parts.append(f"Before config digest: {before_digest}.")
    if after_digest is not None:
        parts.append(f"After cloud-init digest: {after_digest}.")
    if postflight_digest is not None:
        parts.append(f"Verified digest: {postflight_digest}.")
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
    result = context.result if isinstance(context.result, dict) else {}
    verified = result.get("verified") is True
    if context.phase == "completed" and verified:
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
    cloudinit = (
        result.get("cloudinit") if isinstance(result.get("cloudinit"), dict) else {}
    )
    postflight_cloudinit = (
        postflight_result.get("cloudinit")
        if isinstance(postflight_result.get("cloudinit"), dict)
        else {}
    )
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
            value="yes" if result.get("verified") is True else "no",
        ),
        WorkflowEvidenceItem(
            label="Current digest",
            value=normalized_text(cloudinit.get("digest"))
            or normalized_text(postflight_cloudinit.get("digest"))
            or "unknown",
        ),
    ]


def _cloudinit_verification_items(
    result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    items = [
        WorkflowEvidenceItem(
            label="Verified", value="yes" if result.get("verified") is True else "no"
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
    postflight_cloudinit = (
        postflight_result.get("cloudinit")
        if isinstance(postflight_result.get("cloudinit"), dict)
        else None
    )
    if isinstance(postflight_cloudinit, dict):
        items.append(
            WorkflowEvidenceItem(
                label="Current digest",
                value=normalized_text(postflight_cloudinit.get("digest")) or "unknown",
            )
        )
    return items


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
    if result.get("verified") is True:
        summary.append("Verification succeeded.")
    elif _postflight_verified(tool_name, postflight_result):
        summary.append("Postflight verification succeeded.")
    digest = _cloudinit_digest_from_postflight(postflight_result)
    if digest is not None:
        summary.append(f"Current digest: {digest}.")
    return summary


def _cloudinit_digest_from_postflight(
    postflight_result: dict[str, object],
) -> str | None:
    cloudinit = postflight_result.get("cloudinit")
    if not isinstance(cloudinit, dict):
        return None
    return normalized_text(cloudinit.get("digest"))


def _matching_cloudinit_preflight(
    preflight_evidence: list[dict[str, object]],
    args: dict[str, object],
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_vmid = _normalized_int(args.get("vmid"))

    if requested_server_ref is None or requested_node is None or requested_vmid is None:
        return None

    for item in reversed(preflight_evidence):
        if item.get("toolName") != "proxmox_preflight_vm_cloudinit_password_reset":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=None,
        ):
            continue
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        return result

    return None


def _require_cloudinit_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_node = normalized_text(args.get("node"))
    requested_vmid = _normalized_int(args.get("vmid"))
    if requested_server_ref is None or requested_node is None or requested_vmid is None:
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "proxmox_preflight_vm_cloudinit_password_reset"
        and isinstance(item.get("result"), dict)
        and item["result"].get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required Proxmox preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run proxmox_preflight_vm_cloudinit_password_reset with the same server_ref, node, and vmid before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        if normalized_text(result.get("node")) != requested_node:
            continue
        if _normalized_int(result.get("vmid")) != requested_vmid:
            continue
        return None

    return SanitizedToolError(
        error="Required Proxmox preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful proxmox_preflight_vm_cloudinit_password_reset was found for server_ref '{requested_server_ref}', node '{requested_node}', and vmid '{requested_vmid}' in the current turn.",
        ),
    )


def _pool_move_preflight_content(
    *,
    subject: str,
    before_state: dict[str, object] | None,
) -> str:
    if before_state is None:
        return f"Read the current Proxmox pool preflight membership for {subject}."
    return f"Read the current Proxmox pool preflight membership for {subject}."


def _pool_move_verification_content(context: WorkflowTemplateContext) -> str:
    result = context.result if isinstance(context.result, dict) else {}
    if context.phase == "completed" and result.get("verified") is True:
        return "Verify that the VMIDs were removed from the source pool and present in the destination pool."
    return "Poll the task and verify the source and destination pool memberships after the move."


def _pool_move_before_state_items(
    before_state: dict[str, object] | None,
) -> list[WorkflowEvidenceItem]:
    if not isinstance(before_state, dict):
        return [
            WorkflowEvidenceItem(
                label="Status", value="No matching preflight evidence yet"
            )
        ]

    source_pool = (
        before_state.get("source_pool")
        if isinstance(before_state.get("source_pool"), dict)
        else {}
    )
    destination_pool = (
        before_state.get("destination_pool")
        if isinstance(before_state.get("destination_pool"), dict)
        else {}
    )
    return [
        WorkflowEvidenceItem(
            label="Source pool",
            value=_pool_value(
                before_state.get("source_pool")
                and _pool_name(before_state.get("source_pool"))
            ),
        ),
        WorkflowEvidenceItem(
            label="Destination pool",
            value=_pool_value(
                before_state.get("destination_pool")
                and _pool_name(before_state.get("destination_pool"))
            ),
        ),
        WorkflowEvidenceItem(
            label="Source members",
            value=str(len(_pool_members_from_result(source_pool))),
        ),
        WorkflowEvidenceItem(
            label="Destination members",
            value=str(len(_pool_members_from_result(destination_pool))),
        ),
    ]


def _pool_move_after_state_items(
    result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    source_after = (
        result.get("source_pool_after")
        if isinstance(result.get("source_pool_after"), dict)
        else postflight_result.get("source_pool_after")
        if isinstance(postflight_result.get("source_pool_after"), dict)
        else {}
    )
    destination_after = (
        result.get("destination_pool_after")
        if isinstance(result.get("destination_pool_after"), dict)
        else postflight_result.get("destination_pool_after")
        if isinstance(postflight_result.get("destination_pool_after"), dict)
        else {}
    )
    return [
        WorkflowEvidenceItem(
            label="Source members after",
            value=str(
                len(
                    _pool_members_from_result(
                        source_after if isinstance(source_after, dict) else None
                    )
                )
            ),
        ),
        WorkflowEvidenceItem(
            label="Destination members after",
            value=str(
                len(
                    _pool_members_from_result(
                        destination_after
                        if isinstance(destination_after, dict)
                        else None
                    )
                )
            ),
        ),
        WorkflowEvidenceItem(
            label="Move verified",
            value="yes" if result.get("verified") is True else "no",
        ),
    ]


def _pool_move_verification_items(
    result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    items = [
        WorkflowEvidenceItem(
            label="Verified", value="yes" if result.get("verified") is True else "no"
        ),
        WorkflowEvidenceItem(
            label="Add task",
            value=(
                normalized_text((result.get("add_to_destination") or {}).get("data"))
                or "none"
            )
            if isinstance(result.get("add_to_destination"), dict)
            else "none",
        ),
        WorkflowEvidenceItem(
            label="Remove task",
            value=(
                normalized_text((result.get("remove_from_source") or {}).get("data"))
                or "none"
            )
            if isinstance(result.get("remove_from_source"), dict)
            else "none",
        ),
    ]
    if (
        isinstance(postflight_result, dict)
        and postflight_result.get("verified") is True
    ):
        items.append(WorkflowEvidenceItem(label="Postflight", value="verified"))
    return items


def _pool_move_evidence_summary(
    *,
    tool_name: str,
    before_state: dict[str, object] | None,
    result: dict[str, object],
    postflight_result: dict[str, object],
) -> list[str]:
    summary: list[str] = []
    if isinstance(before_state, dict):
        summary.append("Preflight captured for the exact source and destination pools.")
    if result.get("verified") is True:
        summary.append("Verification succeeded.")
    elif _postflight_verified(tool_name, postflight_result):
        summary.append("Postflight verification succeeded.")
    return summary


def _pool_move_approval_summary(
    *,
    before_state: dict[str, object] | None,
    args: dict[str, object],
) -> str:
    source_pool = _pool_value(args.get("source_pool"))
    destination_pool = _pool_value(args.get("destination_pool"))
    vmids = _vmids_text(args.get("vmids"))
    lines = [
        f"Move request for VMIDs {vmids} from {source_pool} to {destination_pool}.",
    ]
    if isinstance(before_state, dict):
        lines.extend(
            [
                _pool_table(
                    "Source pool before",
                    before_state.get("source_pool")
                    if isinstance(before_state.get("source_pool"), dict)
                    else None,
                ),
                _pool_table(
                    "Destination pool before",
                    before_state.get("destination_pool")
                    if isinstance(before_state.get("destination_pool"), dict)
                    else None,
                ),
            ]
        )
    return "\n\n".join(lines)


def _pool_move_completion_summary(
    *,
    before_state: dict[str, object] | None,
    result: dict[str, object],
    postflight_result: dict[str, object],
    args: dict[str, object],
) -> str:
    source_before = (
        before_state.get("source_pool")
        if isinstance(before_state, dict)
        and isinstance(before_state.get("source_pool"), dict)
        else result.get("source_pool_before")
        if isinstance(result.get("source_pool_before"), dict)
        else None
    )
    destination_before = (
        before_state.get("destination_pool")
        if isinstance(before_state, dict)
        and isinstance(before_state.get("destination_pool"), dict)
        else result.get("destination_pool_before")
        if isinstance(result.get("destination_pool_before"), dict)
        else None
    )
    source_after = (
        result.get("source_pool_after")
        if isinstance(result.get("source_pool_after"), dict)
        else postflight_result.get("source_pool_after")
        if isinstance(postflight_result.get("source_pool_after"), dict)
        else None
    )
    destination_after = (
        result.get("destination_pool_after")
        if isinstance(result.get("destination_pool_after"), dict)
        else postflight_result.get("destination_pool_after")
        if isinstance(postflight_result.get("destination_pool_after"), dict)
        else None
    )
    return "\n\n".join(
        [
            _pool_table("Source pool before", source_before),
            _pool_table("Destination pool before", destination_before),
            _pool_table("Source pool after", source_after),
            _pool_table("Destination pool after", destination_after),
            f"Moved VMIDs: {_vmids_text(args.get('vmids'))}.",
        ]
    )


def _matching_pool_move_preflight(
    preflight_evidence: list[dict[str, object]],
    args: dict[str, object],
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_source_pool = normalized_text(args.get("source_pool"))
    requested_destination_pool = normalized_text(args.get("destination_pool"))
    requested_vmids = _normalized_int_list(args.get("vmids"))
    requested_email = normalized_text(args.get("email"))

    if (
        requested_server_ref is None
        or requested_source_pool is None
        or requested_destination_pool is None
        or not requested_vmids
        or requested_email is None
    ):
        return None

    for item in reversed(preflight_evidence):
        if item.get("toolName") != "proxmox_preflight_move_vms_between_pools":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=None,
        ):
            continue
        if normalized_text(item_args.get("source_pool")) != requested_source_pool:
            continue
        if (
            normalized_text(item_args.get("destination_pool"))
            != requested_destination_pool
        ):
            continue
        if _normalized_int_list(item_args.get("vmids")) != requested_vmids:
            continue
        if normalized_text(item_args.get("email")) != requested_email:
            continue
        return result

    return None


def _require_pool_move_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_source_pool = normalized_text(args.get("source_pool"))
    requested_destination_pool = normalized_text(args.get("destination_pool"))
    requested_vmids = _normalized_int_list(args.get("vmids"))
    requested_email = normalized_text(args.get("email"))
    if (
        requested_server_ref is None
        or requested_source_pool is None
        or requested_destination_pool is None
        or not requested_vmids
        or requested_email is None
    ):
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "proxmox_preflight_move_vms_between_pools"
        and isinstance(item.get("result"), dict)
        and item["result"].get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required Proxmox preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run proxmox_preflight_move_vms_between_pools with the same server_ref, source_pool, destination_pool, vmids, and email before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches_any(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        if normalized_text(item_args.get("source_pool")) != requested_source_pool:
            continue
        if (
            normalized_text(item_args.get("destination_pool"))
            != requested_destination_pool
        ):
            continue
        if _normalized_int_list(item_args.get("vmids")) != requested_vmids:
            continue
        if normalized_text(item_args.get("email")) != requested_email:
            continue
        return None

    return SanitizedToolError(
        error="Required Proxmox preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful proxmox_preflight_move_vms_between_pools was found for server_ref '{requested_server_ref}', source_pool '{requested_source_pool}', destination_pool '{requested_destination_pool}', vmids '{requested_vmids}', and email '{requested_email}' in the current turn.",
        ),
    )


def _pool_name(result: dict[str, object] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, list):
        return None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        poolid = normalized_text(entry.get("poolid"))
        if poolid is not None:
            return poolid
    return None


WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {
    "proxmox-vm-nic-connectivity": ProxmoxVMNicConnectivityTemplate(),
    "proxmox-vm-cloudinit-password-reset": ProxmoxVMCloudinitPasswordResetTemplate(),
    "proxmox-pool-membership-move": ProxmoxPoolMembershipMoveTemplate(),
}
