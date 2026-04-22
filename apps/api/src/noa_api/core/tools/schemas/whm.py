"""WHM-specific result schema constants."""

from __future__ import annotations

from noa_api.core.tools.schemas.common import (
    ACCOUNT_RESULT_SCHEMA,
    RESULT_SUCCESS_OK_SCHEMA,
    SERVER_SAFE_RESULT_SCHEMA,
)
from noa_api.core.tools.schema_builders import (
    _result_any_of,
    _result_array_schema,
    _result_boolean_schema,
    _result_nullable_schema,
    _result_object_schema,
    _result_string_schema,
)

# ---------------------------------------------------------------------------
# WHM server choice / error
# ---------------------------------------------------------------------------

WHM_SERVER_CHOICE_SCHEMA = _result_object_schema(
    properties={
        "id": _result_string_schema(),
        "name": _result_string_schema(),
        "base_url": _result_string_schema(),
    },
    required=["id", "name", "base_url"],
)

WHM_RESULT_ERROR_SCHEMA = _result_object_schema(
    properties={
        "ok": _result_boolean_schema(value=False),
        "error_code": _result_string_schema(),
        "message": _result_string_schema(),
        "choices": _result_array_schema(items=WHM_SERVER_CHOICE_SCHEMA),
    },
    required=["ok", "error_code", "message"],
)

# ---------------------------------------------------------------------------
# WHM result schemas
# ---------------------------------------------------------------------------

SERVERS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "servers": _result_array_schema(items=SERVER_SAFE_RESULT_SCHEMA),
        },
        required=["ok", "servers"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

ACCOUNTS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "accounts": _result_array_schema(items=ACCOUNT_RESULT_SCHEMA),
        },
        required=["ok", "accounts"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

SEARCH_ACCOUNTS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "accounts": _result_array_schema(items=ACCOUNT_RESULT_SCHEMA),
            "query": _result_string_schema(),
        },
        required=["ok", "accounts", "query"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

VALIDATE_SERVER_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
        },
        required=["ok", "message"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

CHECK_BINARY_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "binary_name": _result_string_schema(),
            "found": _result_boolean_schema(),
            "path": _result_nullable_schema(_result_string_schema()),
        },
        required=["ok", "binary_name", "found", "path"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

MAIL_AUTH_SUSPECT_ITEM_SCHEMA = _result_object_schema(
    properties={
        "email": _result_string_schema(),
        "count": {"type": "integer"},
    },
    required=["email", "count"],
)

MAIL_AUTH_SUSPECTS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "service": _result_string_schema(enum=["smtpauth", "imapd", "pop3d"]),
            "month": _result_string_schema(
                enum=[
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                ]
            ),
            "day": {"type": "integer"},
            "ip": _result_string_schema(),
            "top_n": {"type": "integer"},
            "suspects": _result_array_schema(items=MAIL_AUTH_SUSPECT_ITEM_SCHEMA),
            "raw_output": _result_string_schema(),
            "stderr": _result_string_schema(),
            "duration_ms": {"type": "integer"},
        },
        required=[
            "ok",
            "service",
            "month",
            "day",
            "ip",
            "top_n",
            "suspects",
            "raw_output",
            "stderr",
            "duration_ms",
        ],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

PREFLIGHT_ACCOUNT_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "account": ACCOUNT_RESULT_SCHEMA,
        },
        required=["ok", "server_id", "account"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

DOMAIN_INVENTORY_RESULT_SCHEMA = _result_object_schema(
    properties={
        "main_domain": _result_nullable_schema(_result_string_schema()),
        "addon_domains": _result_array_schema(items=_result_string_schema()),
        "parked_domains": _result_array_schema(items=_result_string_schema()),
        "sub_domains": _result_array_schema(items=_result_string_schema()),
    },
    required=["main_domain", "addon_domains", "parked_domains", "sub_domains"],
)

PRIMARY_DOMAIN_PREFLIGHT_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "account": ACCOUNT_RESULT_SCHEMA,
            "requested_domain": _result_string_schema(),
            "domain_owner": _result_nullable_schema(_result_string_schema()),
            "requested_domain_location": _result_string_schema(
                enum=["absent", "primary"]
            ),
            "safe_to_change": _result_boolean_schema(value=True),
            "domain_inventory": DOMAIN_INVENTORY_RESULT_SCHEMA,
        },
        required=[
            "ok",
            "server_id",
            "account",
            "requested_domain",
            "domain_owner",
            "requested_domain_location",
            "safe_to_change",
            "domain_inventory",
        ],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

ACCOUNT_CHANGE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "status": _result_string_schema(enum=["changed", "no-op"]),
            "message": _result_string_schema(),
        },
        required=["ok", "status", "message"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

# ---------------------------------------------------------------------------
# Firewall (unified CSF + Imunify) result schemas
# ---------------------------------------------------------------------------

AVAILABLE_TOOLS_SCHEMA = _result_object_schema(
    properties={
        "csf": _result_boolean_schema(),
        "imunify": _result_boolean_schema(),
    },
    required=["csf", "imunify"],
)

PREFLIGHT_FIREWALL_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "target": _result_string_schema(),
            "available_tools": AVAILABLE_TOOLS_SCHEMA,
            "combined_verdict": _result_string_schema(),
            "matches": _result_array_schema(items=_result_string_schema()),
            "csf": {"type": "object"},
            "imunify": {"type": "object"},
        },
        required=[
            "ok",
            "server_id",
            "target",
            "available_tools",
            "combined_verdict",
            "matches",
        ],
        additional_properties=True,
    ),
    WHM_RESULT_ERROR_SCHEMA,
)

FIREWALL_RESULT_ITEM_SCHEMA = _result_object_schema(
    properties={
        "target": _result_string_schema(),
        "ok": _result_boolean_schema(),
        "status": _result_string_schema(enum=["changed", "no-op", "error"]),
        "available_tools": AVAILABLE_TOOLS_SCHEMA,
        "csf": _result_nullable_schema({"type": "object"}),
        "imunify": _result_nullable_schema({"type": "object"}),
    },
    required=["target", "ok", "status", "available_tools"],
    additional_properties=True,
)

FIREWALL_BATCH_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            "ok": _result_boolean_schema(),
            "available_tools": AVAILABLE_TOOLS_SCHEMA,
            "results": _result_array_schema(items=FIREWALL_RESULT_ITEM_SCHEMA),
        },
        required=["ok", "available_tools", "results"],
    ),
    WHM_RESULT_ERROR_SCHEMA,
)
