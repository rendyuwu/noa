from __future__ import annotations

from dataclasses import dataclass
import html
import ipaddress
import re
from typing import Literal


CSFTargetKind = Literal[
    "ip",
    "cidr",
    "ipv6",
    "ipv6_cidr",
    "hostname",
    "unknown",
]


@dataclass(frozen=True)
class CSFTarget:
    raw: str
    kind: CSFTargetKind
    ip: str | None = None
    cidr: str | None = None
    hostname: str | None = None


CSFGrepVerdict = Literal["blocked", "allowlisted", "not_found", "unknown"]


@dataclass(frozen=True)
class CSFGrepParsed:
    verdict: CSFGrepVerdict
    matches: list[str]


def parse_csf_target(raw: str) -> CSFTarget:
    value = raw.strip()
    if not value:
        raise ValueError("CSF target is required")

    try:
        ip = ipaddress.ip_address(value)
        if isinstance(ip, ipaddress.IPv4Address):
            return CSFTarget(raw=value, kind="ip", ip=str(ip))
        return CSFTarget(raw=value, kind="ipv6", ip=str(ip))
    except ValueError:
        pass

    if "/" in value:
        try:
            network = ipaddress.ip_network(value, strict=False)
            if isinstance(network, ipaddress.IPv4Network):
                return CSFTarget(raw=value, kind="cidr", cidr=str(network))
            return CSFTarget(raw=value, kind="ipv6_cidr", cidr=str(network))
        except ValueError:
            pass

    if re.search(r"[a-zA-Z]", value):
        return CSFTarget(raw=value, kind="hostname", hostname=value)

    return CSFTarget(raw=value, kind="unknown")


_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text_lines(html_value: str) -> list[str]:
    normalized = re.sub(r"<\s*br\s*/?>", "\n", html_value, flags=re.IGNORECASE)
    normalized = re.sub(
        r"<\s*/?\s*(pre|p|div|tr|td|table|body|html)\b[^>]*>",
        "\n",
        normalized,
        flags=re.IGNORECASE,
    )
    text = _TAG_RE.sub("", normalized)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return lines


def parse_csf_grep_html(
    html_value: str, *, target: str, max_matches: int = 20
) -> CSFGrepParsed:
    target_value = target.strip()
    if not target_value:
        raise ValueError("CSF grep target is required")

    lines = _html_to_text_lines(html_value)
    matches = [line for line in lines if target_value in line]
    bounded = matches[: max_matches if max_matches > 0 else 0]

    verdict: CSFGrepVerdict = "unknown"
    lower_matches = "\n".join(bounded).lower()
    if not matches:
        verdict = "not_found"
    elif (
        "csf.deny" in lower_matches
        or "tempban" in lower_matches
        or "deny" in lower_matches
    ):
        verdict = "blocked"
    elif "csf.allow" in lower_matches or "allow" in lower_matches:
        verdict = "allowlisted"

    return CSFGrepParsed(verdict=verdict, matches=bounded)
