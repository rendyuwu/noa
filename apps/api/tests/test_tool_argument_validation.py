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


def test_validate_tool_arguments_rejects_duplicate_csf_targets() -> None:
    tool = get_tool_definition("whm_csf_unblock")

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
