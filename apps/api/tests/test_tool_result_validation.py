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
                {"content": "Preflight", "status": "in_progress", "priority": "high"}
            ],
        },
    )


def test_validate_tool_result_rejects_missing_required_fields() -> None:
    tool = get_tool_definition("set_demo_flag")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(tool=tool, result={"ok": True})

    assert exc_info.value.details == ("Missing required field 'flag'",)


def test_validate_tool_result_rejects_invalid_csf_error_item_shape() -> None:
    tool = get_tool_definition("whm_csf_unblock")

    assert tool is not None

    with pytest.raises(ToolResultValidationError) as exc_info:
        validate_tool_result(
            tool=tool,
            result={
                "ok": True,
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

    assert exc_info.value.details == ("Missing required field 'results[0].error_code'",)


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
