from __future__ import annotations

import sys
from typing import Any

from noa_api.whm.integrations.imunify import (
    format_imunify_matches,
    imunify_entry_to_dict,
    parse_imunify_ip_list_response,
)
from noa_api.whm.integrations.imunify_cli import ImunifyCLIError


def _pkg():
    """Return the parent package module for monkeypatch-compatible lookups."""
    return sys.modules["noa_api.whm.tools.firewall_tools"]


async def _imunify_preflight(server: Any, *, target: str) -> dict[str, object]:
    """Check target status in Imunify."""
    pkg = _pkg()
    try:
        result = await pkg.run_imunify_command(
            server, args=["ip-list", "local", "list", "--by-ip", target, "--json"]
        )
        raw_output = pkg.imunify_command_output_text(result)
        data = pkg.parse_imunify_json_output(result)
        parsed = parse_imunify_ip_list_response(data, target)
        return {
            "ok": True,
            "verdict": parsed.verdict,
            "entries": [imunify_entry_to_dict(e) for e in parsed.entries],
            "matches": format_imunify_matches(parsed.entries),
            "raw_data": data,
            "raw_output": raw_output,
        }
    except ImunifyCLIError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }


async def _imunify_blacklist_remove(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from Imunify blacklist."""
    pkg = _pkg()
    try:
        result = await pkg.run_imunify_command(
            server,
            args=["ip-list", "local", "delete", "--purpose", "drop", target, "--json"],
        )
        # Parse to verify success
        pkg.parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _imunify_whitelist_add_ttl(
    server: Any, *, target: str, expiration_epoch: int, reason: str
) -> dict[str, object]:
    """Add target to Imunify whitelist with expiration."""
    pkg = _pkg()
    try:
        result = await pkg.run_imunify_command(
            server,
            args=[
                "ip-list",
                "local",
                "add",
                "--purpose",
                "white",
                target,
                "--comment",
                reason,
                "--expiration",
                str(expiration_epoch),
                "--json",
            ],
        )
        pkg.parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _imunify_whitelist_remove(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from Imunify whitelist."""
    pkg = _pkg()
    try:
        result = await pkg.run_imunify_command(
            server,
            args=["ip-list", "local", "delete", "--purpose", "white", target, "--json"],
        )
        pkg.parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _imunify_blacklist_add_ttl(
    server: Any, *, target: str, expiration_epoch: int, reason: str
) -> dict[str, object]:
    """Add target to Imunify blacklist with expiration."""
    pkg = _pkg()
    try:
        result = await pkg.run_imunify_command(
            server,
            args=[
                "ip-list",
                "local",
                "add",
                "--purpose",
                "drop",
                target,
                "--comment",
                reason,
                "--expiration",
                str(expiration_epoch),
                "--json",
            ],
        )
        pkg.parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}
