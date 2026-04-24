from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.approval import (
    approval_detail_rows as _approval_detail_rows,
    approval_presentation_from_reply_data as _approval_presentation_from_reply_data,
)
from noa_api.core.workflows.proxmox.common import (
    _approval_table_block,
    _normalized_int_list,
    _pool_value,
    _postflight_verified,
    _reason_step_content,
    _vmids_text,
    _workflow_result_failed,
)
from noa_api.core.workflows.proxmox.matching import (
    _matching_pool_move_preflight,
    _require_pool_move_preflight,
)
from noa_api.core.workflows.proxmox.postflight import (
    _pool_members_from_result,
    _pool_postflight_result,
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


class ProxmoxPoolMembershipMoveTemplate(WorkflowTemplate):
    def build_todos(self, context: WorkflowTemplateContext) -> list[WorkflowTodoItem]:
        subject = _pool_move_subject(context.args)
        before_state = _matching_pool_move_preflight(
            context.preflight_evidence, context.args
        )
        result = context.result if isinstance(context.result, dict) else {}
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
                if _pool_move_verified(
                    context.tool_name, result, context.postflight_result
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
                    missing_reason_text=(
                        "Ask the user for a reason—an osTicket/reference number or a brief "
                        "description—before moving pool membership."
                    ),
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
                "content": _pool_move_verification_content(
                    context=context,
                    result=result,
                    postflight_result=context.postflight_result,
                ),
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
            details = _pool_move_approval_details(context.args)
            summary_paragraph = f"Pool membership move requested for {subject}."
            return WorkflowReplyTemplate(
                title="Approve Proxmox pool membership move",
                outcome="info",
                summary=(
                    f"{summary_paragraph}\n\n"
                    f"{_pool_move_approval_summary(before_state=before_state, args=context.args)}"
                ),
                evidence_summary=(
                    evidence_summary := _pool_move_evidence_summary(
                        phase=context.phase,
                        tool_name=context.tool_name,
                        before_state=before_state,
                        result=result,
                        postflight_result=postflight,
                    )
                ),
                approval_presentation=_approval_presentation_from_reply_data(
                    paragraph=summary_paragraph,
                    details=details,
                    evidence_summary=evidence_summary,
                    extra_blocks=[
                        _approval_table_block(
                            headers=["VMID", "Source pool", "Destination pool"],
                            rows=_pool_move_requested_change_table_rows(context.args),
                        )
                    ],
                ),
                details=details,
                next_step="Approve the request to move the VMIDs between pools.",
            )

        if context.phase == "completed" and failed_result:
            if _pool_move_verified(context.tool_name, result, postflight):
                return WorkflowReplyTemplate(
                    title="Proxmox pool membership move partially completed",
                    outcome="partial",
                    summary=(
                        f"The request to move {subject} reported a failure, but postflight verification confirmed the requested VMIDs are in the destination pool."
                    ),
                    evidence_summary=_pool_move_evidence_summary(
                        phase=context.phase,
                        tool_name=context.tool_name,
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
                    phase=context.phase,
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
                    phase=context.phase,
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
                    phase=context.phase,
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
                    f"{_pool_move_completion_summary(tool_name=context.tool_name, before_state=before_state, result=result, postflight_result=postflight, args=context.args)}"
                ),
                evidence_summary=_pool_move_evidence_summary(
                    phase=context.phase,
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
                    items=_pool_move_requested_change_items(context.args),
                ),
                WorkflowEvidenceSection(
                    key="after_state",
                    title="After state",
                    items=_pool_move_after_state_items(result, postflight),
                ),
                WorkflowEvidenceSection(
                    key="verification",
                    title="Verification",
                    items=_pool_move_verification_items(
                        context.tool_name, result, postflight
                    ),
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


def _pool_move_subject(args: dict[str, object]) -> str:
    source_pool = normalized_text(args.get("source_pool")) or "unknown-source-pool"
    destination_pool = (
        normalized_text(args.get("destination_pool")) or "unknown-destination-pool"
    )
    vmids_text = _vmids_text(args.get("vmids"))
    return f"VMIDs {vmids_text} from {source_pool} to {destination_pool}"


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


def _pool_move_preflight_content(
    *,
    subject: str,
    before_state: dict[str, object] | None,
) -> str:
    if before_state is None:
        return f"Read the current Proxmox pool preflight membership for {subject}."
    return f"Read the current Proxmox pool preflight membership for {subject}."


def _pool_move_verification_content(
    *,
    context: WorkflowTemplateContext,
    result: dict[str, object],
    postflight_result: dict[str, object] | None,
) -> str:
    if context.phase == "completed" and _pool_move_verified(
        context.tool_name, result, postflight_result
    ):
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
    # Prefer postflight (fresher) over inline result (stale) for after-state display
    source_after = (
        postflight_result.get("source_pool_after")
        if isinstance(postflight_result.get("source_pool_after"), dict)
        else result.get("source_pool_after")
        if isinstance(result.get("source_pool_after"), dict)
        else {}
    )
    destination_after = (
        postflight_result.get("destination_pool_after")
        if isinstance(postflight_result.get("destination_pool_after"), dict)
        else result.get("destination_pool_after")
        if isinstance(result.get("destination_pool_after"), dict)
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
            value="yes"
            if _pool_move_verified(
                "proxmox_move_vms_between_pools", result, postflight_result
            )
            else "no",
        ),
    ]


def _pool_move_verification_items(
    tool_name: str, result: dict[str, object], postflight_result: dict[str, object]
) -> list[WorkflowEvidenceItem]:
    items = [
        WorkflowEvidenceItem(
            label="Verified",
            value="yes"
            if _pool_move_verified(tool_name, result, postflight_result)
            else "no",
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
    postflight_state = _pool_move_postflight_state(tool_name, postflight_result)
    if postflight_state is not None:
        items.append(
            WorkflowEvidenceItem(
                label="Postflight",
                value=postflight_state,
            )
        )
    return items


def _pool_move_evidence_summary(
    *,
    phase: str,
    tool_name: str,
    before_state: dict[str, object] | None,
    result: dict[str, object],
    postflight_result: dict[str, object] | None,
) -> list[str]:
    summary: list[str] = []
    if isinstance(before_state, dict):
        summary.append("Preflight captured for the exact source and destination pools.")
    if phase in {"waiting_on_approval", "denied", "failed"}:
        return summary
    summary.extend(
        _pool_move_verification_summary_lines(
            tool_name=tool_name,
            result=result,
            postflight_result=postflight_result,
        )
    )
    return summary


def _pool_move_requested_change_facts(
    args: dict[str, object],
) -> dict[str, object]:
    vmids = _normalized_int_list(args.get("vmids"))
    return {
        "source_pool": _pool_value(args.get("source_pool")),
        "destination_pool": _pool_value(args.get("destination_pool")),
        "vmids": vmids,
        "vmids_text": ", ".join(str(vmid) for vmid in vmids)
        if vmids
        else "unknown-vmids",
        "target_user": _pool_value(args.get("email")),
        "reason": normalized_text(args.get("reason")) or "none provided",
    }


def _pool_move_requested_change_items(
    args: dict[str, object],
) -> list[WorkflowEvidenceItem]:
    facts = _pool_move_requested_change_facts(args)
    return [
        WorkflowEvidenceItem(label="Source pool", value=str(facts["source_pool"])),
        WorkflowEvidenceItem(
            label="Destination pool", value=str(facts["destination_pool"])
        ),
        WorkflowEvidenceItem(label="VMIDs", value=str(facts["vmids_text"])),
        WorkflowEvidenceItem(label="Target user", value=str(facts["target_user"])),
        WorkflowEvidenceItem(label="Reason", value=str(facts["reason"])),
    ]


def _pool_move_approval_details(
    args: dict[str, object],
) -> list[dict[str, str]]:
    facts = _pool_move_requested_change_facts(args)
    vmids_text = str(facts["vmids_text"])
    source_pool = str(facts["source_pool"])
    destination_pool = str(facts["destination_pool"])
    return _approval_detail_rows(
        (
            "Action",
            f"Move VMIDs {vmids_text} from {source_pool} to {destination_pool}.",
        ),
        ("Reason", str(facts["reason"])),
        (
            "Success criteria",
            f"VMIDs {vmids_text} are removed from {source_pool} and present in {destination_pool}.",
        ),
    )


def _pool_move_approval_summary(
    *,
    before_state: dict[str, object] | None,
    args: dict[str, object],
) -> str:
    facts = _pool_move_requested_change_facts(args)
    lines = [
        (
            f"Move request for VMIDs {facts['vmids_text']} from {facts['source_pool']} to "
            f"{facts['destination_pool']}."
        ),
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


def _pool_move_requested_change_table_rows(
    args: dict[str, object],
) -> list[list[str]]:
    facts = _pool_move_requested_change_facts(args)
    requested_vmids = facts["vmids"]
    if not isinstance(requested_vmids, list):
        return []
    source_pool = str(facts["source_pool"])
    destination_pool = str(facts["destination_pool"])
    return [
        [str(vmid), source_pool, destination_pool]
        for vmid in requested_vmids
        if isinstance(vmid, int) and not isinstance(vmid, bool)
    ]


def _pool_move_completion_summary(
    *,
    tool_name: str,
    before_state: dict[str, object] | None,
    result: dict[str, object],
    postflight_result: dict[str, object] | None,
    args: dict[str, object],
) -> str:
    postflight = postflight_result if isinstance(postflight_result, dict) else {}
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
    # Prefer postflight (fresher) over inline result (stale) for after-state display
    source_after = (
        postflight.get("source_pool_after")
        if isinstance(postflight.get("source_pool_after"), dict)
        else result.get("source_pool_after")
        if isinstance(result.get("source_pool_after"), dict)
        else None
    )
    destination_after = (
        postflight.get("destination_pool_after")
        if isinstance(postflight.get("destination_pool_after"), dict)
        else result.get("destination_pool_after")
        if isinstance(result.get("destination_pool_after"), dict)
        else None
    )
    return "\n\n".join(
        [
            _pool_table("Source pool before", source_before),
            _pool_table("Destination pool before", destination_before),
            _pool_table("Source pool after", source_after),
            _pool_table("Destination pool after", destination_after),
            f"Moved VMIDs: {_vmids_text(args.get('vmids'))}.",
            *_pool_move_verification_summary_lines(
                tool_name=tool_name,
                result=result,
                postflight_result=postflight_result,
            ),
        ]
    )


def _pool_move_verified(
    tool_name: str,
    result: dict[str, object],
    postflight_result: dict[str, object] | None,
) -> bool:
    postflight = postflight_result if isinstance(postflight_result, dict) else {}
    # If postflight ran and explicitly failed, downgrade even if inline said verified
    if postflight and postflight.get("ok") is False:
        return False
    if _postflight_verified(tool_name, postflight_result):
        return True
    return result.get("verified") is True and not postflight


def _pool_move_verification_summary_lines(
    *,
    tool_name: str,
    result: dict[str, object],
    postflight_result: dict[str, object] | None,
) -> list[str]:
    postflight_state = _pool_move_postflight_state(tool_name, postflight_result)
    verified = _pool_move_verified(tool_name, result, postflight_result)
    if verified:
        if result.get("verified") is True:
            summary = ["Verification succeeded."]
        else:
            summary = ["Postflight verification succeeded."]
        postflight_summary = _pool_move_postflight_summary_line(
            verified=True, postflight_state=postflight_state
        )
        if postflight_summary is not None:
            summary.append(postflight_summary)
        return summary
    summary = ["Verification not confirmed."]
    if postflight_state is None:
        return summary
    postflight_summary = _pool_move_postflight_summary_line(
        verified=False, postflight_state=postflight_state
    )
    if postflight_summary is not None:
        summary.append(postflight_summary)
    return summary


def _pool_move_postflight_state(
    tool_name: str, postflight_result: dict[str, object] | None
) -> str | None:
    if not isinstance(postflight_result, dict) or not postflight_result:
        return None
    if _postflight_verified(tool_name, postflight_result):
        return "verified"
    if normalized_text(postflight_result.get("error_code")) == "postflight_failed":
        return "failed"
    return "degraded"


def _pool_move_postflight_summary_line(
    *, verified: bool, postflight_state: str | None
) -> str | None:
    if postflight_state is None or postflight_state == "verified":
        return None
    if verified:
        if postflight_state == "failed":
            return "Postflight verification disagreed with the result."
        return "Postflight refetch was degraded."
    if postflight_state == "failed":
        return "Postflight verification failed."
    return "Postflight refetch was degraded."


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
