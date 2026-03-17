from __future__ import annotations

from noa_api.core.workflows.registry import build_workflow_todos


def test_whm_account_lifecycle_failed_phase_keeps_terminal_todo_shape() -> None:
    todos = build_workflow_todos(
        tool_name="whm_suspend_account",
        workflow_family="whm-account-lifecycle",
        args={
            "server_ref": "web1",
            "username": "alice",
            "reason": "billing hold",
        },
        phase="failed",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_account",
                "args": {"server_ref": "web1", "username": "alice"},
                "result": {
                    "ok": True,
                    "account": {
                        "user": "alice",
                        "suspended": False,
                    },
                },
            }
        ],
        error_code="tool_execution_failed",
    )

    assert todos is not None
    assert [todo["status"] for todo in todos] == [
        "completed",
        "completed",
        "completed",
        "cancelled",
        "cancelled",
        "completed",
    ]
    assert "tool_execution_failed" in todos[5]["content"]
    assert "billing hold" in todos[5]["content"]


def test_whm_csf_waiting_for_approval_builds_target_specific_blocked_todos() -> None:
    todos = build_workflow_todos(
        tool_name="whm_csf_unblock",
        workflow_family="whm-csf-batch-change",
        args={
            "server_ref": "web2",
            "targets": ["5.6.7.8", "1.2.3.4"],
            "reason": "customer request",
        },
        phase="waiting_on_approval",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_csf_entries",
                "args": {"server_ref": "web2", "target": "5.6.7.8"},
                "result": {
                    "ok": True,
                    "target": "5.6.7.8",
                    "matches": ["/etc/csf/csf.deny"],
                    "verdict": "blocked",
                },
            },
            {
                "toolName": "whm_preflight_csf_entries",
                "args": {"server_ref": "web2", "target": "1.2.3.4"},
                "result": {
                    "ok": True,
                    "target": "1.2.3.4",
                    "matches": ["/etc/csf/csf.deny"],
                    "verdict": "blocked",
                },
            },
        ],
    )

    assert todos is not None
    assert [todo["status"] for todo in todos] == [
        "completed",
        "completed",
        "waiting_on_approval",
        "pending",
        "pending",
        "pending",
    ]
    assert "1.2.3.4" in todos[0]["content"]
    assert "5.6.7.8" in todos[0]["content"]
    assert "remove CSF blocks" in todos[2]["content"]
    assert "customer request" in todos[5]["content"]
