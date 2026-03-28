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

    if _is_valid_hostname(value):
        return CSFTarget(raw=value, kind="hostname", hostname=value)

    return CSFTarget(raw=value, kind="unknown")


_TAG_RE = re.compile(r"<[^>]+>")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HOSTNAME_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _is_valid_hostname(value: str) -> bool:
    if len(value) > 253 or "." not in value or value.endswith("."):
        return False
    labels = value.split(".")
    if not any(any(char.isalpha() for char in label) for label in labels):
        return False
    return all(_HOSTNAME_LABEL_RE.fullmatch(label) for label in labels)


def _text_to_lines(text_value: str) -> list[str]:
    text = html.unescape(_ANSI_ESCAPE_RE.sub("", text_value))
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return lines


def _html_to_text_lines(html_value: str) -> list[str]:
    normalized = re.sub(r"<\s*br\s*/?>", "\n", html_value, flags=re.IGNORECASE)
    normalized = re.sub(
        r"<\s*/?\s*(pre|p|div|tr|td|table|body|html)\b[^>]*>",
        "\n",
        normalized,
        flags=re.IGNORECASE,
    )
    text = _TAG_RE.sub("", normalized)
    return _text_to_lines(text)


def _is_block_match(line: str) -> bool:
    lower_line = line.lower()
    return any(
        marker in lower_line
        for marker in (
            "csf.deny",
            "/etc/csf/csf.deny",
            "temporary blocks:",
            "denyin",
            "denyout",
        )
    )


def _is_allow_match(line: str) -> bool:
    lower_line = line.lower()
    return any(
        marker in lower_line
        for marker in (
            "csf.allow",
            "/etc/csf/csf.allow",
            "temporary allows:",
            "allowin",
            "allowout",
        )
    )


def _is_not_found_match(line: str) -> bool:
    lower_line = line.lower()
    return lower_line.startswith("no matches found for ") or lower_line.startswith(
        "no matches for "
    )


def _parse_csf_grep_lines(
    lines: list[str], *, target: str, max_matches: int = 20
) -> CSFGrepParsed:
    target_value = target.strip()
    if not target_value:
        raise ValueError("CSF grep target is required")

    matches = [line for line in lines if target_value in line]
    bounded = matches[: max_matches if max_matches > 0 else 0]

    if any(_is_block_match(line) for line in matches):
        verdict: CSFGrepVerdict = "blocked"
    elif any(_is_allow_match(line) for line in matches):
        verdict = "allowlisted"
    elif matches and all(_is_not_found_match(line) for line in matches):
        verdict = "not_found"
    elif not matches and any(_is_not_found_match(line) for line in lines):
        verdict = "not_found"
    else:
        verdict = "unknown"

    return CSFGrepParsed(verdict=verdict, matches=bounded)


def parse_csf_grep_output(
    output: str, *, target: str, max_matches: int = 20
) -> CSFGrepParsed:
    return _parse_csf_grep_lines(
        _text_to_lines(output), target=target, max_matches=max_matches
    )


def parse_csf_grep_html(
    html_value: str, *, target: str, max_matches: int = 20
) -> CSFGrepParsed:
    return _parse_csf_grep_lines(
        _html_to_text_lines(html_value),
        target=target,
        max_matches=max_matches,
    )
