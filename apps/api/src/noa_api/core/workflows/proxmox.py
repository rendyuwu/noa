from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

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
from noa_api.proxmox.tools.nic_tools import proxmox_preflight_vm_nic_toggle
from noa_api.storage.postgres.workflow_todos import WorkflowTodoItem


class ProxmoxVMNicConnectivityTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        action_label = _action_label(context.tool_name)
        subject = _subject(context.args)
        before_state = _matching_preflight(context.preflight_evidence, context.args)

        approval_status = "pending"
        execute_status = "pending"
        verify_status = "pending"

        if context.phase == "waiting_on_approval":
            approval_status = "waiting_on_approval"
        elif context.phase == "executing":
            approval_status = "completed"
            execute_status = "in_progress"
        elif context.phase == "completed":
            approval_status = "completed"
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

        return [
            {
                "content": _preflight_content(
                    subject=subject, before_state=before_state
                ),
                "status": "completed" if before_state is not None else "in_progress",
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

        if context.phase == "waiting_on_approval":
            return WorkflowReplyTemplate(
                title=f"Approve {_action_verb(context.tool_name)} {title_subject}",
                outcome="info",
                summary=(
                    f"{subject} is currently link {current_state or 'unknown'} and is ready to be moved to link {desired_state}."
                ),
                evidence_summary=_evidence_summary(
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

            return WorkflowReplyTemplate(
                title=f"{_action_completed_label(context.tool_name)} {title_subject}",
                outcome="changed",
                summary=(
                    f"{subject} moved from link {current_state or 'unknown'} to link {after_state}, and verification succeeded."
                ),
                evidence_summary=_evidence_summary(
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
        if normalized_text(item_args.get("server_ref")) != requested_server_ref:
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
    if context.phase == "completed" and verified:
        final_state = _link_state(result) or _desired_link_state(context.tool_name)
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
    verified = "yes" if result.get("verified") is True else "no"
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
    return summary


WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {
    "proxmox-vm-nic-connectivity": ProxmoxVMNicConnectivityTemplate(),
}
