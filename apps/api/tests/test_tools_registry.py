from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast

from noa_api.core.agent.tool_schemas import _to_openai_tool_schema
from noa_api.core.tools import catalog
from noa_api.core.tools.catalog import get_tool_catalog
from noa_api.core.tools import registry as tool_registry_module
from noa_api.core.tools.demo_tools import get_current_date, get_current_time
from noa_api.core.tools.registry import get_tool_definition, get_tool_registry


async def test_tool_registry_contains_core_tools_with_expected_risk() -> None:
    registry = get_tool_registry()
    names = tuple(tool.name for tool in registry)
    risks = {tool.name: tool.risk for tool in registry}

    assert names == get_tool_catalog()
    assert risks["get_current_time"] == "READ"
    assert risks["get_current_date"] == "READ"
    assert risks["update_workflow_todo"] == "READ"
    assert get_tool_definition("get_current_time") is not None
    assert get_tool_definition("get_current_date") is not None
    assert get_tool_definition("set_demo_flag") is None
    assert get_tool_definition("unknown_tool") is None


async def test_tool_registry_exposes_machine_readable_parameter_schemas() -> None:
    by_name = {tool.name: tool for tool in get_tool_registry()}

    assert by_name["get_current_time"].parameters_schema["properties"] == {}
    assert by_name["get_current_date"].parameters_schema["properties"] == {}
    assert by_name["update_workflow_todo"].risk == "READ"
    assert by_name["update_workflow_todo"].result_schema is not None

    change_email_schema = by_name["whm_change_contact_email"].parameters_schema
    assert change_email_schema["properties"]["new_email"]["format"] == "email"

    primary_domain_schema = by_name["whm_change_primary_domain"].parameters_schema
    assert "pattern" in primary_domain_schema["properties"]["new_domain"]

    search_schema = by_name["whm_search_accounts"].parameters_schema
    assert search_schema["properties"]["limit"]["default"] == 20
    assert search_schema["properties"]["limit"]["minimum"] == 1

    proxmox_disable_schema = by_name["proxmox_disable_vm_nic"].parameters_schema
    assert proxmox_disable_schema["required"] == [
        "server_ref",
        "node",
        "vmid",
        "net",
        "digest",
        "reason",
    ]
    assert proxmox_disable_schema["properties"]["net"]["pattern"] == r"^net\d+$"

    proxmox_enable_schema = by_name["proxmox_enable_vm_nic"].parameters_schema
    assert proxmox_enable_schema["required"] == [
        "server_ref",
        "node",
        "vmid",
        "net",
        "digest",
        "reason",
    ]

    assert by_name["whm_suspend_account"].result_schema is not None
    assert by_name["whm_firewall_unblock"].result_schema is not None
    assert by_name["proxmox_preflight_vm_nic_toggle"].result_schema is not None
    assert by_name["proxmox_disable_vm_nic"].result_schema is not None

    proxmox_user_schema = by_name["proxmox_get_user_by_email"].parameters_schema
    assert proxmox_user_schema["properties"]["email"]["format"] == "email"

    proxmox_move_schema = by_name["proxmox_move_vms_between_pools"].parameters_schema
    assert proxmox_move_schema["required"] == [
        "server_ref",
        "source_pool",
        "destination_pool",
        "vmids",
        "email",
        "reason",
    ]
    assert proxmox_move_schema["properties"]["vmids"]["minItems"] == 1
    assert proxmox_move_schema["properties"]["vmids"]["uniqueItems"] is True

    proxmox_cloudinit_schema = by_name[
        "proxmox_reset_vm_cloudinit_password"
    ].parameters_schema
    assert proxmox_cloudinit_schema["properties"]["new_password"]["minLength"] == 1

    assert by_name["proxmox_get_vm_status_current"].result_schema is not None
    assert by_name["proxmox_get_vm_config"].result_schema is not None
    assert by_name["proxmox_get_vm_pending"].result_schema is not None
    assert by_name["proxmox_get_user_by_email"].result_schema is not None
    assert (
        by_name["proxmox_preflight_vm_cloudinit_password_reset"].result_schema
        is not None
    )
    assert by_name["proxmox_reset_vm_cloudinit_password"].result_schema is not None
    assert by_name["proxmox_preflight_move_vms_between_pools"].result_schema is not None
    assert by_name["proxmox_move_vms_between_pools"].result_schema is not None

    assert "VM configuration" in by_name["proxmox_get_vm_config"].description
    assert "VM config or the digest" in by_name["proxmox_get_vm_config"].prompt_hints[0]
    assert "VM configuration changes" in by_name["proxmox_get_vm_pending"].description
    assert "queued VM changes" in by_name["proxmox_get_vm_pending"].prompt_hints[0]
    assert (
        "VM configuration and cloud-init state"
        in by_name["proxmox_preflight_vm_cloudinit_password_reset"].description
    )
    assert (
        "VM configuration plus cloud-init state"
        in by_name["proxmox_preflight_vm_cloudinit_password_reset"].prompt_hints[0]
    )


