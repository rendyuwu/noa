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
                "ok": False,
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
