from __future__ import annotations

import pytest

from noa_api.core.workflows.registry import (
    build_workflow_evidence_template,
    build_workflow_reply_template,
    build_workflow_todos,
    fetch_postflight_result,
    require_matching_preflight,
)
from noa_api.core.workflows.types import (
    render_workflow_reply_text,
    render_workflow_approval_markdown,
    workflow_evidence_template_payload,
)


class _FakeSession:
    pass


def _reply_detail_map(reply) -> dict[str, str]:
    assert reply is not None
    assert reply.details is not None
    return {item["label"]: item["value"] for item in reply.details}


def _assert_approval_markdown_matches_reply(
    reply,
    *,
    expected_paragraphs: list[str],
    expected_table_rows: list[str] | None = None,
) -> str:
    assert reply is not None
    assert reply.approval_presentation is not None
    assert reply.details is not None

    markdown = render_workflow_approval_markdown(reply.approval_presentation)

    for paragraph in expected_paragraphs:
        assert paragraph in markdown
    for item in reply.details:
        assert f"- **{item['label']}:** {item['value']}" in markdown
    for item in reply.evidence_summary:
        assert f"- {item}" in markdown
    for row in expected_table_rows or []:
        assert row in markdown

    return markdown


_SHA512_PASSWORD_HASH = "$6$saltstring$AIsRs/Ee56G/tC8MEHhvReZTfx8u3rXXMl6eYrjCG9ibix19DxoMBLogdTET5Ukw9Sf7eZTITsuk0Ry5qulYz."
_SHA512_PASSWORD_HASH_ALT = "$6$saltstring$kBE8gj8nVc2heIhflmQyp6fT2NcwZxpZpzmO5C5lurdV60T8VT5krRwB2gqJvvlKpzQgTTxurOSB1L0gzIrFL."
_SHA512_PASSWORD_HASH_MISMATCH = "$6$saltstring$r.1ZoBDig6ks.g50soeNlbxogxJLC6Q2IYHTECzAWa5/x3I1VwWSxpwKFVc19gh4ROQD5GEHESemYB3tFbCOU1"
_SHA256_PASSWORD_HASH = "$5$saltstring$C3o4O1TC6aRHF4FI.QSZMXtHbaj2gSXr4sUc/3NcUi."
_YESCRYPT_PASSWORD_HASH = (
    "$y$j9T$0123456789abcdef$lR1n3oQf67KjQYqzXTbu5mO9zFkv9J6PEbyeH7jZQy4"
)


def _enable_vm_nic_args(
    *, reason: str | None = "restore connectivity"
) -> dict[str, object]:
    args = {
        "server_ref": "pve1",
        "node": "pve1-node",
        "vmid": 101,
        "net": "net0",
        "digest": "digest-1",
    }
    if reason is not None:
        args["reason"] = reason
    return args


def _enable_vm_nic_preflight_result(*, link_state: str = "down") -> dict[str, object]:
    return {
        "ok": True,
        "server_id": "srv-1",
        "node": "pve1-node",
        "vmid": 101,
        "digest": "digest-1",
        "net": "net0",
        "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
        "link_state": link_state,
        "auto_selected_net": False,
        "nets": [],
    }


def _enable_vm_nic_preflight_evidence(
    *, link_state: str = "down"
) -> list[dict[str, object]]:
    return [
        {
            "toolName": "proxmox_preflight_vm_nic_toggle",
            "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
            "result": _enable_vm_nic_preflight_result(link_state=link_state),
        }
    ]


def _enable_vm_nic_result(
    *,
    verified: bool,
    ok: bool = True,
    status: str | None = None,
    message: str | None = None,
    link_state: str = "up",
    after_net: str | None = None,
) -> dict[str, object]:
    return {
        "ok": ok,
        "server_id": "srv-1",
        "node": "pve1-node",
        "vmid": 101,
        "net": "net0",
        "digest": "digest-1",
        "status": status if status is not None else ("changed" if ok else "failed"),
        "message": message
        if message is not None
        else ("NIC enabled" if ok else "NIC enable failed"),
        "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
        "after_net": after_net
        if after_net is not None
        else "virtio=AA:BB:CC,bridge=vmbr0",
        "link_state": link_state,
        "verified": verified,
        "upid": "UPID:pve1:00000002:task",
        "task_status": "stopped",
        "task_exit_status": "OK",
    }


def _enable_vm_nic_postflight_result(
    *, link_state: str = "up", verified: bool = True
) -> dict[str, object]:
    return {
        "ok": True,
        "message": "ok",
        "verified": verified,
        "server_id": "srv-1",
        "node": "pve1-node",
        "vmid": 101,
        "digest": "digest-2",
        "before_net": (
            "virtio=AA:BB:CC,bridge=vmbr0"
            if link_state == "up"
            else "virtio=AA:BB:CC,bridge=vmbr0,link_down=1"
        ),
        "link_state": link_state,
    }


def _pool_move_args() -> dict[str, object]:
    return {
        "server_ref": "pve1",
        "source_pool": "pool-a",
        "destination_pool": "pool-b",
        "vmids": [101],
        "old_email": "l1@example.com",
        "new_email": "l2@example.com",
        "reason": "customer request",
    }


def _pool_move_preflight_result() -> dict[str, object]:
    return {
        "ok": True,
        "server_id": "srv-1",
        "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
        "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
        "old_user": {"data": {"userid": "l1@example.com@pve"}},
        "new_user": {"data": {"userid": "l2@example.com@pve"}},
        "source_permission": {"data": {"/pool/pool-a": {"VM.Allocate": 1}}},
        "destination_permission": {"data": {"/pool/pool-b": {"VM.Console": 1}}},
        "requested_vmids": [101],
        "normalized_old_userid": "l1@example.com@pve",
        "normalized_new_userid": "l2@example.com@pve",
    }


def _pool_move_result(*, verified: bool) -> dict[str, object]:
    return {
        "ok": True,
        "message": "task completed",
        "status": "changed",
        "server_id": "srv-1",
        "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
        "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
        "add_to_destination": {"ok": True, "data": "UPID:ADD"},
        "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
        "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
        "destination_pool_after": {
            "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
        },
        "results": [{"vmid": 101, "status": "changed"}],
        "verified": verified,
    }


def _pool_move_postflight_verified_result() -> dict[str, object]:
    return {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": "srv-1",
        "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
        "destination_pool_after": {
            "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
        },
        "verified": True,
    }


def _pool_move_postflight_refetch_failed_result() -> dict[str, object]:
    return {
        "ok": False,
        "error_code": "bad_pool_refetch",
        "message": "pool refetch temporarily unavailable",
    }


