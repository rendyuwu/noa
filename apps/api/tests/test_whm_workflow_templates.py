from __future__ import annotations

from noa_api.core.workflows.registry import (
    build_workflow_reply_template,
    build_workflow_todos,
)


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


def test_whm_account_contact_email_completed_reply_template_summarizes_change() -> None:
    reply = build_workflow_reply_template(
        tool_name="whm_change_contact_email",
        workflow_family="whm-account-contact-email",
        args={
            "server_ref": "web1",
            "username": "alice",
            "new_email": "new@example.com",
            "reason": "customer request",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_account",
                "args": {"server_ref": "web1", "username": "alice"},
                "result": {
                    "ok": True,
                    "account": {
                        "user": "alice",
                        "contactemail": "old@example.com",
                    },
                },
            }
        ],
        result={"ok": True, "status": "changed", "message": "Contact email updated"},
        postflight_result={
            "ok": True,
            "account": {"user": "alice", "contactemail": "new@example.com"},
        },
    )

    assert reply is not None
    assert reply.outcome == "changed"
    assert reply.title == "Contact email change completed"
    assert "moved from 'old@example.com' to 'new@example.com'" in reply.summary
    assert "Tool result: Contact email updated." in reply.evidence_summary


def test_whm_account_lifecycle_denied_reply_template_preserves_no_change_language() -> (
    None
):
    reply = build_workflow_reply_template(
        tool_name="whm_suspend_account",
        workflow_family="whm-account-lifecycle",
        args={
            "server_ref": "web1",
            "username": "alice",
            "reason": "billing hold",
        },
        phase="denied",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_account",
                "args": {"server_ref": "web1", "username": "alice"},
                "result": {
                    "ok": True,
                    "account": {"user": "alice", "suspended": False},
                },
            }
        ],
    )

    assert reply is not None
    assert reply.outcome == "denied"
    assert reply.title == "Suspend denied"
    assert "was denied" in reply.summary
    assert reply.next_step is not None and "new approval request" in reply.next_step


def test_whm_csf_completed_reply_template_marks_mixed_results_as_partial() -> None:
    reply = build_workflow_reply_template(
        tool_name="whm_csf_unblock",
        workflow_family="whm-csf-batch-change",
        args={
            "server_ref": "web2",
            "targets": ["1.2.3.4", "5.6.7.8"],
            "reason": "customer request",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_csf_entries",
                "args": {"server_ref": "web2", "target": "1.2.3.4"},
                "result": {
                    "ok": True,
                    "target": "1.2.3.4",
                    "verdict": "blocked",
                    "matches": ["/etc/csf/csf.deny"],
                },
            },
            {
                "toolName": "whm_preflight_csf_entries",
                "args": {"server_ref": "web2", "target": "5.6.7.8"},
                "result": {
                    "ok": True,
                    "target": "5.6.7.8",
                    "verdict": "blocked",
                    "matches": ["/etc/csf/csf.deny"],
                },
            },
        ],
        result={
            "ok": True,
            "results": [
                {"target": "1.2.3.4", "ok": True, "status": "changed"},
                {"target": "5.6.7.8", "ok": True, "status": "no-op"},
            ],
        },
        postflight_result={
            "ok": True,
            "results": [
                {"target": "1.2.3.4", "verdict": "clear"},
                {"target": "5.6.7.8", "verdict": "clear"},
            ],
        },
    )

    assert reply is not None
    assert reply.outcome == "partial"
    assert reply.title == "CSF change partially completed"
    assert "mixed results" in reply.summary
    assert "Changed targets: 1.2.3.4." in reply.evidence_summary
    assert "No-op targets: 5.6.7.8." in reply.evidence_summary
