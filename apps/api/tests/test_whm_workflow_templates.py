from __future__ import annotations

from noa_api.core.workflows.registry import (
    build_approval_context,
    build_workflow_evidence_template,
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
    ]
    assert len(todos) == 5


def test_whm_firewall_waiting_for_approval_builds_target_specific_blocked_todos() -> (
    None
):
    todos = build_workflow_todos(
        tool_name="whm_firewall_unblock",
        workflow_family="whm-firewall-batch-change",
        args={
            "server_ref": "web2",
            "targets": ["5.6.7.8", "1.2.3.4"],
            "reason": "customer request",
        },
        phase="waiting_on_approval",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_firewall_entries",
                "args": {"server_ref": "web2", "target": "5.6.7.8"},
                "result": {
                    "ok": True,
                    "target": "5.6.7.8",
                    "matches": ["/etc/csf/csf.deny"],
                    "available_tools": {"csf": True, "imunify": False},
                    "combined_verdict": "blocked",
                },
            },
            {
                "toolName": "whm_preflight_firewall_entries",
                "args": {"server_ref": "web2", "target": "1.2.3.4"},
                "result": {
                    "ok": True,
                    "target": "1.2.3.4",
                    "matches": ["/etc/csf/csf.deny"],
                    "available_tools": {"csf": True, "imunify": True},
                    "combined_verdict": "blocked",
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
    ]
    assert "1.2.3.4" in todos[0]["content"]
    assert "5.6.7.8" in todos[0]["content"]
    assert "Request approval to unblock" in todos[2]["content"]
    assert "customer request" in todos[1]["content"]
    assert len(todos) == 5


def test_whm_account_contact_email_waiting_on_approval_builds_five_step_todos() -> None:
    todos = build_workflow_todos(
        tool_name="whm_change_contact_email",
        workflow_family="whm-account-contact-email",
        args={
            "server_ref": "web1",
            "username": "alice",
            "new_email": "new@example.com",
            "reason": "customer request",
        },
        phase="waiting_on_approval",
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
    )

    assert todos is not None
    assert len(todos) == 5
    assert [todo["status"] for todo in todos] == [
        "completed",
        "completed",
        "waiting_on_approval",
        "pending",
        "pending",
    ]
    assert "Request approval" in todos[2]["content"]


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


def test_whm_firewall_completed_reply_template_marks_mixed_results_as_partial() -> None:
    reply = build_workflow_reply_template(
        tool_name="whm_firewall_unblock",
        workflow_family="whm-firewall-batch-change",
        args={
            "server_ref": "web2",
            "targets": ["1.2.3.4", "5.6.7.8"],
            "reason": "customer request",
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_firewall_entries",
                "args": {"server_ref": "web2", "target": "1.2.3.4"},
                "result": {
                    "ok": True,
                    "target": "1.2.3.4",
                    "combined_verdict": "blocked",
                    "available_tools": {"csf": True, "imunify": True},
                    "matches": ["/etc/csf/csf.deny"],
                },
            },
            {
                "toolName": "whm_preflight_firewall_entries",
                "args": {"server_ref": "web2", "target": "5.6.7.8"},
                "result": {
                    "ok": True,
                    "target": "5.6.7.8",
                    "combined_verdict": "blocked",
                    "available_tools": {"csf": True, "imunify": False},
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
                {"target": "1.2.3.4", "combined_verdict": "not_found"},
                {"target": "5.6.7.8", "combined_verdict": "not_found"},
            ],
        },
    )

    assert reply is not None
    assert reply.outcome == "partial"
    assert reply.title == "Firewall change partially completed"
    assert "mixed results" in reply.summary
    assert "Changed targets: 1.2.3.4." in reply.evidence_summary
    assert "No-op targets: 5.6.7.8." in reply.evidence_summary


def test_whm_account_lifecycle_waiting_on_approval_evidence_has_canonical_sections() -> (
    None
):
    evidence = build_workflow_evidence_template(
        tool_name="whm_suspend_account",
        workflow_family="whm-account-lifecycle",
        args={
            "server_ref": "web1",
            "username": "alice",
            "reason": "billing hold",
        },
        phase="waiting_on_approval",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_account",
                "args": {"server_ref": "web1", "username": "alice"},
                "result": {
                    "ok": True,
                    "account": {
                        "user": "alice",
                        "suspended": False,
                        "domain": "example.com",
                    },
                },
            }
        ],
    )

    assert evidence is not None
    assert [section.key for section in evidence.sections] == [
        "before_state",
        "requested_change",
        "after_state",
        "verification",
    ]