def _pool_move_postflight_verification_failed_result() -> dict[str, object]:
    return {
        "ok": False,
        "error_code": "postflight_failed",
        "message": "Unable to verify pool membership after the move",
    }


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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
    assert "osTicket/reference number or a brief description" in todos[1]["content"]


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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
        postflight_result={
            "ok": True,
            "message": "ok",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "VM 101 on node pve1-node" in reply.summary
    assert "may not take effect immediately" in reply.summary
    assert "Before config digest: digest-1." in reply.summary
    assert "restart or stop/start" in reply.next_step


def test_proxmox_cloudinit_password_reset_completed_reply_uses_integer_vmid_and_postflight_digest() -> (
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
    )

    assert reply is not None
    assert "VM 101 on node pve1-node" in reply.summary
    assert "Before config digest: digest-1." in reply.summary


def test_proxmox_cloudinit_password_reset_completed_evidence_marks_verified_when_postflight_verifies_state() -> (
    None
):
    evidence = build_workflow_evidence_template(
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
            "verified": False,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
    )

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert any(
        item.label == "Verified" and item.value == "yes" for item in verification.items
    )


@pytest.mark.asyncio
async def test_proxmox_cloudinit_password_reset_fetch_postflight_result_returns_runtime_shape() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    class _Client:
        async def get_qemu_cloudinit(self, node: str, vmid: int):
            return {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            }

        async def get_qemu_cloudinit_dump_user(self, node: str, vmid: int):
            _ = node, vmid
            return {
                "ok": True,
                "message": "ok",
                "data": f"password: {_SHA512_PASSWORD_HASH}",
            }

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return _Client(), "srv-1"

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_reset_vm_cloudinit_password",
            workflow_family="proxmox-vm-cloudinit-password-reset",
            args={
                "server_ref": "pve1",
                "node": "pve1-node",
                "vmid": 101,
                "new_password": "secret",
            },
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve
    assert postflight == {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": "srv-1",
        "node": "pve1-node",
        "vmid": 101,
        "cloudinit": {
            "ok": True,
            "message": "ok",
            "data": [{"key": "cipassword", "value": "[redacted]"}],
        },
        "cloudinit_dump_user": {
            "ok": True,
            "message": "ok",
            "data": "password: [REDACTED]",
        },
        "verified": True,
    }


@pytest.mark.asyncio
async def test_proxmox_cloudinit_password_reset_fetch_postflight_result_redacts_dump_and_verifies() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    class _Client:
        async def get_qemu_cloudinit(self, node: str, vmid: int):
            return {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            }

        async def get_qemu_cloudinit_dump_user(self, node: str, vmid: int):
            _ = node, vmid
            return {
                "ok": True,
                "message": "ok",
                "data": f"password: {_SHA512_PASSWORD_HASH}\n",
            }

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return _Client(), "srv-1"

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_reset_vm_cloudinit_password",
            workflow_family="proxmox-vm-cloudinit-password-reset",
            args={
                "server_ref": "pve1",
                "node": "pve1-node",
                "vmid": 101,
                "new_password": "secret",
            },
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve

    assert postflight is not None
    assert postflight["ok"] is True
    assert postflight["verified"] is True
    assert postflight["cloudinit_dump_user"]["data"] == "password: [REDACTED]\n"
    assert "secret" not in postflight["cloudinit_dump_user"]["data"]


@pytest.mark.asyncio
async def test_proxmox_cloudinit_password_reset_fetch_postflight_result_rejects_nonmatching_password_hash() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    class _Client:
        async def get_qemu_cloudinit(self, node: str, vmid: int):
            return {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            }

        async def get_qemu_cloudinit_dump_user(self, node: str, vmid: int):
            return {
                "ok": True,
                "message": "ok",
                "data": f"password: {_SHA512_PASSWORD_HASH_MISMATCH}\n",
            }

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return _Client(), "srv-1"

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_reset_vm_cloudinit_password",
            workflow_family="proxmox-vm-cloudinit-password-reset",
            args={
                "server_ref": "pve1",
                "node": "pve1-node",
                "vmid": 101,
                "new_password": "secret",
            },
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve

    assert postflight is not None
    assert postflight["ok"] is False
    assert postflight["error_code"] == "postflight_failed"


@pytest.mark.asyncio
async def test_proxmox_pool_membership_move_fetch_postflight_result_returns_runtime_failure_shape() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    class _Client:
        async def get_pool(self, poolid: str):
            _ = poolid
            return {
                "ok": True,
                "message": "ok",
                "data": [{"poolid": "pool-a", "members": []}],
            }

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return _Client(), "srv-1"

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_move_vms_between_pools",
            workflow_family="proxmox-pool-membership-move",
            args={
                "server_ref": "pve1",
                "source_pool": "pool-a",
                "destination_pool": "pool-b",
                "vmids": [101],
                "old_email": "l1@example.com",
                "new_email": "l2@example.com",
            },
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve

    assert postflight is not None
    assert postflight["ok"] is False
    assert postflight["error_code"] == "postflight_failed"


@pytest.mark.asyncio
async def test_proxmox_cloudinit_password_reset_fetch_postflight_result_handles_upstream_error() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    class _Client:
        async def get_qemu_cloudinit(self, node: str, vmid: int):
            _ = node, vmid
            return {"ok": False, "error_code": "bad_cloudinit", "message": "boom"}

        async def get_qemu_cloudinit_dump_user(self, node: str, vmid: int):
            _ = node, vmid
            return {"ok": True, "message": "ok", "data": "password: secret"}

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return _Client(), "srv-1"

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_reset_vm_cloudinit_password",
            workflow_family="proxmox-vm-cloudinit-password-reset",
            args={"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve

    assert postflight is not None
    assert postflight["ok"] is False
    assert postflight["error_code"] == "bad_cloudinit"


@pytest.mark.asyncio
async def test_proxmox_cloudinit_password_reset_fetch_postflight_result_handles_missing_resolved_client() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return None

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_reset_vm_cloudinit_password",
            workflow_family="proxmox-vm-cloudinit-password-reset",
            args={"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve

    assert postflight is None


def test_proxmox_cloudinit_password_reset_completed_evidence_serializes_null_wrapper_data() -> (
    None
):
    evidence = build_workflow_evidence_template(
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
            "set_password_task": {"ok": True, "message": "ok", "data": None},
            "regenerate_cloudinit": {"ok": True, "message": "ok", "data": None},
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {
                "ok": True,
                "message": "ok",
                "data": {"password": "secret"},
            },
            "verified": True,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {
                "ok": True,
                "message": "ok",
                "data": {"password": "secret"},
            },
            "verified": True,
        },
    )

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "verification"
    )
    assert any(
        item["label"] == "UPID" and item["value"] == "none"
        for item in verification["items"]
    )


def test_proxmox_cloudinit_password_reset_completed_evidence_reports_integer_vmid() -> (
    None
):
    evidence = build_workflow_evidence_template(
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": "password: [REDACTED]"},
            "verified": True,
        },
    )

    assert evidence is not None
    before_state = next(
        section for section in evidence.sections if section.key == "before_state"
    )
    assert any(
        item.label == "VMID" and item.value == "101" for item in before_state.items
    )


def test_proxmox_cloudinit_password_reset_completed_reply_uses_real_cloudinit_shape() -> (
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
    )

    assert reply is not None
    assert "Before config digest: digest-1." in reply.summary
    assert "digest-2" not in reply.summary


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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
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
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101, 102],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
    )

    assert reply is not None
    assert reply.outcome == "info"
    assert "| VMID | Name | Node | Status |" in reply.summary
    assert reply.summary.count("| 101 | alpha | pve1 | running |") == 1
    assert "pool membership" in reply.summary.lower()


def test_proxmox_enable_vm_nic_waiting_on_approval_reply_includes_detail_rows() -> None:
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="waiting_on_approval",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
    )

    assert _reply_detail_map(reply) == {
        "Action": "Enable VM NIC VM 101 NIC net0 on node pve1-node.",
        "Reason": "restore connectivity",
        "Success criteria": "VM 101 NIC net0 on node pve1-node ends in link state up.",
    }


def test_proxmox_enable_vm_nic_waiting_on_approval_does_not_duplicate_evidence_in_narration() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="waiting_on_approval",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
    )

    assert reply is not None
    assert reply.evidence_summary == []

    rendered = render_workflow_reply_text(reply)
    lead = "VM 101 NIC net0 on node pve1-node is currently link down and is ready to be moved to link up."
    assert rendered.count(lead) == 1
    assert rendered.count("Before: link down.") == 1


def test_proxmox_enable_vm_nic_approval_markdown_presentation_uses_paragraph_and_key_values() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="waiting_on_approval",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
    )

    markdown = _assert_approval_markdown_matches_reply(
        reply,
        expected_paragraphs=[],
    )

    assert "Before: link down." in markdown


