from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ImunifyPurpose = Literal["white", "drop"]
ImunifyVerdict = Literal["whitelisted", "blacklisted", "not_found"]


@dataclass(frozen=True)
class ImunifyIPEntry:
    """Represents a single IP entry in Imunify's IP list."""

    ip: str
    purpose: ImunifyPurpose
    expiration: int | None
    comment: str | None
    manual: bool
    country_code: str | None
    country_name: str | None


@dataclass(frozen=True)
class ImunifyIPListResult:
    """Parsed result from Imunify ip-list query."""

    found: bool
    verdict: ImunifyVerdict
    entries: list[ImunifyIPEntry]
    raw_counts: dict[str, Any] | None = None


def parse_imunify_ip_list_response(
    data: dict[str, Any],
    target_ip: str,
) -> ImunifyIPListResult:
    """
    Parse Imunify ip-list local list --by-ip response.

    Expected response structure:
    {
        "items": [
            {
                "ip": "11.12.13.14",
                "purpose": "white" | "drop",
                "expiration": 1775225644 | null,
                "comment": "...",
                "manual": true,
                "country": {"code": "US", "name": "United States"}
            }
        ],
        "counts": {...}
    }
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

    # Filter entries matching the target IP
    matching_entries = [e for e in entries if e.ip == target_ip]

    # Determine verdict based on matching entries
    verdict: ImunifyVerdict = "not_found"
    if matching_entries:
        # Check for blacklist (drop) entries first - takes priority
        if any(e.purpose == "drop" for e in matching_entries):
            verdict = "blacklisted"
        elif any(e.purpose == "white" for e in matching_entries):
            verdict = "whitelisted"

    return ImunifyIPListResult(
        found=bool(matching_entries),
        verdict=verdict,
        entries=matching_entries,
        raw_counts=raw_counts,
    )


def imunify_entry_to_dict(entry: ImunifyIPEntry) -> dict[str, Any]:
    """Convert ImunifyIPEntry to a JSON-serializable dict."""
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
    """
    Format Imunify entries as human-readable match strings.

    Similar to CSF matches format for consistency.
    """
    matches: list[str] = []
    for entry in entries:
        purpose_label = "whitelist" if entry.purpose == "white" else "blacklist"
        parts = [f"Imunify {purpose_label}: {entry.ip}"]

        if entry.comment:
            parts.append(f"({entry.comment})")

        if entry.expiration:
            parts.append(f"[expires: {entry.expiration}]")

        matches.append(" ".join(parts))

    return matches
