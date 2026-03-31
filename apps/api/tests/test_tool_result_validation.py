from __future__ import annotations

import pytest

from noa_api.core.tools.registry import get_tool_definition
from noa_api.core.tools.result_validation import (
    ToolResultValidationError,
    validate_tool_result,
)


def test_validate_tool_result_accepts_workflow_success_payload() -> None:
    tool = get_tool_definition("update_workflow_todo")

    assert tool is not None

    validate_tool_result(
        tool=tool,
        result={
            "ok": True,
            "todos": [
                {
                    "content": "Request approval",
                    "status": "waiting_on_approval",
                    "priority": "high",
                }
            ],
        },
    )


def test_validate_tool_result_rejects_missing_required_fields() -> None:
    tool = get_tool_definition("set_demo_flag")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(tool=tool, result={"ok": True})

    assert exc_info.value.details == ("Missing required field 'flag'",)


def test_validate_tool_result_rejects_invalid_firewall_item_shape() -> None:
    tool = get_tool_definition("whm_firewall_unblock")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "available_tools": {"csf": True, "imunify": False},
                "results": [
                    {
                        "target": "1.2.3.4",
                        "ok": False,
                        "status": "error",
                        "message": "missing code",
                    }
                ],
            },
        )

    assert exc_info.value.details == (
        "Missing required field 'results[0].available_tools'",
    )


def test_validate_tool_result_rejects_whm_resolution_choices_with_missing_fields() -> (
    None
):
    tool = get_tool_definition("whm_list_accounts")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": False,
                "error_code": "host_ambiguous",
                "message": "Multiple WHM servers match 'web1'",
                "choices": [{"id": "srv-1", "name": "web1"}],
            },
        )

    assert exc_info.value.details == ("Missing required field 'choices[0].base_url'",)


def test_validate_tool_result_rejects_server_list_items_missing_required_fields() -> (
    None
):
    tool = get_tool_definition("whm_list_servers")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "servers": [
                    {
                        "id": "srv-1",
                        "base_url": "https://whm.example.com:2087",
                        "api_username": "root",
                        "verify_ssl": True,
                        "created_at": "2026-03-16T00:00:00+00:00",
                        "updated_at": "2026-03-16T00:00:00+00:00",
                    }
                ],
            },
        )

    assert exc_info.value.details == ("Missing required field 'servers[0].name'",)


def test_validate_tool_result_rejects_preflight_account_success_without_server_id() -> (
    None
):
    tool = get_tool_definition("whm_preflight_account")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "account": {"user": "alice"},
            },
        )

    assert exc_info.value.details == ("Missing required field 'server_id'",)


def test_validate_tool_result_rejects_account_items_without_user() -> None:
    tool = get_tool_definition("whm_list_accounts")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "accounts": [{"domain": "alice.example.com"}],
            },
        )

    assert exc_info.value.details == ("Missing required field 'accounts[0].user'",)


def test_validate_tool_result_rejects_unexpected_account_fields() -> None:
    tool = get_tool_definition("whm_preflight_account")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "server_id": "srv-1",
                "account": {
                    "user": "alice",
                    "domain": "alice.example.com",
                    "owner": "root",
                },
            },
        )

    assert exc_info.value.details == ("Unexpected field 'account.owner'",)


def test_validate_tool_result_rejects_primary_domain_preflight_without_requested_domain() -> (
    None
):
    tool = get_tool_definition("whm_preflight_primary_domain_change")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "server_id": "srv-1",
                "account": {"user": "alice"},
                "domain_owner": None,
                "requested_domain_location": "absent",
                "safe_to_change": True,
                "domain_inventory": {
                    "main_domain": "old.example.com",
                    "addon_domains": [],
                    "parked_domains": [],
                    "sub_domains": [],
                },
            },
        )

    assert exc_info.value.details == ("Missing required field 'requested_domain'",)


def test_validate_tool_result_rejects_proxmox_preflight_without_net() -> None:
    tool = get_tool_definition("proxmox_preflight_vm_nic_toggle")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "server_id": "srv-1",
                "node": "pve1",
                "vmid": 101,
                "digest": "abc123",
                "before_net": "virtio=AA:BB:CC,bridge=vmbr0",
                "link_state": "up",
                "auto_selected_net": True,
                "nets": [],
            },
        )

    assert exc_info.value.details == ("Missing required field 'net'",)


def test_validate_tool_result_rejects_proxmox_change_without_verified() -> None:
    tool = get_tool_definition("proxmox_disable_vm_nic")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
                "server_id": "srv-1",
                "node": "pve1",
                "vmid": 101,
                "net": "net0",
                "digest": "abc123",
                "status": "changed",
                "message": "NIC disabled",
                "before_net": "virtio=AA:BB:CC,bridge=vmbr0",
                "after_net": "virtio=AA:BB:CC,bridge=vmbr0,link_down=1",
                "link_state": "down",
                "upid": "UPID:pve1:00000001:task",
                "task_status": "stopped",
                "task_exit_status": "OK",
            },
        )

    assert exc_info.value.details == ("Missing required field 'verified'",)