def test_proxmox_reset_vm_cloudinit_password_waiting_on_approval_reply_includes_detail_rows() -> (
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
        phase="waiting_on_approval",
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
                },
            }
        ],
    )

    assert _reply_detail_map(reply) == {
        "Action": "Reset the cloud-init password for VM 101 on node pve1-node.",
        "Reason": "customer request",
        "Success criteria": "The cloud-init password reset completes and the regenerated state is available for VM 101 on node pve1-node.",
    }


def test_proxmox_reset_vm_cloudinit_password_waiting_on_approval_does_not_duplicate_evidence_in_narration() -> (
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
        phase="waiting_on_approval",
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
                },
            }
        ],
    )

    assert reply is not None
    assert reply.evidence_summary == []

    rendered = render_workflow_reply_text(reply)
    lead = "Cloud-init password reset requested for VM 101 on node pve1-node."
    assert rendered.count(lead) == 1
    assert rendered.count("Before: VM 101 on pve1-node.") == 1


def test_proxmox_reset_vm_cloudinit_password_approval_markdown_presentation_uses_paragraph_and_key_values() -> (
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
        phase="waiting_on_approval",
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
                },
            }
        ],
    )

    markdown = _assert_approval_markdown_matches_reply(
        reply,
        expected_paragraphs=[],
    )

    assert "Before: VM 101 on pve1-node." in markdown


def test_proxmox_move_vms_between_pools_waiting_on_approval_reply_includes_detail_rows() -> (
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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
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
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101, 102],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
    )

    assert _reply_detail_map(reply) == {
        "Action": "Move VMIDs 101, 102 from pool-a to pool-b.",
        "Reason": "customer request",
        "Success criteria": "VMIDs 101, 102 are removed from pool-a and present in pool-b.",
    }


def test_proxmox_move_vms_between_pools_approval_markdown_presentation_includes_table() -> (
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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
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
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101, 102],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
    )

    markdown = _assert_approval_markdown_matches_reply(
        reply,
        expected_paragraphs=[
            "Pool membership move requested for VMIDs 101, 102 from pool-a to pool-b."
        ],
        expected_table_rows=[
            "| VMID | Source pool | Destination pool |",
            "| 101 | pool-a | pool-b |",
            "| 102 | pool-a | pool-b |",
        ],
    )

    assert "Preflight captured for the exact source and destination pools." in markdown


def test_proxmox_move_vms_between_pools_waiting_on_approval_markdown_matches_requested_change_evidence() -> (
    None
):
    args = {
        "server_ref": "pve1",
        "source_pool": "pool-a",
        "destination_pool": "pool-b",
        "vmids": [102, 101],
        "old_email": "l1@example.com",
        "new_email": "l2@example.com",
        "reason": "customer request",
    }
    preflight_evidence = [
        {
            "toolName": "proxmox_preflight_move_vms_between_pools",
            "args": {
                "server_ref": "pve1",
                "source_pool": "pool-a",
                "destination_pool": "pool-b",
                "vmids": [101, 102],
                "old_email": "l1@example.com",
                "new_email": "l2@example.com",
            },
            "result": {
                "ok": True,
                "server_id": "srv-1",
                "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                "old_user": {"data": {"userid": "l1@example.com@pve"}},
                "new_user": {"data": {"userid": "l2@example.com@pve"}},
                "source_permission": {"data": {"/pool/pool-a": {"VM.Allocate": 1}}},
                "destination_permission": {"data": {"/pool/pool-b": {"VM.Console": 1}}},
                "requested_vmids": [101, 102],
                "normalized_old_userid": "l1@example.com@pve",
                "normalized_new_userid": "l2@example.com@pve",
            },
        }
    ]

    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=args,
        phase="waiting_on_approval",
        preflight_evidence=preflight_evidence,
    )
    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=args,
        phase="waiting_on_approval",
        preflight_evidence=preflight_evidence,
    )

    assert reply is not None
    assert evidence is not None
    requested_change = next(
        section for section in evidence.sections if section.key == "requested_change"
    )
    requested_change_map = {item.label: item.value for item in requested_change.items}
    markdown = _assert_approval_markdown_matches_reply(
        reply,
        expected_paragraphs=[
            "Pool membership move requested for VMIDs 102, 101 from pool-a to pool-b."
        ],
        expected_table_rows=[
            "| 102 | pool-a | pool-b |",
            "| 101 | pool-a | pool-b |",
        ],
    )

    assert requested_change_map == {
        "Source pool": "pool-a",
        "Destination pool": "pool-b",
        "VMIDs": "102, 101",
        "Old email (current PIC)": "l1@example.com",
        "New email (new PIC)": "l2@example.com",
        "Reason": "customer request",
    }
    details = _reply_detail_map(reply)
    assert details["Action"] == (
        f"Move VMIDs {requested_change_map['VMIDs']} from {requested_change_map['Source pool']} "
        f"to {requested_change_map['Destination pool']}."
    )
    assert details["Reason"] == requested_change_map["Reason"]
    assert details["Success criteria"] == (
        f"VMIDs {requested_change_map['VMIDs']} are removed from {requested_change_map['Source pool']} "
        f"and present in {requested_change_map['Destination pool']}."
    )
    assert (
        f"| {requested_change_map['VMIDs'].split(', ')[0]} | pool-a | pool-b |"
        in markdown
    )
    assert (
        f"| {requested_change_map['VMIDs'].split(', ')[1]} | pool-a | pool-b |"
        in markdown
    )


def test_proxmox_pool_membership_move_waiting_on_user_todos_use_action_specific_reason() -> (
    None
):
    todos = build_workflow_todos(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101, 102],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
        },
        phase="waiting_on_user",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": {
                    "server_ref": "pve1",
                    "source_pool": "pool-a",
                    "destination_pool": "pool-b",
                    "vmids": [101, 102],
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
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
                                    }
                                ],
                            }
                        ]
                    },
                    "destination_pool": {
                        "data": [
                            {
                                "poolid": "pool-b",
                                "members": [],
                            }
                        ]
                    },
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101, 102],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
    )

    assert todos is not None
    assert "osTicket/reference number or a brief description" in todos[1]["content"]


def test_proxmox_pool_membership_move_completed_reply_handles_failure_result() -> None:
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={"ok": False, "message": "pool move failed", "error_code": "boom"},
    )

    assert reply is not None
    assert reply.title == "Proxmox pool membership move failed"
    assert reply.outcome == "failed"
    assert "did not complete successfully" in reply.summary
    assert (
        "Run proxmox_preflight_move_vms_between_pools again before retrying."
        in reply.next_step
    )


def test_proxmox_pool_membership_move_denied_reply_does_not_report_verification() -> (
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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
            "reason": "customer request",
        },
        phase="denied",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
    )

    assert reply is not None
    assert reply.outcome == "denied"
    assert "Verification not confirmed." not in reply.evidence_summary


def test_proxmox_pool_membership_move_failed_reply_does_not_report_verification() -> (
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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
            "reason": "customer request",
        },
        phase="failed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result={"ok": False, "message": "pool move failed", "error_code": "boom"},
    )

    assert reply is not None
    assert reply.outcome == "failed"
    assert "Verification not confirmed." not in reply.evidence_summary


@pytest.mark.asyncio
async def test_proxmox_pool_membership_move_fetch_postflight_result_returns_runtime_shape() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    class _Client:
        async def get_pool(self, poolid: str):
            if poolid == "pool-a":
                return {
                    "ok": True,
                    "message": "ok",
                    "data": [{"poolid": "pool-a", "members": []}],
                }
            return {
                "ok": True,
                "message": "ok",
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}],
            }

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return _Client(), "srv-1"

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_move_vms_between_pools",
            workflow_family="proxmox-pool-membership-move",
            args={
                "server_ref": "pve1",
                "source_pool": "pool-a",
                "destination_pool": "pool-b",
                "vmids": [101],
                "old_email": "l1@example.com",
                "new_email": "l2@example.com",
            },
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve
    assert postflight == {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": "srv-1",
        "source_pool_after": {
            "ok": True,
            "message": "ok",
            "data": [{"poolid": "pool-a", "members": []}],
        },
        "destination_pool_after": {
            "ok": True,
            "message": "ok",
            "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}],
        },
        "verified": True,
    }