def test_whm_contact_email_denied_evidence_uses_failure_section() -> None:
    evidence = build_workflow_evidence_template(
        tool_name="whm_change_contact_email",
        workflow_family="whm-account-contact-email",
        args={
            "server_ref": "web1",
            "username": "alice",
            "new_email": "new@example.com",
        },
        phase="denied",
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
    )

    assert evidence is not None
    assert evidence.sections[-1].key == "failure"
    assert evidence.sections[-1].items[0].value == "denied"


def test_whm_firewall_completed_evidence_tracks_per_target_outcomes() -> None:
    evidence = build_workflow_evidence_template(
        tool_name="whm_firewall_unblock",
        workflow_family="whm-firewall-batch-change",
        args={"server_ref": "web2", "targets": ["1.2.3.4", "5.6.7.8"]},
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_firewall_entries",
                "args": {"server_ref": "web2", "target": "1.2.3.4"},
                "result": {
                    "ok": True,
                    "target": "1.2.3.4",
                    "combined_verdict": "blocked",
                    "available_tools": {"csf": True, "imunify": True},
                    "csf": {
                        "verdict": "blocked",
                        "raw_output": (
                            "filter DENYIN\n\n"
                            "Temporary Blocks: IP:1.2.3.4 Port: Dir:in TTL:432000"
                            " (osTicket #121312)"
                        ),
                    },
                    "imunify": {
                        "verdict": "blacklisted",
                        "entries": [
                            {
                                "ip": "1.2.3.4",
                                "purpose": "drop",
                                "comment": "osTicket #121312",
                                "manual": True,
                                "expiration": 1775225644,
                                "country_name": "United States",
                            }
                        ],
                    },
                },
            }
        ],
        result={
            "ok": True,
            "results": [
                {"target": "1.2.3.4", "status": "changed"},
                {"target": "5.6.7.8", "status": "no-op"},
            ],
        },
        postflight_result={
            "ok": True,
            "results": [
                {
                    "target": "1.2.3.4",
                    "combined_verdict": "not_found",
                    "available_tools": {"csf": True, "imunify": True},
                    "csf": {
                        "verdict": "not_found",
                        "raw_output": "ip6tables:\n\nNo matches found for 1.2.3.4 in ip6tables",
                    },
                    "imunify": {"verdict": "not_found", "entries": []},
                },
                {
                    "target": "5.6.7.8",
                    "combined_verdict": "not_found",
                    "available_tools": {"csf": True, "imunify": False},
                    "csf": {
                        "verdict": "not_found",
                        "raw_output": "ip6tables:\n\nNo matches found for 5.6.7.8 in ip6tables",
                    },
                },
            ],
        },
    )

    assert evidence is not None
    assert [section.key for section in evidence.sections] == [
        "before_state",
        "requested_change",
        "after_state",
        "outcomes",
        "verification",
    ]
    before_state = next(
        section for section in evidence.sections if section.key == "before_state"
    )
    after_state = next(
        section for section in evidence.sections if section.key == "after_state"
    )
    assert any(
        item.label == "1.2.3.4 · CSF"
        and item.value
        == "Temporary Blocks: IP:1.2.3.4 Port: Dir:in TTL:432000 (osTicket #121312)"
        for item in before_state.items
    )
    assert any(
        item.label == "1.2.3.4 · Imunify"
        and "Blacklist entry for 1.2.3.4" in item.value
        for item in before_state.items
    )
    assert any(
        item.label == "1.2.3.4 · CSF"
        and item.value == "No matches found for 1.2.3.4 in ip6tables"
        for item in after_state.items
    )
    assert any(
        item.label == "1.2.3.4 · Imunify"
        and item.value == "No Imunify entry found for 1.2.3.4"
        for item in after_state.items
    )
    outcomes = next(
        section for section in evidence.sections if section.key == "outcomes"
    )
    assert any(
        item.label == "Changed" and "1.2.3.4" in item.value for item in outcomes.items
    )
    assert any(
        item.label == "No-op" and "5.6.7.8" in item.value for item in outcomes.items
    )


