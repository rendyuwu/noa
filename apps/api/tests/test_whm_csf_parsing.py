"""CSF target classification and `csf -g` parsing (T16, V54, V69).

Ported from `noa-old` branch `MCP` (`test_whm_csf_parsing.py`) with the HTML-path cases dropped
— that render path is not ported (see `core.integrations.whm.csf`) — and the target-kind case
widened to cover every branch, because `parse_csf_target` is the classifier V54 is enforced
with: the CHANGE tools reject anything that is not `ip`.

The grep fixtures are real `csf -g` output shapes, not invented ones. That matters: the
verdict is read from marker strings in human-facing text, so a fixture that does not look like
the box's output tests nothing.
"""

from __future__ import annotations

import pytest

from core.integrations.whm.csf import parse_csf_grep_output, parse_csf_target

# --- V54: target classification ---


@pytest.mark.parametrize(
    ("raw", "kind", "field", "value"),
    [
        ("1.2.3.4", "ip", "ip", "1.2.3.4"),
        ("1.2.3.0/24", "cidr", "cidr", "1.2.3.0/24"),
        # `strict=False`: csf accepts a host bit set, and normalising to the network is what
        # the operator meant.
        ("1.2.3.4/24", "cidr", "cidr", "1.2.3.0/24"),
        ("2001:db8::1", "ipv6", "ip", "2001:db8::1"),
        ("2001:db8::/32", "ipv6_cidr", "cidr", "2001:db8::/32"),
        ("app-01.example.com", "hostname", "hostname", "app-01.example.com"),
    ],
)
def test_parse_csf_target_classifies_ipv4_cidr_ipv6_hostname_unknown(
    raw: str, kind: str, field: str, value: str
) -> None:
    """Every kind the CHANGE tools branch on gets its own answer, ⊥ a guess (V54, C10)."""
    target = parse_csf_target(raw)

    assert target.kind == kind
    assert getattr(target, field) == value
    assert target.raw == raw


@pytest.mark.parametrize("raw", ["bad_target", "999.999.999.999", "-leading-dash.com", "nodot"])
def test_parse_csf_target_returns_unknown_rather_than_guessing(raw: str) -> None:
    """`unknown` is a verdict. `999.999.999.999` is label-legal but has no letter, so it is
    ⊥ classified as a hostname and never reaches DNS."""
    assert parse_csf_target(raw).kind == "unknown"


def test_parse_csf_target_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        parse_csf_target("   ")


def test_parse_csf_target_does_not_normalize_a_bare_address_to_a_cidr() -> None:
    """`1.2.3.4` stays `ip`. Turning it into `1.2.3.4/32` would erase the V54 distinction."""
    target = parse_csf_target("1.2.3.4")

    assert target.kind == "ip"
    assert target.cidr is None


# --- verdicts ---


def test_parse_csf_grep_output_detects_temporary_allow() -> None:
    output = """
Table  Chain            num   pkts bytes target     prot opt in     out     source               destination

filter ALLOWIN          1        0     0 ACCEPT     all  --  ens192 *       203.0.113.10         0.0.0.0/0

filter ALLOWOUT         1        0     0 ACCEPT     all  --  *      ens192  0.0.0.0/0            203.0.113.10


ip6tables:

Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in ip6tables

Temporary Allows: IP:203.0.113.10 Port: Dir:inout TTL:600 (NOA ttl allow test)
"""

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "allowlisted"
    assert any("Temporary Allows:" in match for match in parsed.matches)


def test_parse_csf_grep_output_detects_temporary_block() -> None:
    output = """
Table  Chain            num   pkts bytes target     prot opt in     out     source               destination

filter DENYIN           69       0     0 DROP       all  --  ens192 *       203.0.113.10         0.0.0.0/0


ip6tables:

Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in ip6tables

Temporary Blocks: IP:203.0.113.10 Port: Dir:in TTL:600 (NOA ttl deny test)
"""

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "blocked"
    assert any("Temporary Blocks:" in match for match in parsed.matches)


def test_parse_csf_grep_output_detects_not_found() -> None:
    output = """
Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in iptables


ip6tables:

Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in ip6tables
"""

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "not_found"
    assert parsed.matches == [
        "No matches found for 203.0.113.10 in iptables",
        "No matches found for 203.0.113.10 in ip6tables",
    ]


def test_blocked_beats_allowlisted_when_the_ip_is_in_both_lists() -> None:
    """T25 releases *and* allows in one action, so the intermediate state is real: an IP in
    `csf.deny` and `csf.allow` at once is, operationally, still blocked."""
    output = "Found 203.0.113.10 in /etc/csf/csf.allow\nFound 203.0.113.10 in /etc/csf/csf.deny\n"

    assert parse_csf_grep_output(output, target="203.0.113.10").verdict == "blocked"


def test_unrecognised_output_is_unknown_not_not_found() -> None:
    """A parse regression must ⊥ read as a clean IP — `not_found` needs csf to have said so."""
    output = "203.0.113.10 appears somewhere we do not have a marker for\n"

    assert parse_csf_grep_output(output, target="203.0.113.10").verdict == "unknown"


def test_matches_are_bounded_and_ansi_colour_is_stripped() -> None:
    """A busy box greps hundreds of log lines; the result is headed for an LLM context."""
    lines = ["Found \x1b[31m1.2.3.4\x1b[0m in /etc/csf/csf.deny"]
    lines += [f"Log entry {index} 1.2.3.4" for index in range(50)]

    parsed = parse_csf_grep_output("\n".join(lines), target="1.2.3.4")

    assert parsed.verdict == "blocked"
    assert len(parsed.matches) == 20
    assert "\x1b[" not in parsed.matches[0]


def test_parse_csf_grep_output_requires_a_target() -> None:
    with pytest.raises(ValueError):
        parse_csf_grep_output("anything", target="  ")