@pytest.mark.asyncio
async def test_proxmox_pool_membership_move_fetch_postflight_result_rejects_unverified_final_state() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    class _Client:
        async def get_pool(self, poolid: str):
            if poolid == "pool-a":
                return {
                    "ok": True,
                    "message": "ok",
                    "data": [
                        {
                            "poolid": "pool-a",
                            "members": [
                                {
                                    "vmid": 101,
                                    "name": "alpha",
                                    "node": "pve1",
                                    "status": "running",
                                }
                            ],
                        }
                    ],
                }
            return {
                "ok": True,
                "message": "ok",
                "data": [{"poolid": "pool-b", "members": []}],
            }

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        return _Client(), "srv-1"

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_move_vms_between_pools",
            workflow_family="proxmox-pool-membership-move",
            args={
                "server_ref": "pve1",
                "source_pool": "pool-a",
                "destination_pool": "pool-b",
                "vmids": [101],
                "old_email": "l1@example.com",
                "new_email": "l2@example.com",
            },
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve

    assert postflight is not None
    assert postflight["ok"] is False
    assert postflight["error_code"] == "postflight_failed"


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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
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
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
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


def test_proxmox_cloudinit_password_reset_require_matching_preflight_matches_new_family() -> (
    None
):
    working_messages = [
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool-call",
                    "toolName": "proxmox_preflight_vm_cloudinit_password_reset",
                    "toolCallId": "call-1",
                    "args": {
                        "server_ref": "pve1",
                        "node": "pve1-node",
                        "vmid": 101,
                    },
                },
                {
                    "type": "tool-result",
                    "toolName": "proxmox_preflight_vm_cloudinit_password_reset",
                    "toolCallId": "call-1",
                    "result": {
                        "ok": True,
                        "server_id": "srv-1",
                        "node": "pve1-node",
                        "vmid": 101,
                        "config": {"digest": "digest-1"},
                        "cloudinit": {
                            "ok": True,
                            "message": "ok",
                            "data": [{"key": "cipassword", "value": "[redacted]"}],
                        },
                    },
                },
            ],
        }
    ]

    error = require_matching_preflight(
        tool_name="proxmox_reset_vm_cloudinit_password",
        workflow_family="proxmox-vm-cloudinit-password-reset",
        args={"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
        working_messages=working_messages,
        requested_server_id="srv-1",
    )

    assert error is None


def test_proxmox_pool_membership_move_require_matching_preflight_matches_new_family() -> (
    None
):
    working_messages = [
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool-call",
                    "toolName": "proxmox_preflight_move_vms_between_pools",
                    "toolCallId": "call-1",
                    "args": {
                        "server_ref": "pve1",
                        "source_pool": "pool-a",
                        "destination_pool": "pool-b",
                        "vmids": [101],
                        "old_email": "l1@example.com",
                        "new_email": "l2@example.com",
                    },
                },
                {
                    "type": "tool-result",
                    "toolName": "proxmox_preflight_move_vms_between_pools",
                    "toolCallId": "call-1",
                    "result": {
                        "ok": True,
                        "server_id": "srv-1",
                        "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                        "destination_pool": {
                            "data": [{"poolid": "pool-b", "members": []}]
                        },
                        "old_user": {"data": {"userid": "l1@example.com@pve"}},
                        "new_user": {"data": {"userid": "l2@example.com@pve"}},
                        "source_permission": {
                            "data": {"/pool/pool-a": {"VM.Allocate": 1}}
                        },
                        "destination_permission": {
                            "data": {"/pool/pool-b": {"VM.Console": 1}}
                        },
                        "requested_vmids": [101],
                        "normalized_old_userid": "l1@example.com@pve",
                        "normalized_new_userid": "l2@example.com@pve",
                    },
                },
            ],
        }
    ]

    error = require_matching_preflight(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
        },
        working_messages=working_messages,
        requested_server_id="srv-1",
    )

    assert error is None


def test_proxmox_pool_membership_move_completed_evidence_serializes_null_wrapper_data() -> (
    None
):
    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
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
                                    }
                                ],
                            }
                        ]
                    },
                    "destination_pool": {
                        "data": [
                            {
                                "poolid": "pool-b",
                                "members": [],
                            }
                        ]
                    },
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
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
                            }
                        ],
                    }
                ]
            },
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "message": "ok", "data": None},
            "remove_from_source": {"ok": True, "message": "ok", "data": None},
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
                            }
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
                            }
                        ],
                    }
                ]
            },
            "verified": True,
        },
    )

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "verification"
    )
    assert any(
        item["label"] == "Add task" and item["value"] == "none"
        for item in verification["items"]
    )
    assert any(
        item["label"] == "Remove task" and item["value"] == "none"
        for item in verification["items"]
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


def test_proxmox_workflow_completed_evidence_marks_nic_postflight_verified() -> None:
    evidence = build_workflow_evidence_template(
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
            "ok": False,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "status": "failed",
            "message": "NIC disable failed",
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

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert any(
        item.label == "Verified" and item.value == "yes" for item in verification.items
    )


def test_proxmox_workflow_completed_reply_handles_failure_result_with_postflight_data() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "11111111-1111-1111-1111-111111111111",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "maintenance window",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
                "result": {
                    "ok": True,
                    "server_id": "11111111-1111-1111-1111-111111111111",
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
            "ok": False,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "status": "failed",
            "message": "NIC disable failed",
        },
        postflight_result={
            "ok": True,
            "server_id": "11111111-1111-1111-1111-111111111111",
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
    assert reply.outcome == "partial"
    assert "postflight" in reply.summary.lower()
    assert "verification succeeded" not in reply.summary.lower()


def test_proxmox_workflow_completed_reply_returns_partial_when_failure_postflight_verifies_state() -> (
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
            "reason": "maintenance window",
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
            "ok": False,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "status": "failed",
            "message": "NIC disable failed",
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "verified": True,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "digest": "digest-2",
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "link_state": "down",
        },
    )

    assert reply is not None
    assert reply.outcome == "partial"
    assert "postflight" in reply.summary.lower()


def test_proxmox_workflow_completed_todos_do_not_mark_verification_completed_when_postflight_errors() -> (
    None
):
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
            "ok": False,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "status": "failed",
            "message": "NIC disable failed",
        },
        postflight_result={
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify Proxmox NIC state",
        },
    )

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "cancelled"


def test_proxmox_workflow_completed_todos_mark_verification_completed_only_when_postflight_verified() -> (
    None
):
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
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "verified": True,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "digest": "digest-2",
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "link_state": "down",
        },
    )

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "completed"


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


def test_proxmox_workflow_completed_todos_mark_execution_and_verification_completed_with_postflight_data() -> (
    None
):
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_disable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "11111111-1111-1111-1111-111111111111",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "maintenance window",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
                "result": {
                    "ok": True,
                    "server_id": "11111111-1111-1111-1111-111111111111",
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
            "ok": False,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "status": "failed",
            "message": "NIC disable failed",
        },
        postflight_result={
            "ok": True,
            "server_id": "11111111-1111-1111-1111-111111111111",
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

    assert workflow_todos is not None
    assert [todo["status"] for todo in workflow_todos] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
    ]


def test_proxmox_workflow_completed_todos_mark_verification_completed_when_postflight_verified() -> (
    None
):
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
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "verified": True,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "digest": "digest-2",
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "link_state": "down",
        },
    )

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "completed"


