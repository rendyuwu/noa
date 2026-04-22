from __future__ import annotations

import sys
from typing import Any

from noa_api.whm.integrations.csf import parse_csf_grep_output
from noa_api.whm.integrations.csf_cli import CSFCLIError


def _pkg():
    """Return the parent package module for monkeypatch-compatible lookups."""
    return sys.modules["noa_api.whm.tools.firewall_tools"]


async def _csf_preflight(server: Any, *, target: str) -> dict[str, object]:
    """Check target status in CSF."""
    pkg = _pkg()
    try:
        grep_result = await pkg.run_csf_command(server, args=["-g", target])
        output = pkg.require_csf_success(grep_result, default_message="CSF grep failed")
        if not output.strip():
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "CSF grep returned an invalid response",
            }
        parsed = parse_csf_grep_output(output, target=target)
        return {
            "ok": True,
            "verdict": parsed.verdict,
            "matches": parsed.matches,
            "raw_output": output,
        }
    except CSFCLIError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }


async def _csf_unblock(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from CSF block lists."""
    pkg = _pkg()
    try:
        # Remove from temporary blocks
        await pkg.run_csf_command(server, args=["-tr", target])
        # Remove from permanent deny list
        await pkg.run_csf_command(server, args=["-dr", target])
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _csf_allowlist_add_ttl(
    server: Any, *, target: str, duration_seconds: int, reason: str
) -> dict[str, object]:
    """Add target to CSF temporary allowlist."""
    pkg = _pkg()
    try:
        await pkg.run_csf_command(
            server, args=["-ta", target, str(duration_seconds), reason]
        )
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _csf_allowlist_remove(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from CSF allowlist."""
    pkg = _pkg()
    try:
        # Remove from temporary allows
        await pkg.run_csf_command(server, args=["-tra", target])
        # Remove from permanent allow list
        await pkg.run_csf_command(server, args=["-ar", target])
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _csf_denylist_add_ttl(
    server: Any, *, target: str, duration_seconds: int, reason: str
) -> dict[str, object]:
    """Add target to CSF temporary denylist."""
    pkg = _pkg()
    try:
        await pkg.run_csf_command(
            server, args=["-td", target, str(duration_seconds), reason]
        )
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}
