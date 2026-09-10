"""Imunify360 `ip-list` response parsing.

Copied from `noa-old` branch `MCP` (`whm/integrations/imunify.py`) unchanged — pure functions,
zero NOA imports, no I/O. The counterpart to `csf.py`: Imunify is the second firewall backend
(V57), and unlike CSF it answers in JSON, so this module validates a shape rather than scraping
text.

The shape `imunify360-agent ip-list local list --by-ip <ip> --json` returns:

    {"items": [{"ip": …, "purpose": "white"|"drop", "expiration": …, "comment": …,
                "manual": …, "country": {"code": …, "name": …}}],
     "counts": {…}}

Every field is re-validated rather than trusted. A malformed item is skipped, not raised on:
one bad row in a list of hundreds should not turn a preflight into an error, and the fields
that matter (`ip`, `purpose`) are exactly the ones a skip requires.

Two behaviours worth keeping in mind at the tool layer:

- **`drop` beats `white`.** Same precedence as CSF's block-beats-allow: an IP in both lists is
  reported `blacklisted`, because that is what the box is doing to it.
- **`found` is separate from `verdict`.** `found=False` means Imunify holds no entry for this
  IP — distinct from an entry that exists and is `white`.

`format_imunify_matches` renders entries as the same kind of human-readable evidence line CSF
already produces, so a dual-backend preflight result reads as one list rather than two
shapes stitched together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ImunifyPurpose = Literal["white", "drop"]
ImunifyVerdict = Literal["whitelisted", "blacklisted", "not_found"]


@dataclass(frozen=True)
class ImunifyIPEntry:
    """One validated row of Imunify's IP list."""

    ip: str
    purpose: ImunifyPurpose
    expiration: int | None
    comment: str | None
    manual: bool
    country_code: str | None
    country_name: str | None


@dataclass(frozen=True)
class ImunifyIPListResult:
    """Parsed `ip-list` query: whether the IP is listed, how, and the rows behind it.

    `allow_entry` is CSF's `allow_entry` one backend over, and it is here for the same reason
    (T26): `drop` beats `white`, so an IP on both lists reports `blacklisted` and the `white`
    row it also holds stops being visible in the verdict. An allowlist removal's postflight asks
    exactly about that row, and reading it off the verdict would call a surviving whitelist entry
    removed whenever a blacklist entry outranked it.
    """

    found: bool
    verdict: ImunifyVerdict
    entries: list[ImunifyIPEntry]
    raw_counts: dict[str, Any] | None = None
    allow_entry: bool = False


def parse_imunify_ip_list_response(data: dict[str, Any], target_ip: str) -> ImunifyIPListResult:
    """Validate and reduce an `ip-list local list --by-ip` response.

    `--by-ip` is a server-side filter, but the result is filtered again here against
    `target_ip`: the flag has been observed returning neighbours, and a verdict read off
    someone else's row is worse than no verdict.
    """
    items = data.get("items")
    if not isinstance(items, list):
        items = []

    counts = data.get("counts")
    raw_counts = counts if isinstance(counts, dict) else None

    entries: list[ImunifyIPEntry] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        ip = item.get("ip")
        if not isinstance(ip, str):
            continue

        purpose_raw = item.get("purpose")
        if purpose_raw not in ("white", "drop"):
            continue
        purpose: ImunifyPurpose = purpose_raw

        expiration = item.get("expiration")
        if expiration is not None and not isinstance(expiration, int):
            expiration = None

        comment = item.get("comment")
        if not isinstance(comment, str):
            comment = None

        manual = item.get("manual") is True

        country = item.get("country")
        country_code: str | None = None
        country_name: str | None = None
        if isinstance(country, dict):
            code = country.get("code")
            name = country.get("name")
            if isinstance(code, str):
                country_code = code
            if isinstance(name, str):
                country_name = name

        entries.append(
            ImunifyIPEntry(
                ip=ip,
                purpose=purpose,
                expiration=expiration,
                comment=comment,
                manual=manual,
                country_code=country_code,
                country_name=country_name,
            )
        )

    matching_entries = [e for e in entries if e.ip == target_ip]

    # Read off the rows, not off the verdict below, which is about to discard it when a `drop`
    # outranks it.
    allow_entry = any(e.purpose == "white" for e in matching_entries)

    verdict: ImunifyVerdict = "not_found"
    if matching_entries:
        # `drop` wins, mirroring CSF's block-beats-allow precedence.
        if any(e.purpose == "drop" for e in matching_entries):
            verdict = "blacklisted"
        elif allow_entry:
            verdict = "whitelisted"

    return ImunifyIPListResult(
        found=bool(matching_entries),
        verdict=verdict,
        entries=matching_entries,
        raw_counts=raw_counts,
        allow_entry=allow_entry,
    )


def imunify_entry_to_dict(entry: ImunifyIPEntry) -> dict[str, Any]:
    """JSON-serialisable view, for receipt payloads and audit rows."""
    return {
        "ip": entry.ip,
        "purpose": entry.purpose,
        "expiration": entry.expiration,
        "comment": entry.comment,
        "manual": entry.manual,
        "country_code": entry.country_code,
        "country_name": entry.country_name,
    }


def format_imunify_matches(entries: list[ImunifyIPEntry]) -> list[str]:
    """Render entries as evidence lines shaped like CSF's, so both backends read alike.

    **The comment goes last, and that ordering is load-bearing as of T25.** A comment is the one
    field on this line whose text NOA may itself have written — it is where a firewall entry NOA
    created carries the operator's approval reason — and the surface that answers a
    *model* has to cut that text back out again. csf gives no closing boundary for its own
    comment, so the cut runs from NOA's marker to the end of the line; putting `[expires: …]`
    ahead of the comment here is what keeps that cut from taking a second field with it. Swapping
    these two back would silently shorten the evidence a model reads.
    """
    matches: list[str] = []
    for entry in entries:
        purpose_label = "whitelist" if entry.purpose == "white" else "blacklist"
        parts = [f"Imunify {purpose_label}: {entry.ip}"]

        if entry.expiration:
            parts.append(f"[expires: {entry.expiration}]")

        if entry.comment:
            parts.append(f"({entry.comment})")

        matches.append(" ".join(parts))

    return matches


__all__ = [
    "ImunifyIPEntry",
    "ImunifyIPListResult",
    "ImunifyPurpose",
    "ImunifyVerdict",
    "format_imunify_matches",
    "imunify_entry_to_dict",
    "parse_imunify_ip_list_response",
]
