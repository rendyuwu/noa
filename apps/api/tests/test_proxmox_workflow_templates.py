from __future__ import annotations

from noa_api.core.workflows.registry import (
    build_workflow_evidence_template,
    build_workflow_reply_template,
    build_workflow_todos,
    require_matching_preflight,
)


def test_proxmox_workflow_completed_reply_summarizes_before_after_and_verification() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "node": "pve1-node",
                    "vmid": 101,
                    "digest": "digest-1",
                    "net": "net0",
                    "before_net": "virtio=AA:BB:CC,bridge=vmbr0",
                    "link_state": "up",
                    "auto_selected_net": True,
                    "nets": [],
                },
            }
        ],
        result={
            "ok": True,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "status": "changed",
            "message": "NIC disabled",
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0",
            "after_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "link_state": "down",
            "verified": True,
            "upid": "UPID:pve1:00000001:task",
            "task_status": "stopped",
            "task_exit_status": "OK",
        },
        postflight_result={
            "ok": True,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "digest": "digest-2",
            "net": "net0",
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "link_state": "down",
            "auto_selected_net": False,
            "nets": [],
        },
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert reply.title == "Disabled VM 101 net0 on pve1-node"
    assert "moved from link up to link down" in reply.summary
    assert "Verification succeeded." in reply.evidence_summary


def test_proxmox_workflow_preflight_matching_uses_generic_preflight_collection() -> (
    None
):
    working_messages = [
        {"role": "user", "parts": [{"type": "text", "text": "Disable the NIC"}]},
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool-call",
                    "toolName": "proxmox_preflight_vm_nic_toggle",
                    "toolCallId": "call-1",
                    "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool-result",
                    "toolName": "proxmox_preflight_vm_nic_toggle",
                    "toolCallId": "call-1",
                    "result": {
                        "ok": True,
                        "server_id": "srv-1",
                        "node": "pve1-node",
                        "vmid": 101,
                        "digest": "digest-1",
                        "net": "net0",
                        "before_net": "virtio=AA:BB:CC,bridge=vmbr0",
                        "link_state": "up",
                        "auto_selected_net": True,
                        "nets": [],
                    },
                }
            ],
        },
    ]

    assert (
        require_matching_preflight(
            tool_name="proxmox_disable_vm_nic",
            workflow_family="proxmox-vm-nic-connectivity",
            args={
                "server_ref": "pve1",
                "node": "pve1-node",
                "vmid": 101,
                "net": "net0",
                "digest": "digest-1",
            },
            working_messages=working_messages,
            requested_server_id="srv-1",
        )
        is None
    )

    mismatch = require_matching_preflight(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-stale",
        },
        working_messages=working_messages,
        requested_server_id="srv-1",
    )

    assert mismatch is not None
    assert mismatch.error_code == "preflight_mismatch"


def test_proxmox_workflow_waiting_on_user_todos_include_reason_step() -> None:
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
        },
        phase="waiting_on_user",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "node": "pve1-node",
                    "vmid": 101,
                    "digest": "digest-1",
                    "net": "net0",
                    "before_net": "virtio=AA:BB:CC,bridge=vmbr0",
                    "link_state": "up",
                    "auto_selected_net": True,
                    "nets": [],
                },
            }
        ],
    )

    assert workflow_todos is not None
    assert len(workflow_todos) == 5
    assert workflow_todos[1]["status"] == "waiting_on_user"
    assert "reason" in workflow_todos[1]["content"].lower()


def test_proxmox_workflow_requested_change_evidence_includes_reason() -> None:
    evidence = build_workflow_evidence_template(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "customer request",
        },
        phase="waiting_on_approval",
        preflight_evidence=[],
    )

    assert evidence is not None
    requested_change = next(
        section for section in evidence.sections if section.key == "requested_change"
    )
    assert any(
        item.label == "Reason" and item.value == "customer request"
        for item in requested_change.items
    )
