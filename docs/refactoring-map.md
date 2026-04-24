# API Refactoring Map

Quick-reference for debugging after the large-file refactoring on branch `refactor/api-large-file-split`.
If something breaks, use this to trace where code moved.

---

## Where Did My Code Go?

### `core/workflows/whm.py` (3,150 lines) → `core/workflows/whm/` package

| Old location (line range) | New file | What's in it |
|---|---|---|
| 33–64 | `whm/base.py` | `_WHMTemplate`, `_WHMAccountTemplate` base classes |
| 912–1088 | `whm/common.py` | `_format_argument_value`, `_extract_before_state`, `_result_ok/status/message/error_code`, `_account_subject`, `_action_label`, `_account_state/email/domain`, `_domain_inventory`, `_join_with_and`, `_approval_sentence_summary` |
| 661–810 | `whm/matching.py` | `_require_account_preflight`, `_require_primary_domain_preflight`, `_server_identity_matches`, `_matching_account_preflight`, `_matching_primary_domain_preflight`, `_postflight_account`, `_account_preflight_candidates`, `_primary_domain_preflight_candidates` |
| 812–909 | `whm/inference.py` | `_latest_user_text`, `_infer_whm_account_lifecycle_tool_name`, `_select_account_preflight_candidate`, `_select_primary_domain_preflight_candidate`, `_extract_email`, `_extract_domain` |
| 1948–2261 | `whm/todo_helpers.py` | `_preflight_step_content`, `_reason_step_content`, `_postflight_step_content`, `_conclusion_step_content`, `_account_before_state_items`, `_account_after_state_items`, all `_primary_domain_*_step_content`, `_contact_email_*_step_content`, `_firewall_*_step_content` |
| 121–258, 1092–1635 | `whm/account_lifecycle.py` | `WHMAccountLifecycleTemplate`, `_build_account_lifecycle_reply_template_impl`, `_build_account_lifecycle_evidence_template` |
| 259–374, 1226–1755 | `whm/contact_email.py` | `WHMAccountContactEmailTemplate`, `_build_contact_email_reply_template_impl`, `_build_contact_email_evidence_template` |
| 376–533, 1361–1941 | `whm/primary_domain.py` | `WHMAccountPrimaryDomainTemplate`, `_build_primary_domain_reply_template_impl`, `_build_primary_domain_evidence_template` |
| 535–650, 2412–3150 | `whm/firewall.py` | `WHMFirewallBatchTemplate`, all `_firewall_*` helpers, `_build_firewall_reply_template`, `_build_firewall_evidence_template` |
| 653–658 | `whm/__init__.py` | `WORKFLOW_TEMPLATES` dict |

**Shared approval helpers** (`_approval_detail_rows`, `_approval_paragraph_block`, etc.) moved to `core/workflows/approval.py`.

---

### `core/workflows/proxmox.py` (2,640 lines) → `core/workflows/proxmox/` package

| Old location | New file | What's in it |
|---|---|---|
| 383–438 | `proxmox/common.py` | `_normalized_int`, `_action_label`, `_subject`, `_title_subject`, `_workflow_result_failed`, `_vmids_text`, `_pool_value`, `_upstream_error`, `_reason_step_content`, `_link_state`, `_approval_table_block` |
| 441–589 | `proxmox/matching.py` | `_server_identity_matches`, `_matching_preflight`, `_require_vm_nic_preflight`, `_matching_cloudinit_preflight`, `_require_cloudinit_preflight`, `_matching_pool_move_preflight`, `_require_pool_move_preflight` |
| 1481–1624 | `proxmox/postflight.py` | `_resolve_proxmox_client`, `_cloudinit_postflight_result`, `_pool_postflight_result`, `_wait_for_cloudinit_verification` |
| 32–381 | `proxmox/nic_connectivity.py` | `ProxmoxVMNicConnectivityTemplate` + NIC helpers |
| 800–1145 | `proxmox/cloudinit_password_reset.py` | `ProxmoxVMCloudinitPasswordResetTemplate` + cloud-init helpers |
| 1147–1471 | `proxmox/pool_membership_move.py` | `ProxmoxPoolMembershipMoveTemplate` + pool helpers |
| 2636–2640 | `proxmox/__init__.py` | `WORKFLOW_TEMPLATES` dict |

---

### `core/agent/runner.py` (2,157 lines) → `core/agent/` package

