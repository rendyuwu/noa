from __future__ import annotations

import pytest

from noa_api.core.tools.argument_validation import (
    ToolArgumentValidationError,
    validate_tool_arguments,
)
from noa_api.core.tools.registry import get_tool_definition


def test_validate_tool_arguments_rejects_whitespace_only_required_strings() -> None:
    tool = get_tool_definition("whm_search_accounts")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={"server_ref": "web1", "query": "   "},
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["query must not be blank"],
    }


def test_validate_tool_arguments_accepts_trimmed_email_strings() -> None:
    tool = get_tool_definition("whm_change_contact_email")

    assert tool is not None

    validate_tool_arguments(
        tool=tool,
        args={
            "server_ref": "web1",
            "username": "alice",
            "new_email": " alice@example.com ",
            "reason": "customer request",
        },
    )


def test_validate_tool_arguments_accepts_trimmed_primary_domain_strings() -> None:
    tool = get_tool_definition("whm_change_primary_domain")

    assert tool is not None

    validate_tool_arguments(
        tool=tool,
        args={
            "server_ref": "web1",
            "username": "alice",
            "new_domain": " new.example.com ",
            "reason": "customer request",
        },
    )


def test_validate_tool_arguments_accepts_ticket_style_proxmox_reason() -> None:
    tool = get_tool_definition("proxmox_disable_vm_nic")

    assert tool is not None

    validate_tool_arguments(
        tool=tool,
        args={
            "server_ref": "pve1",
            "node": "node1",
            "vmid": 100,
            "net": "net0",
            "digest": "abc123",
            "reason": "CHG-12345: customer-approved NIC maintenance",
        },
    )


def test_validate_tool_arguments_accepts_ticket_style_proxmox_enable_reason() -> None:
    tool = get_tool_definition("proxmox_enable_vm_nic")

    assert tool is not None

    validate_tool_arguments(
        tool=tool,
        args={
            "server_ref": "pve1",
            "node": "node1",
            "vmid": 100,
            "net": "net0",
            "digest": "abc123",
            "reason": "Ticket #1661262: restore NIC connectivity",
        },
    )


def test_validate_tool_arguments_rejects_blank_proxmox_reason() -> None:
    tool = get_tool_definition("proxmox_disable_vm_nic")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={
                "server_ref": "pve1",
                "node": "node1",
                "vmid": 100,
                "net": "net0",
                "digest": "abc123",
                "reason": "   ",
            },
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["reason must not be blank"],
    }


def test_validate_tool_arguments_rejects_duplicate_firewall_targets() -> None:
    tool = get_tool_definition("whm_firewall_unblock")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={
                "server_ref": "web1",
                "targets": ["1.2.3.4", " 1.2.3.4 "],
                "reason": "customer unblock",
            },
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["targets must not contain duplicate values: '1.2.3.4'"],
    }


def test_validate_tool_arguments_rejects_invalid_server_ref() -> None:
    tool = get_tool_definition("whm_list_accounts")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={"server_ref": "https://whm.example.com:2087"},
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["server_ref must be a valid WHM server reference"],
    }


def test_validate_tool_arguments_rejects_invalid_whm_username() -> None:
    tool = get_tool_definition("whm_suspend_account")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={
                "server_ref": "web1",
                "username": "alice@example.com",
                "reason": "customer request",
            },
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["username must be a valid WHM username"],
    }


def test_validate_tool_arguments_rejects_invalid_firewall_target() -> None:
    tool = get_tool_definition("whm_firewall_unblock")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={
                "server_ref": "web1",
                "targets": ["bad_target"],
                "reason": "customer unblock",
            },
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["targets[0] must be a valid CSF target"],
    }


def test_validate_tool_arguments_rejects_blank_proxmox_email() -> None:
    tool = get_tool_definition("proxmox_get_user_by_email")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={"server_ref": "pve1", "email": "   "},
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": [
            "email must not be blank",
            "email must be a valid email address",
        ],
    }


def test_validate_tool_arguments_rejects_duplicate_vmids_for_pool_move() -> None:
    tool = get_tool_definition("proxmox_move_vms_between_pools")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={
                "server_ref": "pve1",
                "source_pool": "source",
                "destination_pool": "dest",
                "vmids": [100, 100],
                "old_email": "owner@example.com",
                "new_email": "newowner@example.com",
                "reason": "move pool membership",
            },
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["vmids must not contain duplicate values: '100'"],
    }


@pytest.mark.parametrize("vmids", ([0], [-1]))
def test_validate_tool_arguments_rejects_non_positive_pool_move_vmids(
    vmids: list[int],
) -> None:
    tool = get_tool_definition("proxmox_move_vms_between_pools")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={
                "server_ref": "pve1",
                "source_pool": "source",
                "destination_pool": "dest",
                "vmids": vmids,
                "old_email": "owner@example.com",
                "new_email": "newowner@example.com",
                "reason": "move pool membership",
            },
        )

    assert exc_info.value.as_result() == {
        "error": "Tool arguments are invalid",
        "error_code": "invalid_tool_arguments",
        "details": ["vmids[0] must be greater than or equal to 1"],
    }


def test_validate_tool_arguments_accepts_proxmox_email_with_pve_realm() -> None:
    """V70: Proxmox email params accept ``user@domain.com@pve`` userid format."""
    tool = get_tool_definition("proxmox_get_user_by_email")

    assert tool is not None

    # Should not raise — @pve suffix is valid for Proxmox emails
    validate_tool_arguments(
        tool=tool,
        args={"server_ref": "pve1", "email": "l1@biznetgio.com@pve"},
    )


def test_validate_tool_arguments_accepts_proxmox_email_without_pve_realm() -> None:
    """V70: Proxmox email params also accept plain email without @pve."""
    tool = get_tool_definition("proxmox_get_user_by_email")

    assert tool is not None

    validate_tool_arguments(
        tool=tool,
        args={"server_ref": "pve1", "email": "l1@biznetgio.com"},
    )


def test_validate_tool_arguments_rejects_invalid_proxmox_email() -> None:
    """V70: Proxmox email params still reject truly invalid emails."""
    tool = get_tool_definition("proxmox_get_user_by_email")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={"server_ref": "pve1", "email": "not-an-email"},
        )

    assert "email must be a valid email address" in exc_info.value.as_result()["details"]


def test_validate_tool_arguments_accepts_pool_move_emails_with_pve_realm() -> None:
    """V70: old_email and new_email accept @pve suffix in pool move."""
    tool = get_tool_definition("proxmox_preflight_move_vms_between_pools")

    assert tool is not None

    validate_tool_arguments(
        tool=tool,
        args={
            "server_ref": "pve1",
            "source_pool": "pool_a",
            "destination_pool": "pool_b",
            "vmids": [1040],
            "old_email": "l1@biznetgio.com@pve",
            "new_email": "l2@biznetgio.com@pve",
        },
    )


@pytest.mark.parametrize(
    "email",
    [
        "@pve",
        "@@pve",
        "user@@pve",
        "not-an-email@pve",
    ],
)
def test_validate_tool_arguments_rejects_proxmox_email_edge_cases(
    email: str,
) -> None:
    """V70: @pve suffix stripping must still reject malformed underlying email."""
    tool = get_tool_definition("proxmox_get_user_by_email")

    assert tool is not None

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool=tool,
            args={"server_ref": "pve1", "email": email},
        )

    details = exc_info.value.as_result()["details"]
    assert any("email must be" in d for d in details)
