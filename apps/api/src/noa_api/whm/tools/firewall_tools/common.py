from __future__ import annotations

import re
from typing import Any


_LFD_AUTH_LINE_RE = re.compile(
    r"\blfd(?:\[\d+\])?:\s*\((smtpauth|imapd|pop3d)\)",
    re.IGNORECASE,
)


def _extract_lfd_auth_line(csf_preflight: object) -> str | None:
    if not isinstance(csf_preflight, dict):
        return None
    if csf_preflight.get("ok") is not True:
        return None

    matches = csf_preflight.get("matches")
    if isinstance(matches, list):
        for item in matches:
            if isinstance(item, str) and _LFD_AUTH_LINE_RE.search(item):
                return item

    raw_output = csf_preflight.get("raw_output")
    if isinstance(raw_output, str):
        for line in raw_output.splitlines():
            if _LFD_AUTH_LINE_RE.search(line):
                return line.strip()
    return None


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _no_firewall_tools_error() -> dict[str, object]:
    return {
        "ok": False,
        "error_code": "no_firewall_tools",
        "message": "Neither CSF nor Imunify360 is available on this server",
    }


def _compute_combined_verdict(
    csf_verdict: str | None,
    imunify_verdict: str | None,
) -> str:
    """
    Compute combined verdict from CSF and Imunify results.

    Priority: blocked > allowlisted/whitelisted > not_found
    """
    if csf_verdict == "blocked" or imunify_verdict == "blacklisted":
        return "blocked"
    if csf_verdict == "allowlisted" or imunify_verdict == "whitelisted":
        return "allowlisted"
    return "not_found"
