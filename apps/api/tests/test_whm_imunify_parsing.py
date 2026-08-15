"""Imunify360 `ip-list` response parsing (T16, V57, V69).

New — `noa-old` shipped `imunify.py` without a parser test on branch `MCP`. It is the second
of V57's two backends, so its verdict carries the same weight as CSF's, and every guard in the
module is a place a malformed row could otherwise become a wrong answer about whether an IP is
blocked.

Three behaviours are load-bearing and each gets an assertion rather than being inferred from a
happy path: `drop` outranks `white`, rows for other IPs are filtered out even though `--by-ip`
claims to have done it, and a malformed row is skipped rather than raised on.
"""

from __future__ import annotations

from typing import Any

from core.integrations.whm.imunify import (
    format_imunify_matches,
    imunify_entry_to_dict,
    parse_imunify_ip_list_response,
)

TARGET = "11.12.13.14"


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "ip": TARGET,
        "purpose": "white",
        "expiration": 1775225644,
        "comment": "NOA allowlist",
        "manual": True,
        "country": {"code": "US", "name": "United States"},
    }
    item.update(overrides)
    return item


def test_whitelisted_entry_is_found_and_fully_parsed() -> None:
    result = parse_imunify_ip_list_response({"items": [_item()], "counts": {"white": 1}}, TARGET)

    assert result.found is True
    assert result.verdict == "whitelisted"
    assert result.raw_counts == {"white": 1}
    (entry,) = result.entries
    assert entry.ip == TARGET
    assert entry.purpose == "white"
    assert entry.expiration == 1775225644
    assert entry.comment == "NOA allowlist"
    assert entry.manual is True
    assert entry.country_code == "US"
    assert entry.country_name == "United States"


def test_drop_beats_white_when_the_ip_is_in_both_lists() -> None:
    """Same precedence as CSF's block-beats-allow: report what the box is doing to the IP."""
    result = parse_imunify_ip_list_response(
        {"items": [_item(purpose="white"), _item(purpose="drop")]}, TARGET
    )

    assert result.verdict == "blacklisted"
    assert len(result.entries) == 2


def test_entries_for_other_ips_are_filtered_out() -> None:
    """`--by-ip` has been seen returning neighbours; a verdict off someone else's row is
    worse than no verdict."""
    result = parse_imunify_ip_list_response(
        {"items": [_item(ip="9.9.9.9", purpose="drop")]}, TARGET
    )

    assert result.found is False
    assert result.verdict == "not_found"
    assert result.entries == []


def test_absent_ip_reports_not_found_rather_than_whitelisted() -> None:
    assert parse_imunify_ip_list_response({"items": []}, TARGET).verdict == "not_found"


def test_missing_or_malformed_payload_degrades_to_not_found() -> None:
    """One bad row must ⊥ turn a dual-backend preflight into an error (V57)."""
    payload = {
        "items": [
            "not-a-dict",
            {"purpose": "drop"},  # no ip
            {"ip": TARGET},  # no purpose
            {"ip": TARGET, "purpose": "sideways"},  # purpose not in the enum
        ],
        "counts": "not-a-dict",
    }

    result = parse_imunify_ip_list_response(payload, TARGET)

    assert result.found is False
    assert result.verdict == "not_found"
    assert result.raw_counts is None


def test_optional_fields_of_the_wrong_type_become_none() -> None:
    result = parse_imunify_ip_list_response(
        {
            "items": [
                _item(
                    expiration="soon",
                    comment=None,
                    manual="yes",
                    country="United States",
                )
            ]
        },
        TARGET,
    )

    (entry,) = result.entries
    assert entry.expiration is None
    assert entry.comment is None
    assert entry.manual is False  # only a literal `True` counts
    assert entry.country_code is None
    assert entry.country_name is None


def test_items_absent_entirely_is_not_an_error() -> None:
    result = parse_imunify_ip_list_response({}, TARGET)

    assert result.found is False
    assert result.entries == []


def test_entry_serialises_for_receipts_and_audit() -> None:
    (entry,) = parse_imunify_ip_list_response({"items": [_item()]}, TARGET).entries

    assert imunify_entry_to_dict(entry) == {
        "ip": TARGET,
        "purpose": "white",
        "expiration": 1775225644,
        "comment": "NOA allowlist",
        "manual": True,
        "country_code": "US",
        "country_name": "United States",
    }


def test_matches_render_as_csf_shaped_evidence_lines() -> None:
    """A dual-backend preflight shows one list, ⊥ two stitched-together shapes."""
    result = parse_imunify_ip_list_response(
        {"items": [_item(purpose="drop", comment="brute force", expiration=1775225644)]},
        TARGET,
    )

    (line,) = format_imunify_matches(result.entries)

    assert line == f"Imunify blacklist: {TARGET} [expires: 1775225644] (brute force)"


def test_the_comment_is_the_last_field_on_the_line() -> None:
    """T25, §V.96: the one field whose text NOA may have written goes last.

    The surface that answers a model cuts NOA's own comment out of an evidence line, and csf
    gives a comment no closing boundary — so the cut runs to the end of the line
    (`noa_api.mcp_tools.whm_firewall.without_noa_comment_text`). Any structured field rendered
    *after* the comment would be taken with it, silently. Asserted as an ordering rather than as
    a whole line, because the claim is about position and would survive a wording change here.
    """
    result = parse_imunify_ip_list_response(
        {"items": [_item(comment="office", expiration=1775225644)]}, TARGET
    )

    (line,) = format_imunify_matches(result.entries)

    assert line.index("[expires:") < line.index("(office)")
    assert line.endswith("(office)")


def test_match_line_omits_absent_comment_and_expiry() -> None:
    result = parse_imunify_ip_list_response(
        {"items": [_item(comment=None, expiration=None)]}, TARGET
    )

    assert format_imunify_matches(result.entries) == [f"Imunify whitelist: {TARGET}"]