| Old location | New file | What's in it |
|---|---|---|
| 49–68, 273–402, 1226–1235, 2152 | `agent/llm_client.py` | `LLMToolCall`, `LLMTurnResponse`, `LLMClientProtocol`, `OpenAICompatibleLLMClient`, `create_default_llm_client`, `_split_text_deltas` |
| 72–80, 84–86, 120–261, 1238–1370 | `agent/message_codec.py` | `AgentMessage`, `AgentRunnerResult`, `ProcessedToolCall`, `_as_object_dict`, `_assistant_message_parts`, `_append_assistant_text_*`, `_message_visible_text`, `_finalize_turn_messages`, `_prompt_replay_parts`, `_to_openai_chat_messages`, `_safe_json_object`, `_extract_reasoning_summary` |
| 1622–1655 | `agent/tool_schemas.py` | `_build_approval_context`, `_to_openai_tool_schema`, `_llm_tool_description`, `_tool_risk_note` |
| 1658–1970 | `agent/guidance.py` | `_tool_error_messages`, `_assistant_guidance_for_change_validation_error`, `_internal_tool_guidance`, `_should_stop_after_internal_tool_guidance`, `_preflight_retry_guidance`, `_preflight_user_retry_reply`, `_extract_firewall_preflight_raw_outputs`, `_render_firewall_preflight_raw_output`, `_append_firewall_preflight_raw_output` |
| 1730–2137 | `agent/fallbacks.py` | `_latest_tool_result_part`, `_tool_call_args_for_id`, `_canonical_tool_args`, `_working_messages_after_part`, `_has_fresh_matching_preflight_after_failed_tool_result`, `_latest_matching_failed_tool_result_part`, `_assistant_reply_from_tool_result_part`, `_generic_read_success_fallback`, `_generic_read_result_count`, `_infer_waiting_on_user_workflow_from_messages` |
| 1395–1593 | `agent/change_validation.py` | `_normalized_text`, `_reason_provenance_tokens`, `_validate_change_reason_provenance`, `_canonicalize_reason_follow_up_args`, `_matches_reason_follow_up_workflow_action`, `_tool_args_without_reason`, `_message_has_text` |
| 1380, 1606 | `core/workflows/preflight_validation.py` | `validate_matching_preflight` (was `_require_matching_preflight`), `resolve_requested_server_id` (was `_resolve_requested_server_id`) |
| 405–1223 (remaining) | `agent/runner.py` (~960 lines) | `AgentRunner` class, `_workflow_todo_tool_messages`, all re-exports |

**All symbols remain importable from `noa_api.core.agent.runner`** via re-exports.

---

### `core/tools/registry.py` (1,683 lines) → `core/tools/` restructured

| Old location | New file | What's in it |
|---|---|---|
| 58–72 | `tools/types.py` | `ToolExecutor`, `ToolParametersSchema`, `ToolResultSchema`, `ToolDefinition` |
| 75–265 | `tools/schema_builders.py` | `_object_schema`, `_string_param`, `_integer_param`, all `_result_*` builders |
| 267–979 (constants) | `tools/schemas/common.py` | Shared schemas (server choice, reason, todo, generic success/error) |
| 267–979 (constants) | `tools/schemas/whm.py` | WHM-specific result schemas |
| 267–979 (constants) | `tools/schemas/proxmox.py` | Proxmox-specific result schemas |
| 980–1670 (`_MVP_TOOLS`) | `tools/definitions/common.py` | 3 common tool defs (time, date, workflow_todo) |
| 980–1670 (`_MVP_TOOLS`) | `tools/definitions/whm.py` | 17 WHM tool defs |
| 980–1670 (`_MVP_TOOLS`) | `tools/definitions/proxmox.py` | 13 Proxmox tool defs |
| 980–1670 (`_MVP_TOOLS`) | `tools/definitions/__init__.py` | `ALL_TOOLS` = common + whm + proxmox |
| 1674–1683 | `tools/registry.py` (~20 lines) | `get_tool_registry()`, `get_tool_definition()`, `get_tool_names()` |

---

### `whm/tools/firewall_tools.py` (1,072 lines) → `whm/tools/firewall_tools/` package

| Old location | New file | What's in it |
|---|---|---|
| 33–68 | `firewall_tools/common.py` | `_LFD_AUTH_LINE_RE`, `_extract_lfd_auth_line`, `_resolution_error`, `_no_firewall_tools_error`, `_compute_combined_verdict` |
| 81–155 | `firewall_tools/csf_backend.py` | `_csf_preflight`, `_csf_unblock`, `_csf_allowlist_add_ttl`, `_csf_allowlist_remove`, `_csf_denylist_add_ttl` |
| 162–265 | `firewall_tools/imunify_backend.py` | `_imunify_preflight`, `_imunify_blacklist_remove`, `_imunify_whitelist_add_ttl`, `_imunify_whitelist_remove`, `_imunify_blacklist_add_ttl` |
| 294–1072 | `firewall_tools/__init__.py` | Public tool handlers: `whm_preflight_firewall_entries`, `whm_firewall_unblock`, `whm_firewall_allowlist_add_ttl`, `whm_firewall_allowlist_remove`, `whm_firewall_denylist_add_ttl` |

