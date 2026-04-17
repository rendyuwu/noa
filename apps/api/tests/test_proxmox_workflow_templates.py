from __future__ import annotations

from noa_api.core.workflows.registry import (
    build_workflow_evidence_template,
    build_workflow_reply_template,
    build_workflow_todos,
    require_matching_preflight,
)


def test_proxmox_cloudinit_password_reset_waiting_on_user_todos_are_five_step_and_preflight_gated() -> (
    None
):
    todos = build_workflow_todos(
        tool_name="proxmox_reset_vm_cloudinit_password",
        workflow_family="proxmox-vm-cloudinit-password-reset",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "new_password": "secret",
            "reason": "customer request",
        },
        phase="waiting_on_user",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_cloudinit_password_reset",
                "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "node": "pve1-node",
                    "vmid": 101,
                    "config": {"digest": "digest-1"},
                    "cloudinit": {"data": {"cipassword": "old"}},
                },
            }
        ],
    )

    assert todos is not None
    assert len(todos) == 5
    assert [todo["status"] for todo in todos] == [
        "completed",
        "waiting_on_user",
        "pending",
        "pending",
        "pending",
    ]
    assert "preflight" in todos[0]["content"].lower()


def test_proxmox_cloudinit_password_reset_completed_reply_mentions_restart_note() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_reset_vm_cloudinit_password",
        workflow_family="proxmox-vm-cloudinit-password-reset",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "new_password": "secret",
            "reason": "customer request",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_cloudinit_password_reset",
                "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "node": "pve1-node",
                    "vmid": 101,
                    "config": {"digest": "digest-1"},
                    "cloudinit": {"data": {"cipassword": "old"}},
                },
            }
        ],
        result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "set_password_task": {"ok": True, "data": "UPID:SET"},
            "regenerate_cloudinit": {"ok": True},
            "cloudinit": {"ok": True},
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "may not take effect immediately" in reply.summary
    assert "restart or stop/start" in reply.next_step


def test_proxmox_pool_membership_move_waiting_on_approval_reply_includes_full_tables() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101, 102],
            "email": "l1@example.com",
            "reason": "customer request",
        },
        phase="waiting_on_approval",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": {
                    "server_ref": "pve1",
                    "source_pool": "pool-a",
                    "destination_pool": "pool-b",
                    "vmids": [101, 102],
                    "email": "l1@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {
                        "data": [
                            {
                                "poolid": "pool-a",
                                "members": [
                                    {
                                        "vmid": 101,
                                        "name": "alpha",
                                        "node": "pve1",
                                        "status": "running",
                                    },
                                    {
                                        "vmid": 102,
                                        "name": "beta",
                                        "node": "pve2",
                                        "status": "stopped",
                                    },
                                ],
                            }
                        ]
                    },
                    "destination_pool": {
                        "data": [
                            {
                                "poolid": "pool-b",
                                "members": [
                                    {
                                        "vmid": 201,
                                        "name": "gamma",
                                        "node": "pve3",
                                        "status": "running",
                                    },
                                ],
                            }
                        ]
                    },
                    "target_user": {"data": {"userid": "l1@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101, 102],
                    "normalized_userid": "l1@example.com@pve",
                },
            }
        ],
    )

    assert reply is not None
    assert reply.outcome == "info"
    assert "| VMID | Name | Node | Status |" in reply.summary
    assert reply.summary.count("| 101 | alpha | pve1 | running |") == 1


def test_proxmox_pool_membership_move_completed_reply_includes_full_before_and_after_tables() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "email": "l1@example.com",
            "reason": "customer request",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": {
                    "server_ref": "pve1",
                    "source_pool": "pool-a",
                    "destination_pool": "pool-b",
                    "vmids": [101],
                    "email": "l1@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {
                        "data": [
                            {
                                "poolid": "pool-a",
                                "members": [
                                    {
                                        "vmid": 101,
                                        "name": "alpha",
                                        "node": "pve1",
                                        "status": "running",
                                    },
                                ],
                            }
                        ]
                    },
                    "destination_pool": {
                        "data": [
                            {
                                "poolid": "pool-b",
                                "members": [
                                    {
                                        "vmid": 201,
                                        "name": "gamma",
                                        "node": "pve3",
                                        "status": "running",
                                    },
                                ],
                            }
                        ]
                    },
                    "target_user": {"data": {"userid": "l1@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_userid": "l1@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {
                "data": [
                    {
                        "poolid": "pool-a",
                        "members": [
                            {
                                "vmid": 101,
                                "name": "alpha",
                                "node": "pve1",
                                "status": "running",
                            },
                        ],
                    }
                ]
            },
            "destination_pool_before": {
                "data": [
                    {
                        "poolid": "pool-b",
                        "members": [
                            {
                                "vmid": 201,
                                "name": "gamma",
                                "node": "pve3",
                                "status": "running",
                            },
                        ],
                    }
                ]
            },
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [
                    {
                        "poolid": "pool-b",
                        "members": [
                            {
                                "vmid": 101,
                                "name": "alpha",
                                "node": "pve1",
                                "status": "running",
                            },
                            {
                                "vmid": 201,
                                "name": "gamma",
                                "node": "pve3",
                                "status": "running",
                            },
                        ],
                    }
                ]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": True,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [
                    {
                        "poolid": "pool-b",
                        "members": [
                            {
                                "vmid": 101,
                                "name": "alpha",
                                "node": "pve1",
                                "status": "running",
                            },
                        ],
                    }
                ]
            },
            "verified": True,
        },
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "Source pool before" in reply.summary
    assert "| 101 | alpha | pve1 | running |" in reply.summary
    assert "Source pool after" in reply.summary


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


def test_proxmox_workflow_terminal_todos_keep_captured_reason_completed() -> None:
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "maintenance window",
        },
        phase="denied",
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
    assert workflow_todos[1]["status"] == "completed"

    failed_todos = build_workflow_todos(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "maintenance window",
        },
        phase="failed",
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

    assert failed_todos is not None
    assert failed_todos[1]["status"] == "completed"
