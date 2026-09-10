"""CSF target classification and `csf -g` output parsing.

Copied from `noa-old` branch `MCP` (`whm/integrations/csf.py`) unchanged — pure functions,
zero NOA imports, no I/O. Everything here exists because CSF has no machine-readable output:
`csf -g` prints iptables tables for humans, and the WHM web addon wraps the same text in HTML.
The parsers are the accumulated answer to what that text actually looks like on a live box.

**`parse_csf_target` is what V54 is enforced with.** It classifies a target as
`ip` (IPv4) / `cidr` (IPv4) / `ipv6` / `ipv6_cidr` / `hostname` / `unknown` without deciding
policy. The CHANGE tools reject anything but `ip`; the preflight READ accepts
all kinds, because "you asked about an IPv6 address and here is what CSF says" is a useful
answer even where changing it is not permitted.

**One entry point, not two.** `noa-old` also carried `parse_csf_grep_html` for WHM's
`cgi/addon_csf.cgi?action=grepip` HTTP path, plus the `WHMClient.csf_grep` /
`csf_request_action` methods that fed it. Nothing on that branch called any of the three —
`whm/tools/firewall_tools/csf_backend.py` goes over SSH — and I.ext pins WHM's firewall access
to SSH, so the HTTP addon path is not ported (T16 deviation (e)). Entity unescaping stays,
because it costs nothing and csf's own output is not guaranteed clean. ANSI escapes are
stripped because a TTY-ish csf still emits colour.

Verdict precedence is deliberate and load-bearing for the release-and-allow flow: a
block match wins over an allow match. An IP can appear in both `csf.deny` and `csf.allow` at
once, and the operationally true statement is "still blocked".

**`allow_entry` is reported beside the verdict because that precedence discards it**.
"What is this box doing to this address" and "is there still an allow entry for it" are two
questions, and the second one is the whole of an allowlist removal's postflight. Answering it
from the verdict would report a surviving `csf.allow` line as removed on any address a deny
entry also matched — a change reported as done because a *different* list masked it.

`not_found` requires positive evidence — csf's own "No matches found for …" line. An empty
parse is `unknown`, never `not_found`: "CSF said nothing we recognise" and "CSF said this IP
is clean" are different facts, and collapsing them would let a parse regression read as a
clean host.

Matches are bounded (`max_matches`, default 20). A busy server's grep can run to hundreds of
log lines, and the tool result is headed for an LLM context (V64's concern, one layer down).
**`total_matches` is reported beside them**: a cap that drops rows silently lets the
model report "there are twenty entries" at two hundred — a fabrication the tool handed it. The
count is this module's to give, because this is where the cut happens.

V85 also asks for a stable ordering before the cut, and the kept order is csf's own — not a
sort. `csf -g` renders the current tables and files deterministically, so identical calls
against unchanged state yield an identical prefix, which is the property V85 is protecting
(`listaccts`, the tool it was written at, has no such guarantee). Sorting log lines
alphabetically would also scramble the deny/allow grouping that makes the evidence readable.
"""

from __future__ import annotations

import html
import ipaddress
import re
from dataclasses import dataclass
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
    """A classified firewall target. `raw` is what the caller passed, trimmed."""

    raw: str
    kind: CSFTargetKind
    ip: str | None = None
    cidr: str | None = None
    hostname: str | None = None


CSFGrepVerdict = Literal["blocked", "allowlisted", "not_found", "unknown"]


@dataclass(frozen=True)
class CSFGrepParsed:
    """Verdict, the bounded evidence lines it was read from, and how many there were.

    `total_matches` counts the lines *before* `max_matches` cut them, so a caller can say the
    list is short rather than letting it read as complete.

    `allow_entry` is a second fact rather than a re-reading of the first, and it exists because
    the verdict deliberately loses it: block beats allow, so an address in both `csf.deny` and
    `csf.allow` is `blocked` and the allow line it was also found on stops being visible. That
    is the right answer to "what is this box doing to this address" and the wrong one to "is
    there still an allow entry here", which is what T26 asks after removing one. Reading the
    removal's outcome off the verdict would report a still-present allow entry as removed
    whenever a deny entry happened to outrank it.
    """

    verdict: CSFGrepVerdict
    matches: list[str]
    total_matches: int
    allow_entry: bool = False


def parse_csf_target(raw: str) -> CSFTarget:
    """Classify `raw` as an address, network, hostname, or nothing recognisable.

    Order matters: a bare address is tried before a network, so `1.2.3.4` never becomes a
    `/32` CIDR here — normalising it would erase the distinction the CHANGE tools check.
    """
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


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HOSTNAME_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _is_valid_hostname(value: str) -> bool:
    """RFC-1123 labels, at least one dot, at least one letter somewhere.

    The letter requirement is what stops `999.999.999.999` — not a valid address, but
    label-legal — from being classified as a hostname and reaching DNS.
    """
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


def _is_block_match(line: str) -> bool:
    """Permanent (`csf.deny`) and temporary (`Temporary Blocks:`, `DENYIN/OUT`) blocks."""
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
    """Permanent (`csf.allow`) and temporary (`Temporary Allows:`, `ALLOWIN/OUT`) allows."""
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


def _parse_csf_grep_lines(lines: list[str], *, target: str, max_matches: int = 20) -> CSFGrepParsed:
    target_value = target.strip()
    if not target_value:
        raise ValueError("CSF grep target is required")

    matches = [line for line in lines if target_value in line]
    bounded = matches[: max_matches if max_matches > 0 else 0]

    # Read off every match, not off the verdict below, which is about to discard it when a block
    # outranks it. Taken from the full list rather than from `bounded`, for `total_matches`'
    # reason: the cut shortens the evidence, it does not change what csf holds.
    allow_entry = any(_is_allow_match(line) for line in matches)

    # Block beats allow: an IP present in both is, operationally, still blocked.
    if any(_is_block_match(line) for line in matches):
        verdict: CSFGrepVerdict = "blocked"
    elif allow_entry:
        verdict = "allowlisted"
    # `not_found` needs csf to have said so. Silence is `unknown`, not clean.
    elif matches and all(_is_not_found_match(line) for line in matches):
        verdict = "not_found"
    elif not matches and any(_is_not_found_match(line) for line in lines):
        verdict = "not_found"
    else:
        verdict = "unknown"

    return CSFGrepParsed(
        verdict=verdict,
        matches=bounded,
        total_matches=len(matches),
        allow_entry=allow_entry,
    )


def parse_csf_grep_output(output: str, *, target: str, max_matches: int = 20) -> CSFGrepParsed:
    """Parse `csf -g <target>` output. The only CSF read path (see module docstring)."""
    return _parse_csf_grep_lines(_text_to_lines(output), target=target, max_matches=max_matches)


__all__ = [
    "CSFGrepParsed",
    "CSFGrepVerdict",
    "CSFTarget",
    "CSFTargetKind",
    "parse_csf_grep_output",
    "parse_csf_target",
]