---

### `api/assistant/assistant_action_operations.py` (1,036 lines) → 3 modules (DELETED)

| Old location | New file | What's in it |
|---|---|---|
| 61–177 | `api/assistant/workflow_emission.py` | `_has_recorded_change_reason`, `_emit_update_workflow_todo_messages`, `_emit_workflow_receipt_messages`, `_build_change_receipt_v1`, `ApprovedToolExecutor` Protocol |
| 180–469 | `api/assistant/action_requests.py` | `require_pending_action_request`, `deny_action_request`, `approve_action_request` |
| 472–1036 | `api/assistant/approved_execution.py` | `execute_approved_tool_run`, `_validate_approved_tool_preflight`, `_list_working_messages`, `_execute_tool`, `_persist_failed_tool_run` |

**Original file deleted.** No re-export hub. All imports updated.

---

### `api/routes/assistant.py` (1,231 lines) → extracted into `api/assistant/`

| Old location | New file | What's in it |
|---|---|---|
| 100–158 | `api/assistant/schemas.py` | `AssistantThreadStateMessage`, `AssistantWorkflowTodo`, `AssistantPendingApproval`, `AssistantActionRequest`, `AssistantThreadStateResponse`, `AssistantRunAckResponse` |
| 161–533 | `api/assistant/service.py` | `AssistantService`, `_serialize_pending_approval`, `_action_request_lifecycle_status`, `_serialize_action_request` |
| 535–572 | `api/assistant/dependencies.py` | `_build_assistant_service`, `_build_authorization_service`, `get_assistant_service` |
| 578–930 | `api/assistant/run_lifecycle.py` | `_coerce_run_id`, `_extract_waiting_action_request_id`, `_canonical_active_run_id`, `_should_resume_existing_run`, `_coordinator_task*`, `_snapshot_is_terminal`, `_terminal_live_event`, `_persist_terminal_run_state`, `_execute_detached_run_job`, `_run_detached_assistant_turn` |
| 92, 931–1231 | `api/routes/assistant.py` (~395 lines) | `router`, route handlers, `_RUN_COORDINATOR`, `get_assistant_run_coordinator`, `_require_active_user` |

---

### `api/routes/admin.py` (866 lines) → `api/admin/` package

| Old location | New file | What's in it |
|---|---|---|
| 48–134 | `api/admin/schemas.py` | All Pydantic models + `_to_user_response` |
| 136–228 | `api/admin/guards.py` | `_require_admin`, `_record_admin_outcome`, `ADMIN_OUTCOMES_TOTAL` |
| 232–492, 755–866 | `api/admin/user_routes.py` | `list_users`, `update_user_active`, `delete_user`, `list_tools`, `set_user_tools`, `migrate_direct_grants`, `set_user_roles` |
| 495–751 | `api/admin/role_routes.py` | `list_roles`, `create_role`, `delete_role`, `get_role_tools`, `set_role_tools` |
| (composition) | `api/routes/admin.py` (8 lines) | `router` includes `user_router` + `role_router` |

---

### `api/routes/threads.py` (761 lines) → `api/threads/` package

| Old location | New file | What's in it |
|---|---|---|
| 32–89 | `api/threads/schemas.py` | Pydantic models + `_to_thread_response` |
| 125–245 | `api/threads/repository.py` | `SQLThreadRepository` |
| 247–314 | `api/threads/service.py` | `ThreadService` |
| 92–123 | `api/threads/title_generation.py` | `_extract_text_chunks`, `_message_text_chunks` |
| (routes) | `api/routes/threads.py` (~423 lines) | `router`, route handlers, `get_thread_service`, `_require_active_user` |

---

### `core/auth/authorization.py` (753 lines) → 4 modules + re-export hub

| Old location | New file | What's in it |
|---|---|---|
| 25–70 | `core/auth/authorization_errors.py` | 10 exception classes |
| 74–148 | `core/auth/authorization_types.py` | `AuthorizationUser`, `DirectGrantsMigrationSummary`, `AuthorizationRepositoryProtocol` |
| 150–366 | `core/auth/authorization_repository.py` | `SQLAuthorizationRepository` |
| 368–753 | `core/auth/authorization_service.py` | `AuthorizationService`, `get_authorization_service` |
| (hub) | `core/auth/authorization.py` (~20 lines) | Re-exports all used symbols |

---

### Shared extractions (new files)