def test_proxmox_enable_vm_nic_completed_reply_summarizes_before_after_and_verification() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=True),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert reply.title == "Enabled VM 101 net0 on pve1-node"
    assert "moved from link down to link up" in reply.summary
    assert "verification succeeded." in reply.summary.lower()


def test_proxmox_enable_vm_nic_completed_evidence_marks_postflight_verified() -> None:
    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "verification"
    )
    assert any(
        item["label"] == "Verified" and item["value"] == "yes"
        for item in verification["items"]
    )


def test_proxmox_enable_vm_nic_completed_reply_and_evidence_use_postflight_verification_wording_when_only_postflight_confirms_state() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    assert reply is not None
    assert "postflight verification succeeded." in reply.summary.lower()
    assert "verification succeeded." not in reply.summary.lower().replace(
        "postflight verification succeeded.", ""
    )

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "verification"
    )
    assert any(
        item["label"] == "Verified" and item["value"] == "yes"
        for item in verification["items"]
    )


def test_proxmox_enable_vm_nic_completed_todos_mark_verification_completed_when_postflight_verified() -> (
    None
):
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    assert workflow_todos is not None
    assert [todo["status"] for todo in workflow_todos] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    assert (
        "Reason captured for the enable VM NIC change" in workflow_todos[1]["content"]
    )


def test_proxmox_enable_vm_nic_completed_reply_returns_partial_when_failure_postflight_verifies_state() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False, ok=False),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    assert reply is not None
    assert reply.outcome == "partial"
    assert "postflight" in reply.summary.lower()


def test_proxmox_enable_vm_nic_completed_reply_reports_verification_not_confirmed_without_postflight() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False),
        postflight_result={},
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "verification is not confirmed" in reply.summary.lower()


def test_proxmox_enable_vm_nic_completed_reply_evidence_and_todos_do_not_confirm_verified_result_when_postflight_disagrees() -> (
    None
):
    conflict_result = _enable_vm_nic_result(verified=True)
    conflict_postflight = _enable_vm_nic_postflight_result(link_state="down")

    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "verification succeeded" not in reply.summary.lower()
    assert "verification succeeded" not in " ".join(reply.evidence_summary).lower()

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "cancelled"

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "verification"
    )
    assert any(
        item["label"] == "Verified" and item["value"] == "no"
        for item in verification["items"]
    )


def test_proxmox_enable_vm_nic_completed_reply_evidence_and_todos_do_not_confirm_verified_result_when_postflight_errors() -> (
    None
):
    conflict_result = _enable_vm_nic_result(verified=True)
    conflict_postflight = {
        "ok": False,
        "error_code": "postflight_failed",
        "message": "Unable to verify Proxmox NIC state",
    }

    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "verification succeeded" not in reply.summary.lower()
    assert "verification succeeded" not in " ".join(reply.evidence_summary).lower()

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "cancelled"

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "verification"
    )
    assert any(
        item["label"] == "Verified" and item["value"] == "no"
        for item in verification["items"]
    )


def test_proxmox_enable_vm_nic_completed_todos_mark_verification_completed_when_result_verified() -> (
    None
):
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=True),
        postflight_result={},
    )

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "completed"
    assert (
        "Verify that the NIC finished in link state up." == workflow_todos[4]["content"]
    )


def test_proxmox_enable_vm_nic_completed_todos_do_not_mark_verification_completed_when_postflight_errors() -> (
    None
):
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False),
        postflight_result={
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify Proxmox NIC state",
        },
    )

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_proxmox_enable_vm_nic_fetch_postflight_result_returns_runtime_shape() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    async def _preflight(*, session, server_ref, node, vmid, net=None):
        _ = session, server_ref, node, vmid, net
        return {
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "after_net": "virtio=AA:BB:CC,bridge=vmbr0",
            "link_state": "up",
            "verified": True,
            "upid": "UPID:pve1:00000002:task",
            "task_status": "stopped",
            "task_exit_status": "OK",
        }

    original_preflight = proxmox_workflows.proxmox_preflight_vm_nic_toggle
    proxmox_workflows.proxmox_preflight_vm_nic_toggle = _preflight
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_enable_vm_nic",
            workflow_family="proxmox-vm-nic-connectivity",
            args=_enable_vm_nic_args(),
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows.proxmox_preflight_vm_nic_toggle = original_preflight

    assert postflight == {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": "srv-1",
        "node": "pve1-node",
        "vmid": 101,
        "net": "net0",
        "digest": "digest-1",
        "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
        "after_net": "virtio=AA:BB:CC,bridge=vmbr0",
        "link_state": "up",
        "verified": True,
        "upid": "UPID:pve1:00000002:task",
        "task_status": "stopped",
        "task_exit_status": "OK",
    }


@pytest.mark.asyncio
async def test_proxmox_enable_vm_nic_fetch_postflight_result_rejects_unverified_final_state() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    async def _preflight(*, session, server_ref, node, vmid, net=None):
        _ = session, server_ref, node, vmid, net
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify NIC 'net0' after the update",
        }

    original_preflight = proxmox_workflows.proxmox_preflight_vm_nic_toggle
    proxmox_workflows.proxmox_preflight_vm_nic_toggle = _preflight
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_enable_vm_nic",
            workflow_family="proxmox-vm-nic-connectivity",
            args=_enable_vm_nic_args(),
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows.proxmox_preflight_vm_nic_toggle = original_preflight

    assert postflight is not None
    assert postflight["ok"] is False
    assert postflight["error_code"] == "postflight_failed"


@pytest.mark.asyncio
async def test_proxmox_enable_vm_nic_fetch_postflight_result_returns_none_for_missing_required_args() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows

    async def _resolve(*, session, server_ref):
        _ = session, server_ref
        raise AssertionError("resolver should not be called")

    original_resolve = proxmox_workflows._resolve_proxmox_client
    proxmox_workflows._resolve_proxmox_client = _resolve
    try:
        postflight = await fetch_postflight_result(
            tool_name="proxmox_enable_vm_nic",
            workflow_family="proxmox-vm-nic-connectivity",
            args={"server_ref": "pve1", "node": "pve1-node", "vmid": 101},
            session=_FakeSession(),
        )
    finally:
        proxmox_workflows._resolve_proxmox_client = original_resolve

    assert postflight is None


def test_proxmox_enable_vm_nic_completed_reply_and_todo_prefer_postflight_link_state_when_result_conflicts() -> (
    None
):
    conflict_result = {
        **_enable_vm_nic_result(verified=False),
        "link_state": "down",
        "after_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
    }
    conflict_postflight = _enable_vm_nic_postflight_result(link_state="up")

    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    assert reply is not None
    assert "moved from link down to link up" in reply.summary
    assert "verification succeeded" in reply.summary.lower()
    assert workflow_todos is not None
    assert (
        workflow_todos[4]["content"] == "Verify that the NIC finished in link state up."
    )
    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "after_state"
    )
    assert any(
        item["label"] == "Link state" and item["value"] == "up"
        for item in verification["items"]
    )
    assert any(
        item["label"] == "NIC config"
        and item["value"] == "virtio=AA:BB:CC,bridge=vmbr0"
        for item in verification["items"]
    )


