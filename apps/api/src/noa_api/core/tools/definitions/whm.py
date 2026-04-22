"""WHM tool definitions."""

from __future__ import annotations

from noa_api.core.tools.schema_builders import (
    _integer_param,
    _object_schema,
    _string_array_param,
    _string_param,
)
from noa_api.core.tools.schemas.common import (
    BINARY_NAME_PARAM,
    CSF_TARGET_PARAM,
    CSF_TARGETS_PARAM,
    DOMAIN_PARAM,
    REASON_PARAM,
    SERVER_REF_PARAM,
    USERNAME_PARAM,
)
from noa_api.core.tools.schemas.whm import (
    ACCOUNT_CHANGE_RESULT_SCHEMA,
    ACCOUNTS_RESULT_SCHEMA,
    CHECK_BINARY_RESULT_SCHEMA,
    FIREWALL_BATCH_RESULT_SCHEMA,
    MAIL_AUTH_SUSPECTS_RESULT_SCHEMA,
    PREFLIGHT_ACCOUNT_RESULT_SCHEMA,
    PREFLIGHT_FIREWALL_RESULT_SCHEMA,
    PRIMARY_DOMAIN_PREFLIGHT_RESULT_SCHEMA,
    SEARCH_ACCOUNTS_RESULT_SCHEMA,
    SERVERS_RESULT_SCHEMA,
    VALIDATE_SERVER_RESULT_SCHEMA,
)
from noa_api.core.tools.types import ToolDefinition
from noa_api.storage.postgres.lifecycle import ToolRisk
from noa_api.whm.tools.account_change_tools import (
    whm_change_contact_email,
    whm_change_primary_domain,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.whm.tools.firewall_tools import (
    whm_firewall_allowlist_add_ttl,
    whm_firewall_allowlist_remove,
    whm_firewall_denylist_add_ttl,
    whm_firewall_unblock,
    whm_preflight_firewall_entries,
)
from noa_api.whm.tools.preflight_tools import (
    whm_preflight_account,
    whm_preflight_primary_domain_change,
)
from noa_api.whm.tools.read_tools import (
    whm_check_binary_exists,
    whm_list_accounts,
    whm_list_servers,
    whm_mail_log_failed_auth_suspects,
    whm_search_accounts,
    whm_validate_server,
)

WHM_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="whm_list_servers",
        description="List configured WHM servers using safe fields only.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(properties={}, required=[]),
        execute=whm_list_servers,
        prompt_hints=(
            "Use this when the user has not supplied a server_ref or when you need server choices for disambiguation.",
            "Successful results return `servers` and never include API tokens.",
        ),
        result_schema=SERVERS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_validate_server",
        description="Validate a WHM server reference by calling a lightweight WHM API check.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={"server_ref": SERVER_REF_PARAM},
            required=["server_ref"],
        ),
        execute=whm_validate_server,
        prompt_hints=(
            "Use this for connectivity or credential validation, not for account discovery.",
            "Success returns `ok: true` and `message: ok`; failures return `error_code`, `message`, and possibly `choices` if the server reference is ambiguous.",
        ),
        result_schema=VALIDATE_SERVER_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_check_binary_exists",
        description="Check whether a specific binary exists over SSH on one resolved WHM server.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "binary_name": BINARY_NAME_PARAM,
            },
            required=["server_ref", "binary_name"],
        ),
        execute=whm_check_binary_exists,
        prompt_hints=(
            "Use this after the WHM server has SSH credentials and a validated host key fingerprint.",
            "Successful results return `found` plus the resolved binary `path` when present.",
        ),
        result_schema=CHECK_BINARY_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_mail_log_failed_auth_suspects",
        description=(
            "Parse an LFD auth-block log line (smtpauth/imapd/pop3d) and search mail logs (typically /var/log/maillog*; also /var/log/exim_mainlog* for smtpauth) for failed authentication attempts from the parsed IP on that date, returning the top suspect mailbox usernames."
        ),
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "lfd_log_line": _string_param(
                    "Exact LFD/CSF log line (source of truth), e.g. `lfd: (pop3d) Failed POP3 login from 1.2.3.4 ... - Tue Mar 31 12:23:11 2026`. The tool hard-guards to smtpauth, imapd, and pop3d."
                ),
                "top_n": _integer_param(
                    "Maximum number of suspect usernames to return.",
                    minimum=1,
                    maximum=200,
                    default=50,
                ),
                "include_raw_output": {
                    "type": "boolean",
                    "description": "Include the copy/paste-ready aggregated output lines in `raw_output`. Defaults to false to reduce stored sensitive output.",
                    "default": False,
                },
            },
            required=["server_ref", "lfd_log_line"],
        ),
        execute=whm_mail_log_failed_auth_suspects,
        prompt_hints=(
            "Use only for follow-up on firewall blocks caused by failed SMTP AUTH (smtpauth), IMAP (imapd), or POP3 (pop3d) logins.",
            "Provide the exact LFD log line so the tool can parse month/day/IP reliably.",
            "This tool may take up to ~2 minutes on large mail logs; wait for the result.",
            "The output includes mailbox identifiers and should be treated as sensitive operational data.",
            "Return `raw_output` to the user in a copy/paste-friendly code block.",
        ),
        result_schema=MAIL_AUTH_SUSPECTS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_list_accounts",
        description="List WHM accounts for one resolved server.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={"server_ref": SERVER_REF_PARAM},
            required=["server_ref"],
        ),
        execute=whm_list_accounts,
        prompt_hints=(
            "Use this for account discovery when you need the available usernames or domains on a server.",
            "Successful results return `accounts`; resolution failures return `choices` or `error_code`.",
        ),
        result_schema=ACCOUNTS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_search_accounts",
        description="Search WHM accounts by case-insensitive username or domain substring on one server.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "query": _string_param(
                    "Case-insensitive search text matched against the account username and domain."
                ),
                "limit": _integer_param(
                    "Maximum number of matching accounts to return. Must be a positive integer.",
                    minimum=1,
                    maximum=100,
                    default=20,
                ),
            },
            required=["server_ref", "query"],
        ),
        execute=whm_search_accounts,
        prompt_hints=(
            "Use this to discover the exact WHM username before account changes or preflight calls.",
            "Successful results return matching `accounts` and echo the original `query`.",
        ),
        result_schema=SEARCH_ACCOUNTS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_preflight_account",
        description="Fetch the current WHM account state for one exact username before an account change.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "username": USERNAME_PARAM,
            },
            required=["server_ref", "username"],
        ),
        execute=whm_preflight_account,
        prompt_hints=(
            "Run this before `whm_suspend_account`, `whm_unsuspend_account`, or `whm_change_contact_email` and summarize the evidence before proposing the change.",
            "Successful results return `account` with fields such as `user`, `domain`, `contactemail`, and `suspended`.",
        ),
        result_schema=PREFLIGHT_ACCOUNT_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_preflight_primary_domain_change",
        description="Fetch the current WHM account state and verify that a requested primary domain is safe to use before changing it.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "username": USERNAME_PARAM,
                "new_domain": DOMAIN_PARAM,
            },
            required=["server_ref", "username", "new_domain"],
        ),
        execute=whm_preflight_primary_domain_change,
        prompt_hints=(
            "Run this before `whm_change_primary_domain` and summarize the current primary domain plus any existing addon-domain conflicts.",
            "Successful results return `account`, `requested_domain`, `requested_domain_location`, `domain_owner`, and `domain_inventory`.",
        ),
        result_schema=PRIMARY_DOMAIN_PREFLIGHT_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_suspend_account",
        description="Suspend one WHM account after the exact account has been preflighted.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "username": USERNAME_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "username", "reason"],
        ),
        execute=whm_suspend_account,
        prompt_hints=(
            "Run `whm_preflight_account` first and summarize the current account state before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if already suspended, or `status` `changed` only after postflight confirms the suspension.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=ACCOUNT_CHANGE_RESULT_SCHEMA,
        workflow_family="whm-account-lifecycle",
    ),
    ToolDefinition(
        name="whm_unsuspend_account",
        description="Unsuspend one WHM account after the exact account has been preflighted.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "username": USERNAME_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "username", "reason"],
        ),
        execute=whm_unsuspend_account,
        prompt_hints=(
            "Run `whm_preflight_account` first and summarize the current account state before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if the account is already active, or `status` `changed` only after postflight confirms the unsuspend.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=ACCOUNT_CHANGE_RESULT_SCHEMA,
        workflow_family="whm-account-lifecycle",
    ),
    ToolDefinition(
        name="whm_change_contact_email",
        description="Change the WHM contact email for one exact account after preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "username": USERNAME_PARAM,
                "new_email": _string_param(
                    "New contact email address to set for the WHM account.",
                    format_name="email",
                ),
                "reason": REASON_PARAM,
            },
            required=["server_ref", "username", "new_email", "reason"],
        ),
        execute=whm_change_contact_email,
        prompt_hints=(
            "Run `whm_preflight_account` first and summarize the existing contact email before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if the email already matches, or `status` `changed` only after postflight verifies the new email.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=ACCOUNT_CHANGE_RESULT_SCHEMA,
        workflow_family="whm-account-contact-email",
    ),
    ToolDefinition(
        name="whm_change_primary_domain",
        description="Change the WHM primary domain for one exact account after a domain-specific preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "username": USERNAME_PARAM,
                "new_domain": DOMAIN_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "username", "new_domain", "reason"],
        ),
        execute=whm_change_primary_domain,
        prompt_hints=(
            "Run `whm_preflight_primary_domain_change` first and summarize the current primary domain plus any addon-domain conflicts before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if the primary domain already matches, or `status` `changed` only after postflight verifies the new primary domain and DNS zone.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=ACCOUNT_CHANGE_RESULT_SCHEMA,
        workflow_family="whm-account-primary-domain",
    ),
    # -------------------------------------------------------------------------
    # Unified Firewall Tools (CSF + Imunify)
    # -------------------------------------------------------------------------
    ToolDefinition(
        name="whm_preflight_firewall_entries",
        description="Inspect the current firewall state (CSF and/or Imunify) for one target before a firewall change. This is the preferred preflight tool for IP operations.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "target": CSF_TARGET_PARAM,
            },
            required=["server_ref", "target"],
        ),
        execute=whm_preflight_firewall_entries,
        prompt_hints=(
            "Use this as the primary tool for checking IP status before firewall changes.",
            "Automatically detects which firewall tools (CSF, Imunify, or both) are available.",
            "Returns `combined_verdict` plus individual `csf` and `imunify` results when available.",
            "After a successful run, summarize the combined verdict and available tools before proposing changes.",
        ),
        result_schema=PREFLIGHT_FIREWALL_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_firewall_unblock",
        description="Remove firewall block entries from CSF and/or Imunify (based on availability) for one or more targets.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "targets": CSF_TARGETS_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "targets", "reason"],
        ),
        execute=whm_firewall_unblock,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
            "If CSF indicates the block reason is an LFD auth failure (smtpauth/imapd/pop3d), the tool will also return `failed_auth_suspects` for that target.",
        ),
        result_schema=FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
    ToolDefinition(
        name="whm_firewall_allowlist_add_ttl",
        description="Temporarily add one or more IPv4 targets to the firewall allowlist (CSF and/or Imunify based on availability).",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "targets": _string_array_param(
                    "One or more IPv4 addresses to allowlist temporarily. This TTL tool does not accept CIDRs, hostnames, or IPv6 targets.",
                    item_description="Exact IPv4 address target",
                    item_format_name="ipv4",
                    unique_items=True,
                ),
                "duration_minutes": _integer_param(
                    "Duration already converted to minutes before calling the tool.",
                    minimum=1,
                    maximum=525600,
                ),
                "reason": REASON_PARAM,
            },
            required=["server_ref", "targets", "duration_minutes", "reason"],
        ),
        execute=whm_firewall_allowlist_add_ttl,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
        ),
        result_schema=FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
    ToolDefinition(
        name="whm_firewall_allowlist_remove",
        description="Remove one or more targets from the firewall allowlist (CSF and/or Imunify based on availability).",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "targets": CSF_TARGETS_PARAM,
                "reason": REASON_PARAM,
            },
            required=["server_ref", "targets", "reason"],
        ),
        execute=whm_firewall_allowlist_remove,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
        ),
        result_schema=FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
    ToolDefinition(
        name="whm_firewall_denylist_add_ttl",
        description="Temporarily add one or more IPv4 targets to the firewall denylist/blacklist (CSF and/or Imunify based on availability).",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": SERVER_REF_PARAM,
                "targets": _string_array_param(
                    "One or more IPv4 addresses to deny/block temporarily. This TTL tool does not accept CIDRs, hostnames, or IPv6 targets.",
                    item_description="Exact IPv4 address target",
                    item_format_name="ipv4",
                    unique_items=True,
                ),
                "duration_minutes": _integer_param(
                    "Duration already converted to minutes before calling the tool.",
                    minimum=1,
                    maximum=525600,
                ),
                "reason": REASON_PARAM,
            },
            required=["server_ref", "targets", "duration_minutes", "reason"],
        ),
        execute=whm_firewall_denylist_add_ttl,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
        ),
        result_schema=FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
)