async def test_openai_tool_schema_includes_risk_notes_and_guidance() -> None:
    suspend_tool = get_tool_definition("whm_suspend_account")
    todo_tool = get_tool_definition("update_workflow_todo")

    assert suspend_tool is not None
    assert todo_tool is not None

    suspend_schema = cast(dict[str, Any], _to_openai_tool_schema(suspend_tool))
    todo_schema = cast(dict[str, Any], _to_openai_tool_schema(todo_tool))

    suspend_description = suspend_schema["function"]["description"]
    assert (
        "Risk: CHANGE. Requires persisted approval before execution."
        in suspend_description
    )
    assert "Run `whm_preflight_account` first" in suspend_description
    assert "status` `no-op`" in suspend_description

    todo_description = todo_schema["function"]["description"]
    assert (
        "Risk: READ. Evidence-gathering only; it does not change system state."
        in todo_description
    )
    assert "backend-managed operational workflows" in todo_description
    assert "Do not use it for simple READ questions" in todo_description
    assert "Successful results return the saved `todos`" in todo_description

    todo_items_description = todo_schema["function"]["parameters"]["properties"][
        "todos"
    ]["description"]
    assert "Keep exactly one item in_progress at a time" in todo_items_description


async def test_whm_change_tools_expose_workflow_families() -> None:
    by_name = {tool.name: tool for tool in get_tool_registry()}

    assert by_name["whm_suspend_account"].workflow_family == "whm-account-lifecycle"
    assert by_name["whm_unsuspend_account"].workflow_family == "whm-account-lifecycle"
    assert (
        by_name["whm_change_contact_email"].workflow_family
        == "whm-account-contact-email"
    )
    assert (
        by_name["whm_change_primary_domain"].workflow_family
        == "whm-account-primary-domain"
    )
    assert (
        by_name["whm_firewall_unblock"].workflow_family == "whm-firewall-batch-change"
    )
    assert (
        by_name["whm_firewall_allowlist_remove"].workflow_family
        == "whm-firewall-batch-change"
    )
    assert (
        by_name["whm_firewall_allowlist_add_ttl"].workflow_family
        == "whm-firewall-batch-change"
    )
    assert (
        by_name["whm_firewall_denylist_add_ttl"].workflow_family
        == "whm-firewall-batch-change"
    )
    assert (
        by_name["proxmox_disable_vm_nic"].workflow_family
        == "proxmox-vm-nic-connectivity"
    )
    assert (
        by_name["proxmox_enable_vm_nic"].workflow_family
        == "proxmox-vm-nic-connectivity"
    )
    assert (
        by_name["proxmox_reset_vm_cloudinit_password"].workflow_family
        == "proxmox-vm-cloudinit-password-reset"
    )
    assert (
        by_name["proxmox_move_vms_between_pools"].workflow_family
        == "proxmox-pool-membership-move"
    )


async def test_proxmox_change_tools_expose_preflight_guidance() -> None:
    cloudinit_tool = get_tool_definition("proxmox_reset_vm_cloudinit_password")
    pool_move_tool = get_tool_definition("proxmox_move_vms_between_pools")

    assert cloudinit_tool is not None
    assert pool_move_tool is not None

    cloudinit_schema = cast(dict[str, Any], _to_openai_tool_schema(cloudinit_tool))
    pool_move_schema = cast(dict[str, Any], _to_openai_tool_schema(pool_move_tool))

    assert (
        "Run `proxmox_preflight_vm_cloudinit_password_reset` first"
        in cloudinit_schema["function"]["description"]
    )
    assert (
        "Run `proxmox_preflight_move_vms_between_pools` first"
        in pool_move_schema["function"]["description"]
    )


async def test_all_change_tools_require_shared_reason_parameter() -> None:
    for tool in get_tool_registry():
        if tool.risk != "CHANGE":
            continue

        properties = cast(dict[str, Any], tool.parameters_schema.get("properties"))
        required = cast(list[str], tool.parameters_schema.get("required"))
        reason_schema = properties.get("reason")
        assert reason_schema is tool_registry_module._REASON_PARAM
        assert "reason" in required


async def test_tools_catalog_is_sourced_live_from_registry(monkeypatch) -> None:
    monkeypatch.setattr(catalog, "get_tool_names", lambda: ("dynamic_tool",))

    assert catalog.get_tool_catalog() == ("dynamic_tool",)


async def test_read_demo_tools_return_time_and_date_payloads() -> None:
    current_time = await get_current_time()
    current_date = await get_current_date()

    assert "time" in current_time
    assert "date" in current_date
    datetime.fromisoformat(current_time["time"])
    date.fromisoformat(current_date["date"])