| New file | What's in it | Extracted from |
|---|---|---|
| `core/workflows/approval.py` | `approval_detail_rows`, `approval_paragraph_block`, `approval_bullet_list_block`, `approval_key_value_block`, `approval_key_value_block_from_details`, `approval_reason_detail`, `approval_presentation`, `approval_presentation_from_reply_data` | Duplicated in `whm.py` + `proxmox.py` |
| `core/workflows/preflight_validation.py` | `validate_matching_preflight`, `resolve_requested_server_id` | `runner.py` (was `_require_matching_preflight`, `_resolve_requested_server_id`) |
| `api/route_telemetry.py` | `status_family`, `safe_trace`, `safe_metric`, `record_route_outcome` | Duplicated in `admin.py` + `threads.py` |

---

## Troubleshooting

### Import errors

If you see `ImportError: cannot import name 'X' from 'noa_api.Y'`:

1. **Check this map** — find where `X` moved to
2. **Most re-exports are preserved** — `core/agent/runner.py` and `core/auth/authorization.py` re-export everything
3. **Exception:** `assistant_action_operations.py` was deleted with NO re-exports — imports must use the new paths

### Monkeypatch failures in tests

If a test patches `noa_api.api.assistant.assistant_action_operations.some_function` and it fails:

- `approve_action_request`, `deny_action_request` → patch `noa_api.api.assistant.action_requests.X`
- `execute_approved_tool_run`, `_execute_tool`, `_validate_approved_tool_preflight` → patch `noa_api.api.assistant.approved_execution.X`
- `get_tool_definition`, `fetch_postflight_result`, `_resolve_requested_server_id` → patch on `noa_api.api.assistant.approved_execution.X` (where they're imported)

### Workflow template not found

If a workflow family isn't registered:
- Check `core/workflows/whm/__init__.py` — `WORKFLOW_TEMPLATES` dict
- Check `core/workflows/proxmox/__init__.py` — `WORKFLOW_TEMPLATES` dict
- Check `core/workflows/registry.py` — imports both dicts and registers them

### Tool not found in registry

If `get_tool_definition("tool_name")` returns `None`:
- Check `core/tools/definitions/whm.py` or `proxmox.py` or `common.py`
- Check `core/tools/definitions/__init__.py` — `ALL_TOOLS` merges all three
- Check `core/tools/registry.py` — builds index from `ALL_TOOLS`

---

## Commit Reference

| Commit | What changed |
|---|---|
| `6104e58` | Extract shared approval helpers → `core/workflows/approval.py` |
| `5b3d59f` | Extract shared telemetry → `api/route_telemetry.py` |
| `8064696` | Extract preflight validation → `core/workflows/preflight_validation.py` |
| `449e927` | Split `whm.py` → `core/workflows/whm/` (10 files) |
| `5f47e0b` | Split `proxmox.py` → `core/workflows/proxmox/` (7 files) |
| `6b03532` | Split `runner.py` → `core/agent/` (6 new files) |
| `8e1f22d` | Split `firewall_tools.py` → `whm/tools/firewall_tools/` (4 files) |
| `9608d33` | Split `registry.py` → `core/tools/` (8 new files) |
| `3abff61` | Extract from `routes/assistant.py` → `api/assistant/` (4 new files) |
| `3a51a40` | Split `routes/admin.py` → `api/admin/` (5 files) |
| `d56f78b` | Split `routes/threads.py` → `api/threads/` (4 files) |
| `1d66cf7` | Split `authorization.py` → 4 modules + hub |
| `5039ffb` | Auto-format 4 files |
| `36bae8d` | Split `assistant_action_operations.py` → 3 modules (deleted original) |
| `c2ac2b8` | Remove dead re-exports, update imports to direct paths |

---

## File Size Before/After

| File | Before | After | Notes |
|---|---|---|---|
| `core/workflows/whm.py` | 3,150 | — | Split into 10 files (largest: `firewall.py` 750) |
| `core/workflows/proxmox.py` | 2,640 | — | Split into 7 files (largest: `pool_membership_move.py` 818) |
| `core/agent/runner.py` | 2,157 | 960 | 6 modules extracted |
| `core/tools/registry.py` | 1,683 | ~20 | Thin hub; 8 modules extracted |
| `api/routes/assistant.py` | 1,231 | 395 | 4 modules extracted to `api/assistant/` |
| `whm/tools/firewall_tools.py` | 1,072 | 870 | 3 backend modules extracted |
| `api/assistant/assistant_action_operations.py` | 1,036 | — | Deleted; 3 replacement modules |
| `api/routes/admin.py` | 866 | 8 | 5 modules in `api/admin/` |
| `api/routes/threads.py` | 761 | 423 | 4 modules in `api/threads/` |
| `core/auth/authorization.py` | 753 | ~20 | Re-export hub; 4 modules extracted |
| **Files > 1,000 lines** | **7** | **0** | |
| **Total source files** | **102** | **178** | |
| **Tests passing** | **667** | **667** | |