def test_proxmox_enable_vm_nic_completed_reply_matches_server_id_alias_preflight() -> (
    None
):
    canonical_server_id = "11111111-1111-1111-1111-111111111111"
    canonical_result = _enable_vm_nic_result(
        verified=True,
        link_state="up",
        after_net="virtio=AA:BB:CC,bridge=vmbr0",
    )
    canonical_result["server_id"] = canonical_server_id
    canonical_postflight = _enable_vm_nic_postflight_result(link_state="up")
    canonical_postflight["server_id"] = canonical_server_id

    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "restore connectivity",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {
                    "server_ref": canonical_server_id,
                    "node": "pve1-node",
                    "vmid": 101,
                },
                "result": {
                    "ok": True,
                    "server_id": canonical_server_id,
                    "node": "pve1-node",
                    "vmid": 101,
                    "digest": "digest-1",
                    "net": "net0",
                    "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
                    "link_state": "down",
                    "auto_selected_net": False,
                    "nets": [],
                },
            }
        ],
        result=canonical_result,
        postflight_result=canonical_postflight,
    )

    assert reply is not None
    assert "moved from link down to link up" in reply.summary
    assert "verification succeeded" in reply.summary.lower()


def test_proxmox_enable_vm_nic_completed_rendering_rejects_alias_preflight_with_mismatched_canonical_server_id() -> (
    None
):
    mismatched_server_id = "22222222-2222-2222-2222-222222222222"
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "restore connectivity",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {
                    "server_ref": "pve1",
                    "node": "pve1-node",
                    "vmid": 101,
                },
                "result": {
                    "ok": True,
                    "server_id": mismatched_server_id,
                    "node": "pve1-node",
                    "vmid": 101,
                    "digest": "digest-1",
                    "net": "net0",
                    "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
                    "link_state": "down",
                    "auto_selected_net": False,
                    "nets": [],
                },
            }
        ],
        result=_enable_vm_nic_result(verified=True),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "restore connectivity",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {
                    "server_ref": "pve1",
                    "node": "pve1-node",
                    "vmid": 101,
                },
                "result": {
                    "ok": True,
                    "server_id": mismatched_server_id,
                    "node": "pve1-node",
                    "vmid": 101,
                    "digest": "digest-1",
                    "net": "net0",
                    "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
                    "link_state": "down",
                    "auto_selected_net": False,
                    "nets": [],
                },
            }
        ],
        result=_enable_vm_nic_result(verified=True),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args={
            "server_ref": "pve1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "reason": "restore connectivity",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_vm_nic_toggle",
                "args": {
                    "server_ref": "pve1",
                    "node": "pve1-node",
                    "vmid": 101,
                },
                "result": {
                    "ok": True,
                    "server_id": mismatched_server_id,
                    "node": "pve1-node",
                    "vmid": 101,
                    "digest": "digest-1",
                    "net": "net0",
                    "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
                    "link_state": "down",
                    "auto_selected_net": False,
                    "nets": [],
                },
            }
        ],
        result=_enable_vm_nic_result(verified=True),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    assert reply is not None
    assert "link unknown" in reply.summary.lower()
    assert "moved from link unknown to link up" in reply.summary.lower()

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    before_state = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "before_state"
    )
    assert any(
        item["label"] == "Status"
        and item["value"] == "No matching preflight evidence yet"
        for item in before_state["items"]
    )

    assert workflow_todos is not None
    assert workflow_todos[0]["status"] == "in_progress"
    assert "digest-1" not in workflow_todos[0]["content"]


def test_proxmox_enable_vm_nic_waiting_on_approval_reply_uses_enable_specific_wording() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="waiting_on_approval",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
    )

    assert reply is not None
    assert reply.title == "Approve enable VM 101 net0 on pve1-node"
    assert reply.summary == (
        "VM 101 NIC net0 on node pve1-node is currently link down and is ready to be moved to link up."
    )
    assert reply.next_step == (
        "Approve the request to enable VM NIC VM 101 NIC net0 on node pve1-node."
    )


def test_proxmox_enable_vm_nic_waiting_on_user_todos_require_reason_step() -> None:
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(reason=None),
        phase="waiting_on_user",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
    )

    assert workflow_todos is not None
    assert [todo["status"] for todo in workflow_todos] == [
        "completed",
        "waiting_on_user",
        "pending",
        "pending",
        "pending",
    ]
    assert (
        "osTicket/reference number or a brief description"
        in workflow_todos[1]["content"]
    )


def test_proxmox_enable_vm_nic_completed_evidence_includes_reason() -> None:
    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=True),
        postflight_result=_enable_vm_nic_postflight_result(),
    )

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    requested_change = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "requested_change"
    )
    assert any(
        item["label"] == "Reason" and item["value"] == "restore connectivity"
        for item in requested_change["items"]
    )


def test_proxmox_enable_vm_nic_denied_reply_reports_request_denied() -> None:
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="denied",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False),
    )

    assert reply is not None
    assert reply.outcome == "denied"
    assert "approval was denied" in reply.summary.lower()
    assert "remains link down" in reply.summary.lower()


def test_proxmox_enable_vm_nic_failed_reply_reports_execution_failure() -> None:
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="failed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=_enable_vm_nic_result(verified=False, ok=False),
    )

    assert reply is not None
    assert reply.outcome == "failed"
    assert "did not complete successfully" in reply.summary.lower()


def test_proxmox_enable_vm_nic_completed_reply_reports_no_op_when_already_enabled() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(link_state="up"),
        result=_enable_vm_nic_result(
            verified=True,
            status="no-op",
            message="NIC already enabled",
            link_state="up",
            after_net="virtio=AA:BB:CC,bridge=vmbr0",
        ),
        postflight_result=_enable_vm_nic_postflight_result(link_state="up"),
    )

    assert reply is not None
    assert reply.outcome == "no_op"
    assert "already link up" in reply.summary.lower()
    assert "no proxmox config change was required" in reply.summary.lower()


def test_proxmox_enable_vm_nic_completed_reply_and_todos_reject_verified_result_when_state_conflicts() -> (
    None
):
    conflict_result = _enable_vm_nic_result(
        verified=True,
        link_state="down",
        after_net="virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
    )
    conflict_postflight = _enable_vm_nic_postflight_result(link_state="up")

    reply = build_workflow_reply_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    workflow_todos = build_workflow_todos(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_enable_vm_nic",
        workflow_family="proxmox-vm-nic-connectivity",
        args=_enable_vm_nic_args(),
        phase="completed",
        preflight_evidence=_enable_vm_nic_preflight_evidence(),
        result=conflict_result,
        postflight_result=conflict_postflight,
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "verification is not confirmed" in reply.summary.lower()
    assert "verification succeeded" not in reply.summary.lower()

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "cancelled"

    assert evidence is not None
    payload = workflow_evidence_template_payload(evidence)
    verification = next(
        section
        for section in payload["evidenceSections"]
        if section["key"] == "verification"
    )
    assert any(
        item["label"] == "Verified" and item["value"] == "no"
        for item in verification["items"]
    )


def test_proxmox_cloudinit_password_reset_completed_reply_matches_server_id_alias_preflight() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_reset_vm_cloudinit_password",
        workflow_family="proxmox-vm-cloudinit-password-reset",
        args={
            "server_ref": "11111111-1111-1111-1111-111111111111",
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
                    "server_id": "11111111-1111-1111-1111-111111111111",
                    "node": "pve1-node",
                    "vmid": 101,
                    "config": {"digest": "digest-1"},
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
                },
            }
        ],
        result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "11111111-1111-1111-1111-111111111111",
            "node": "pve1-node",
            "vmid": 101,
            "set_password_task": {"ok": True, "data": "UPID:SET"},
            "regenerate_cloudinit": {"ok": True},
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "server_id": "11111111-1111-1111-1111-111111111111",
            "node": "pve1-node",
            "vmid": 101,
            "cloudinit": {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "[redacted]"}],
            },
            "cloudinit_dump_user": {"ok": True, "data": {"password": "secret"}},
            "verified": True,
        },
    )

    assert reply is not None
    assert "Before config digest: digest-1." in reply.summary