def test_whm_firewall_allowlist_remove_completed_evidence_includes_human_readable_after_state() -> (
    None
):
    evidence = build_workflow_evidence_template(
        tool_name="whm_firewall_allowlist_remove",
        workflow_family="whm-firewall-batch-change",
        args={"server_ref": "web2", "targets": ["103.103.11.123"]},
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_firewall_entries",
                "args": {"server_ref": "web2", "target": "103.103.11.123"},
                "result": {
                    "ok": True,
                    "target": "103.103.11.123",
                    "combined_verdict": "allowlisted",
                    "available_tools": {"csf": True, "imunify": True},
                    "csf": {
                        "verdict": "allowlisted",
                        "raw_output": (
                            "filter ALLOWIN\n\n"
                            "Temporary Allows: IP:103.103.11.123 Port: Dir:inout TTL:432000"
                        ),
                    },
                    "imunify": {
                        "verdict": "whitelisted",
                        "entries": [
                            {
                                "ip": "103.103.11.123",
                                "purpose": "white",
                                "manual": True,
                            }
                        ],
                    },
                },
            }
        ],
        result={
            "ok": True,
            "results": [{"target": "103.103.11.123", "status": "changed"}],
        },
        postflight_result={
            "ok": True,
            "results": [
                {
                    "target": "103.103.11.123",
                    "combined_verdict": "not_found",
                    "available_tools": {"csf": True, "imunify": True},
                    "csf": {
                        "verdict": "not_found",
                        "raw_output": "ip6tables:\n\nNo matches found for 103.103.11.123 in ip6tables",
                    },
                    "imunify": {"verdict": "not_found", "entries": []},
                }
            ],
        },
    )

    assert evidence is not None
    before_state = next(
        section for section in evidence.sections if section.key == "before_state"
    )
    after_state = next(
        section for section in evidence.sections if section.key == "after_state"
    )
    assert any(
        item.label == "103.103.11.123 · CSF"
        and item.value
        == "Temporary Allows: IP:103.103.11.123 Port: Dir:inout TTL:432000"
        for item in before_state.items
    )
    assert any(
        item.label == "103.103.11.123 · Imunify"
        and item.value == "Whitelist entry for 103.103.11.123; manual"
        for item in before_state.items
    )
    assert any(
        item.label == "103.103.11.123 · CSF"
        and item.value == "No matches found for 103.103.11.123 in ip6tables"
        for item in after_state.items
    )
    assert any(
        item.label == "103.103.11.123 · Imunify"
        and item.value == "No Imunify entry found for 103.103.11.123"
        for item in after_state.items
    )


def test_whm_firewall_add_ttl_completed_evidence_includes_requested_ttl() -> None:
    evidence = build_workflow_evidence_template(
        tool_name="whm_firewall_allowlist_add_ttl",
        workflow_family="whm-firewall-batch-change",
        args={
            "server_ref": "web2",
            "targets": ["103.103.11.123"],
            "duration_minutes": 60,
        },
        phase="completed",
        preflight_evidence=[
            {
                "toolName": "whm_preflight_firewall_entries",
                "args": {"server_ref": "web2", "target": "103.103.11.123"},
                "result": {
                    "ok": True,
                    "target": "103.103.11.123",
                    "combined_verdict": "not_found",
                    "available_tools": {"csf": True, "imunify": True},
                    "csf": {
                        "verdict": "not_found",
                        "raw_output": "ip6tables:\n\nNo matches found for 103.103.11.123 in ip6tables",
                    },
                    "imunify": {"verdict": "not_found", "entries": []},
                },
            }
        ],
        result={
            "ok": True,
            "results": [{"target": "103.103.11.123", "status": "changed"}],
        },
        postflight_result={
            "ok": True,
            "results": [
                {
                    "target": "103.103.11.123",
                    "combined_verdict": "allowlisted",
                    "available_tools": {"csf": True, "imunify": True},
                    "csf": {
                        "verdict": "allowlisted",
                        "raw_output": "Header\n\nTemporary Allows: IP:103.103.11.123",
                    },
                    "imunify": {
                        "verdict": "whitelisted",
                        "entries": [
                            {
                                "ip": "103.103.11.123",
                                "purpose": "white",
                                "comment": "Ticket #232123",
                                "manual": True,
                            }
                        ],
                    },
                }
            ],
        },
    )

    assert evidence is not None
    requested_change = next(
        section for section in evidence.sections if section.key == "requested_change"
    )
    after_state = next(
        section for section in evidence.sections if section.key == "after_state"
    )
    assert any(
        item.label == "Requested TTL" and item.value == "60 minute(s)"
        for item in requested_change.items
    )
    assert any(
        item.label == "103.103.11.123 · CSF"
        and item.value == "Temporary Allows: IP:103.103.11.123"
        for item in after_state.items
    )
    assert any(
        item.label == "103.103.11.123 · Imunify"
        and "Whitelist entry for 103.103.11.123" in item.value
        for item in after_state.items
    )


def test_build_approval_context_includes_evidence_sections_and_compat_before_state() -> (
    None
):
    context = build_approval_context(
        tool_name="whm_suspend_account",
        workflow_family="whm-account-lifecycle",
        args={
            "server_ref": "web1",
            "username": "alice",
            "reason": "billing hold",
        },
        working_messages=[
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "args": {"server_ref": "web1", "username": "alice"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "whm_preflight_account",
                        "toolCallId": "preflight-1",
                        "result": {
                            "ok": True,
                            "account": {"user": "alice", "suspended": False},
                        },
                        "isError": False,
                    }
                ],
            },
        ],
    )

    assert "evidenceSections" in context
    assert isinstance(context["evidenceSections"], list)
    assert context["beforeState"]