def test_proxmox_pool_membership_move_completed_reply_matches_server_id_alias_preflight() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "11111111-1111-1111-1111-111111111111",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "11111111-1111-1111-1111-111111111111",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "11111111-1111-1111-1111-111111111111",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": True,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "11111111-1111-1111-1111-111111111111",
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "verified": True,
        },
    )

    assert reply is not None
    assert "Source pool before" in reply.summary
    assert "Moved VMIDs: 101." in reply.summary


def test_proxmox_pool_membership_move_completed_reply_and_evidence_marks_verified_when_postflight_success_verifies_state() -> (
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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "task completed",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": True,
        },
        postflight_result=_pool_move_postflight_verified_result(),
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "Verification succeeded." in reply.summary
    assert "Verification succeeded." in reply.evidence_summary

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "task completed",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": True,
        },
        postflight_result=_pool_move_postflight_verified_result(),
    )

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert any(
        item.label == "Verified" and item.value == "yes" for item in verification.items
    )
    assert any(
        item.label == "Postflight" and item.value == "verified"
        for item in verification.items
    )


def test_proxmox_pool_membership_move_completed_todos_downgrade_verification_when_postflight_disagrees() -> (
    None
):
    workflow_todos = build_workflow_todos(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result=_pool_move_result(verified=True),
        postflight_result=_pool_move_postflight_verification_failed_result(),
    )

    assert workflow_todos is not None
    # Postflight disagreement downgrades verification even if inline said verified
    assert workflow_todos[4]["status"] == "cancelled"


def test_proxmox_pool_membership_move_completed_reply_and_evidence_downgrades_when_postflight_refetch_fails() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result=_pool_move_result(verified=True),
        postflight_result=_pool_move_postflight_refetch_failed_result(),
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result=_pool_move_result(verified=True),
        postflight_result=_pool_move_postflight_refetch_failed_result(),
    )

    # Postflight disagreement downgrades verification
    assert reply is not None
    assert reply.outcome == "changed"
    assert "Verification not confirmed." in reply.summary

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert any(
        item.label == "Verified" and item.value == "no" for item in verification.items
    )
    assert any(
        item.label == "Postflight" and item.value == "degraded"
        for item in verification.items
    )


def test_proxmox_pool_membership_move_completed_reply_and_evidence_downgrades_when_postflight_verification_disagrees() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result=_pool_move_result(verified=True),
        postflight_result=_pool_move_postflight_verification_failed_result(),
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result=_pool_move_result(verified=True),
        postflight_result=_pool_move_postflight_verification_failed_result(),
    )

    # Postflight disagreement downgrades verification
    assert reply is not None
    assert reply.outcome == "changed"
    assert "Verification not confirmed." in reply.summary
    assert "Postflight verification failed." in reply.summary

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert any(
        item.label == "Verified" and item.value == "no" for item in verification.items
    )
    assert any(
        item.label == "Postflight" and item.value == "failed"
        for item in verification.items
    )


def test_proxmox_pool_membership_move_completed_reply_and_evidence_are_explicit_when_unverified_and_postflight_is_degraded() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result=_pool_move_result(verified=False),
        postflight_result=_pool_move_postflight_refetch_failed_result(),
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result=_pool_move_result(verified=False),
        postflight_result=_pool_move_postflight_refetch_failed_result(),
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "Verification not confirmed." in reply.summary
    assert "Postflight refetch was degraded." in reply.summary

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert any(
        item.label == "Verified" and item.value == "no" for item in verification.items
    )
    assert any(
        item.label == "Postflight" and item.value == "degraded"
        for item in verification.items
    )


def test_proxmox_pool_membership_move_completed_reply_and_evidence_keep_missing_postflight_silent() -> (
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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "task completed",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": True,
        },
        postflight_result=None,
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "task completed",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": True,
        },
        postflight_result=None,
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "Verification succeeded." in reply.summary
    assert "Verification succeeded." in reply.evidence_summary

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert not any(item.label == "Postflight" for item in verification.items)
    assert any(
        item.label == "Verified" and item.value == "yes" for item in verification.items
    )


def test_proxmox_pool_membership_move_completed_reply_and_evidence_rescue_verification_content_when_postflight_verifies_state() -> (
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
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "task completed",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": False,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "verified": True,
        },
    )

    evidence = build_workflow_evidence_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "task completed",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": False,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "verified": True,
        },
    )

    workflow_todos = build_workflow_todos(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args={
            "server_ref": "pve1",
            "source_pool": "pool-a",
            "destination_pool": "pool-b",
            "vmids": [101],
            "old_email": "l1@example.com",
            "new_email": "l2@example.com",
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
                    "old_email": "l1@example.com",
                    "new_email": "l2@example.com",
                },
                "result": {
                    "ok": True,
                    "server_id": "srv-1",
                    "source_pool": {"data": [{"poolid": "pool-a", "members": []}]},
                    "destination_pool": {"data": [{"poolid": "pool-b", "members": []}]},
                    "old_user": {"data": {"userid": "l1@example.com@pve"}},
                    "new_user": {"data": {"userid": "l2@example.com@pve"}},
                    "destination_permission": {
                        "data": {"/pool/pool-b": {"VM.Console": 1}}
                    },
                    "requested_vmids": [101],
                    "normalized_old_userid": "l1@example.com@pve",
                    "normalized_new_userid": "l2@example.com@pve",
                },
            }
        ],
        result={
            "ok": True,
            "message": "task completed",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {"ok": True, "data": "UPID:ADD"},
            "remove_from_source": {"ok": True, "data": "UPID:REMOVE"},
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "results": [{"vmid": 101, "status": "changed"}],
            "verified": False,
        },
        postflight_result={
            "ok": True,
            "message": "ok",
            "status": "changed",
            "server_id": "srv-1",
            "source_pool_after": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_after": {
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}]
            },
            "verified": True,
        },
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert "Postflight verification succeeded." in reply.summary

    assert evidence is not None
    verification = next(
        section for section in evidence.sections if section.key == "verification"
    )
    assert any(
        item.label == "Verified" and item.value == "yes" for item in verification.items
    )

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "completed"
    assert (
        "Verify that the VMIDs were removed from the source pool"
        in workflow_todos[4]["content"]
    )


def test_proxmox_pool_membership_move_completed_reply_and_todos_return_partial_when_failure_result_is_rescued_by_verified_postflight() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result={
            "ok": False,
            "message": "task failed",
            "status": "failed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {
                "ok": False,
                "error_code": "permission_denied",
                "message": "add to destination pool failed",
            },
            "remove_from_source": {"ok": False, "message": "removal not attempted"},
            "results": [{"vmid": 101, "status": "failed"}],
            "verified": False,
        },
        postflight_result=_pool_move_postflight_verified_result(),
    )

    workflow_todos = build_workflow_todos(
        tool_name="proxmox_move_vms_between_pools",
        workflow_family="proxmox-pool-membership-move",
        args=_pool_move_args(),
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "proxmox_preflight_move_vms_between_pools",
                "args": _pool_move_args(),
                "result": _pool_move_preflight_result(),
            }
        ],
        result={
            "ok": False,
            "message": "task failed",
            "status": "failed",
            "server_id": "srv-1",
            "source_pool_before": {"data": [{"poolid": "pool-a", "members": []}]},
            "destination_pool_before": {"data": [{"poolid": "pool-b", "members": []}]},
            "add_to_destination": {
                "ok": False,
                "error_code": "permission_denied",
                "message": "add to destination pool failed",
            },
            "remove_from_source": {"ok": False, "message": "removal not attempted"},
            "results": [{"vmid": 101, "status": "failed"}],
            "verified": False,
        },
        postflight_result=_pool_move_postflight_verified_result(),
    )

    assert reply is not None
    assert reply.outcome == "partial"
    assert "postflight" in reply.summary.lower()

    assert workflow_todos is not None
    assert workflow_todos[4]["status"] == "completed"


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


def test_proxmox_workflow_completed_reply_handles_nic_denied_branch() -> None:
    reply = build_workflow_reply_template(
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

    assert reply is not None
    assert reply.outcome == "denied"
    assert "remains link up" in reply.summary


def test_proxmox_workflow_completed_reply_handles_nic_failed_branch() -> None:
    reply = build_workflow_reply_template(
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
        result={
            "ok": False,
            "server_id": "srv-1",
            "node": "pve1-node",
            "vmid": 101,
            "net": "net0",
            "digest": "digest-1",
            "status": "failed",
            "message": "NIC disable failed",
        },
    )

    assert reply is not None
    assert reply.outcome == "failed"
    assert "did not complete successfully" in reply.summary


def test_proxmox_workflow_completed_reply_handles_nic_no_op_branch() -> None:
    reply = build_workflow_reply_template(
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
                    "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
                    "link_state": "down",
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
            "status": "no-op",
            "message": "already disabled",
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "after_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "link_state": "down",
            "verified": True,
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
    assert reply.outcome == "no_op"
    assert "already link down" in reply.summary

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


@pytest.mark.asyncio
async def test_cloudinit_postflight_skips_hash_check_when_password_is_none() -> None:
    """When new_password is None, postflight should still succeed if cloud-init
    confirms the password reset — it just skips the hash match check."""
    from noa_api.core.workflows.proxmox import postflight

    class _Client:
        async def get_qemu_cloudinit(self, node: str, vmid: int) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "$6$rounds=5000$salt$hash"}],
            }

        async def get_qemu_cloudinit_dump_user(
            self, node: str, vmid: int
        ) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "data": "password: $6$rounds=5000$salt$hash\n",
            }

    result = await postflight._cloudinit_postflight_result(
        client=_Client(),  # type: ignore[arg-type]
        node="pve1",
        vmid=101,
        new_password=None,
    )

    assert result is not None
    assert result["ok"] is True
    assert result["verified"] is True


# --- Edge case tests added for T5 audit fixes ---


@pytest.mark.asyncio
async def test_proxmox_pool_postflight_rejects_empty_vmids() -> None:
    from noa_api.core.workflows.proxmox import postflight

    class _Client:
        async def get_pool(self, poolid: str):
            return {
                "ok": True,
                "message": "ok",
                "data": [{"poolid": poolid, "members": []}],
            }

    result = await postflight._pool_postflight_result(
        client=_Client(),  # type: ignore[arg-type]
        source_pool="pool-a",
        destination_pool="pool-b",
        vmids=[],
    )

    assert result is not None
    assert result["ok"] is False
    assert result["error_code"] == "invalid_request"


@pytest.mark.asyncio
async def test_proxmox_pool_postflight_rejects_malformed_source_payload() -> None:
    from noa_api.core.workflows.proxmox import postflight

    class _Client:
        async def get_pool(self, poolid: str):
            if poolid == "pool-a":
                # Malformed: data is not a list
                return {"ok": True, "message": "ok", "data": "not-a-list"}
            return {
                "ok": True,
                "message": "ok",
                "data": [{"poolid": "pool-b", "members": [{"vmid": 101}]}],
            }

    result = await postflight._pool_postflight_result(
        client=_Client(),  # type: ignore[arg-type]
        source_pool="pool-a",
        destination_pool="pool-b",
        vmids=[101],
    )

    assert result is not None
    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


@pytest.mark.asyncio
async def test_proxmox_nic_postflight_returns_verified_false_when_link_state_wrong() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows
    from noa_api.core.workflows.proxmox.nic_connectivity import (
        ProxmoxVMNicConnectivityTemplate,
    )

    async def _fake_preflight(*, session, server_ref, node, vmid, net):
        return {
            "ok": True,
            "server_id": "srv-1",
            "node": node,
            "vmid": vmid,
            "digest": "digest-2",
            "net": net,
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
            "link_state": "down",
            "auto_selected_net": False,
            "nets": [],
        }

    original = proxmox_workflows.proxmox_preflight_vm_nic_toggle
    proxmox_workflows.proxmox_preflight_vm_nic_toggle = _fake_preflight
    try:
        template = ProxmoxVMNicConnectivityTemplate()
        result = await template.fetch_postflight_result(
            tool_name="proxmox_enable_vm_nic",
            args={
                "server_ref": "pve1",
                "node": "pve1-node",
                "vmid": 101,
                "net": "net0",
            },
            session=_FakeSession(),  # type: ignore[arg-type]
        )
    finally:
        proxmox_workflows.proxmox_preflight_vm_nic_toggle = original

    assert result is not None
    assert result["ok"] is True
    # NIC is still down but we wanted up → verified should be False
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_proxmox_nic_postflight_returns_verified_true_when_link_state_matches() -> (
    None
):
    from noa_api.core.workflows import proxmox as proxmox_workflows
    from noa_api.core.workflows.proxmox.nic_connectivity import (
        ProxmoxVMNicConnectivityTemplate,
    )

    async def _fake_preflight(*, session, server_ref, node, vmid, net):
        return {
            "ok": True,
            "server_id": "srv-1",
            "node": node,
            "vmid": vmid,
            "digest": "digest-2",
            "net": net,
            "before_net": "virtio=AA:BB:CC,bridge=vmbr0",
            "link_state": "up",
            "auto_selected_net": False,
            "nets": [],
        }

    original = proxmox_workflows.proxmox_preflight_vm_nic_toggle
    proxmox_workflows.proxmox_preflight_vm_nic_toggle = _fake_preflight
    try:
        template = ProxmoxVMNicConnectivityTemplate()
        result = await template.fetch_postflight_result(
            tool_name="proxmox_enable_vm_nic",
            args={
                "server_ref": "pve1",
                "node": "pve1-node",
                "vmid": 101,
                "net": "net0",
            },
            session=_FakeSession(),  # type: ignore[arg-type]
        )
    finally:
        proxmox_workflows.proxmox_preflight_vm_nic_toggle = original

    assert result is not None
    assert result["ok"] is True
    assert result["verified"] is True


def test_proxmox_cloudinit_completed_todos_downgrade_when_postflight_disagrees() -> (
    None
):
    workflow_todos = build_workflow_todos(
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
                    "cloudinit": {
                        "ok": True,
                        "message": "ok",
                        "data": [{"key": "cipassword", "value": "[redacted]"}],
                    },
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
        postflight_result={
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init verification did not confirm the password reset",
        },
    )

    assert workflow_todos is not None
    # Postflight disagreement downgrades verification
    assert workflow_todos[4]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_proxmox_cloudinit_postflight_sanitizes_cipassword() -> None:
    from noa_api.core.workflows.proxmox import postflight

    class _Client:
        async def get_qemu_cloudinit(self, node: str, vmid: int):
            return {
                "ok": True,
                "message": "ok",
                "data": [{"key": "cipassword", "value": "actual-secret-hash"}],
            }

        async def get_qemu_cloudinit_dump_user(self, node: str, vmid: int):
            return {
                "ok": True,
                "message": "ok",
                "data": f"password: {_SHA512_PASSWORD_HASH}\n",
            }

    result = await postflight._cloudinit_postflight_result(
        client=_Client(),  # type: ignore[arg-type]
        node="pve1",
        vmid=101,
        new_password="secret",
    )

    assert result is not None
    assert result["ok"] is True
    assert result["verified"] is True
    # cipassword value should be redacted in the sanitized cloudinit payload
    cloudinit = result["cloudinit"]
    assert isinstance(cloudinit, dict)
    data = cloudinit.get("data")
    assert isinstance(data, list)
    for entry in data:
        if isinstance(entry, dict) and entry.get("key") == "cipassword":
            assert entry["value"] == "[redacted]"
